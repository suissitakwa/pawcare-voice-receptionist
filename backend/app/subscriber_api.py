"""Subscriber sign-up, sign-in, and the business profile that configures a receptionist.

A profile is collected and stored here. It does not yet drive live calls: website import,
document reading, calendar connection, and the phone line are later steps.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import get_now
from app.db import get_db
from app.errors import ApiError
from app.models import BusinessProfile, ProfileDocument, SubscriberAccount, SubscriberSession
from app.staff import DUMMY_HASH, LOCKOUT, MAX_FAILED_ATTEMPTS, SESSION_LIFETIME, hasher
from app.subscriber_schemas import (
    BUSINESS_TYPE_LABELS,
    CALENDAR_LABELS,
    VOICES,
    AccountPublic,
    DocumentPublic,
    Option,
    ProfileDetails,
    ProfileOptions,
    ProfilePublic,
    ProfileUpdate,
    SessionResult,
    SignInRequest,
    SignUpRequest,
    VoiceOption,
)

router = APIRouter(prefix="/api/v1/subscribers", tags=["subscribers"])

DbSession = Annotated[Session, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENTS = 10
DOCUMENT_TYPES = {
    "application/pdf": b"%PDF-",
    # A .docx file is a zip archive.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "text/plain": None,
    "text/markdown": None,
}
SIGNUPS_PER_CLIENT_PER_HOUR = 5


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "That email and password do not match.")


def client_hash(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    return sha256(f"subscriber-signup:{address}")


def new_session(db: Session, account: SubscriberAccount, now: datetime) -> SessionResult:
    token = secrets.token_urlsafe(32)
    record = SubscriberSession(
        account_id=account.id, token_hash=sha256(token), expires_at=now + SESSION_LIFETIME
    )
    db.add(record)
    db.flush()
    return SessionResult(
        token=token,
        expires_at=record.expires_at,
        account=AccountPublic(email=account.email, full_name=account.full_name),
    )


def current_account(
    db: DbSession, now: Now, authorization: Annotated[str | None, Header()] = None
) -> tuple[SubscriberAccount, SubscriberSession]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    found = db.execute(
        select(SubscriberSession, SubscriberAccount)
        .join(SubscriberAccount, SubscriberAccount.id == SubscriberSession.account_id)
        .where(
            SubscriberSession.token_hash == sha256(token),
            SubscriberSession.revoked_at.is_(None),
            SubscriberSession.expires_at > now,
        )
    ).first()
    if found is None:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    return found[1], found[0]


Account = Annotated[tuple[SubscriberAccount, SubscriberSession], Depends(current_account)]


def load_profile(db: Session, account_id: uuid.UUID) -> BusinessProfile:
    profile = db.scalar(select(BusinessProfile).where(BusinessProfile.account_id == account_id))
    if profile is None:
        # Every account gets its profile at sign-up; this only covers a missing row.
        profile = BusinessProfile(account_id=account_id, details={})
        db.add(profile)
        db.flush()
    return profile


def missing_fields(profile: BusinessProfile, details: ProfileDetails, documents: int) -> list[str]:
    """What still has to be filled in before the profile can be submitted."""
    checks = {
        "business_name": bool(profile.business_name),
        "business_type": bool(profile.business_type),
        "business_phone": bool(details.business_phone),
        "calendar_provider": details.calendar_provider is not None,
        "knowledge": bool(details.knowledge_urls or details.faqs or documents),
        "voice": bool(details.voice),
        "ai_disclosure_ack": details.ai_disclosure_ack,
    }
    return [name for name, done in checks.items() if not done]


def to_public(db: Session, profile: BusinessProfile) -> ProfilePublic:
    details = ProfileDetails.from_stored(profile.details)
    documents = db.scalars(
        select(ProfileDocument)
        .where(ProfileDocument.profile_id == profile.id)
        .order_by(ProfileDocument.created_at)
    ).all()
    return ProfilePublic(
        status=profile.status,
        business_name=profile.business_name,
        business_type=profile.business_type,
        details=details,
        documents=[DocumentPublic.model_validate(d) for d in documents],
        missing=missing_fields(profile, details, len(documents)),
        submitted_at=profile.submitted_at,
        updated_at=profile.updated_at,
    )


@router.get("/options", response_model=ProfileOptions)
def options() -> ProfileOptions:
    def listed(labels: dict[str, str]) -> list[Option]:
        return [Option(value=k, label=v) for k, v in labels.items()]

    return ProfileOptions(
        business_types=listed(BUSINESS_TYPE_LABELS),
        voices=[VoiceOption(value=k, label=v[0], sounds=v[1]) for k, v in VOICES.items()],
        calendar_providers=listed(CALENDAR_LABELS),
        document_types=list(DOCUMENT_TYPES),
        max_document_bytes=MAX_DOCUMENT_BYTES,
    )


@router.post("/signup", response_model=SessionResult, status_code=201)
def sign_up(body: SignUpRequest, request: Request, db: DbSession, now: Now) -> SessionResult:
    origin = client_hash(request)
    recent = db.scalar(
        select(func.count())
        .select_from(SubscriberAccount)
        .where(
            SubscriberAccount.client_hash == origin,
            SubscriberAccount.created_at > now - timedelta(hours=1),
        )
    )
    if recent >= SIGNUPS_PER_CLIENT_PER_HOUR:
        raise ApiError(429, "too_many_requests", "Too many sign-ups. Please try again later.")
    account = SubscriberAccount(
        email=body.email,
        full_name=body.full_name,
        password_hash=hasher.hash(body.password),
        terms_accepted_at=now,
        client_hash=origin,
        created_at=now,
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409, "email_taken", "An account with that email already exists. Please sign in."
        ) from None
    db.add(BusinessProfile(account_id=account.id, details={}))
    result = new_session(db, account, now)
    db.commit()
    return result


@router.post("/login", response_model=SessionResult)
def sign_in(body: SignInRequest, db: DbSession, now: Now) -> SessionResult:
    account = db.scalar(
        select(SubscriberAccount)
        .where(SubscriberAccount.email == body.email.strip().lower())
        .with_for_update()
    )
    if account is None:
        # Takes as long as a wrong password, so timing does not reveal which emails exist.
        try:
            hasher.verify(DUMMY_HASH, body.password)
        except VerificationError:
            pass
        raise invalid_credentials()
    if account.locked_until is not None and account.locked_until > now:
        raise ApiError(
            429, "too_many_attempts", "Too many failed sign-ins. Please try again later."
        )
    try:
        hasher.verify(account.password_hash, body.password)
    except (VerificationError, InvalidHashError):
        account.failed_attempts += 1
        if account.failed_attempts >= MAX_FAILED_ATTEMPTS:
            account.failed_attempts = 0
            account.locked_until = now + LOCKOUT
        db.commit()
        raise invalid_credentials() from None
    account.failed_attempts = 0
    account.locked_until = None
    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(body.password)
    result = new_session(db, account, now)
    db.commit()
    return result


@router.post("/logout", status_code=204)
def sign_out(context: Account, db: DbSession, now: Now) -> Response:
    context[1].revoked_at = now
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=AccountPublic)
def me(context: Account) -> AccountPublic:
    return AccountPublic(email=context[0].email, full_name=context[0].full_name)


@router.get("/profile", response_model=ProfilePublic)
def get_profile(context: Account, db: DbSession) -> ProfilePublic:
    profile = load_profile(db, context[0].id)
    db.commit()
    return to_public(db, profile)


@router.patch("/profile", response_model=ProfilePublic)
def update_profile(body: ProfileUpdate, context: Account, db: DbSession) -> ProfilePublic:
    """Saves part of the profile. Top-level detail fields sent replace the stored ones."""
    profile = load_profile(db, context[0].id)
    sent = body.model_fields_set
    if "business_name" in sent:
        profile.business_name = body.business_name
    if "business_type" in sent:
        profile.business_type = body.business_type
    if body.details is not None:
        merged = {
            **ProfileDetails.from_stored(profile.details).model_dump(mode="json"),
            **body.details,
        }
        try:
            details = ProfileDetails.model_validate(merged)
        except ValidationError as error:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in error.errors()})
            raise ApiError(
                422, "invalid_request", "Check these fields: " + ", ".join(fields) + "."
            ) from None
        profile.details = details.model_dump(mode="json")
    if profile.status == "submitted" and missing_fields(
        profile, ProfileDetails.from_stored(profile.details), count_documents(db, profile)
    ):
        # Editing a submitted profile is allowed, but it cannot become incomplete.
        db.rollback()
        raise ApiError(422, "profile_incomplete", "A submitted profile must stay complete.")
    db.commit()
    return to_public(db, profile)


def count_documents(db: Session, profile: BusinessProfile) -> int:
    return db.scalar(
        select(func.count())
        .select_from(ProfileDocument)
        .where(ProfileDocument.profile_id == profile.id)
    )


@router.post("/profile/submit", response_model=ProfilePublic)
def submit_profile(context: Account, db: DbSession, now: Now) -> ProfilePublic:
    profile = load_profile(db, context[0].id)
    details = ProfileDetails.from_stored(profile.details)
    missing = missing_fields(profile, details, count_documents(db, profile))
    if missing:
        raise ApiError(
            422, "profile_incomplete", "Fill in these sections first: " + ", ".join(missing) + "."
        )
    if profile.status != "submitted":
        profile.status = "submitted"
        profile.submitted_at = now
    db.commit()
    return to_public(db, profile)


def clean_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable())
    return name[:120]


@router.post("/profile/documents", response_model=DocumentPublic, status_code=201)
async def upload_document(
    request: Request,
    context: Account,
    db: DbSession,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> DocumentPublic:
    """Stores a document for the receptionist to learn from. The body is the raw file."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in DOCUMENT_TYPES:
        raise ApiError(415, "unsupported_document", "Upload a PDF, Word (.docx), or text file.")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_DOCUMENT_BYTES:
        raise ApiError(413, "document_too_large", "Documents can be at most 5 MB.")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ApiError(413, "document_too_large", "Documents can be at most 5 MB.")
    if not content:
        raise ApiError(422, "empty_document", "That file is empty.")
    signature = DOCUMENT_TYPES[content_type]
    if signature is not None and not content.startswith(signature):
        raise ApiError(415, "unsupported_document", "The file does not match its type.")
    if signature is None:
        try:
            bytes(content).decode("utf-8")
        except UnicodeDecodeError:
            raise ApiError(415, "unsupported_document", "Text files must be UTF-8.") from None
    name = clean_filename(filename)
    if not name:
        raise ApiError(422, "invalid_request", "Check these fields: filename.")
    profile = load_profile(db, context[0].id)
    if count_documents(db, profile) >= MAX_DOCUMENTS:
        raise ApiError(409, "too_many_documents", "A profile can hold at most 10 documents.")
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(ProfileDocument).where(
            ProfileDocument.profile_id == profile.id, ProfileDocument.sha256 == digest
        )
    )
    if existing is not None:
        # The same file twice is stored once.
        return DocumentPublic.model_validate(existing)
    document = ProfileDocument(
        profile_id=profile.id,
        filename=name,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        content=bytes(content),
    )
    db.add(document)
    db.commit()
    return DocumentPublic.model_validate(document)


@router.delete("/profile/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, context: Account, db: DbSession) -> Response:
    profile = load_profile(db, context[0].id)
    document = db.scalar(
        select(ProfileDocument).where(
            ProfileDocument.id == document_id, ProfileDocument.profile_id == profile.id
        )
    )
    if document is None:
        raise ApiError(404, "document_not_found", "No document with that id.")
    db.delete(document)
    db.flush()
    if profile.status == "submitted" and missing_fields(
        profile, ProfileDetails.from_stored(profile.details), count_documents(db, profile)
    ):
        db.rollback()
        raise ApiError(422, "profile_incomplete", "A submitted profile must stay complete.")
    db.commit()
    return Response(status_code=204)
