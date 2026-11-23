"""Suggested answers from the owner's own web pages and documents, for them to review.

One request reads one source, so each stays within a page fetch and one drafting call. The
suggestions are returned, not stored: the ones the owner adds become ordinary FAQs in
their profile.
"""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import knowledge
from app.errors import ApiError
from app.knowledge import KnowledgeError
from app.models import KnowledgeRun, ProfileDocument
from app.subscriber_api import Account, Config, DbSession, Now, load_profile
from app.subscriber_schemas import BUSINESS_TYPE_LABELS, ProfileDetails, Strict

router = APIRouter(prefix="/api/v1/subscribers", tags=["subscribers"])


class SuggestRequest(Strict):
    """Either one of the profile's saved web pages, or one of its documents."""

    url: str | None = Field(default=None, max_length=2000)
    document_id: uuid.UUID | None = None


class SuggestedAnswer(BaseModel):
    question: str
    answer: str
    # The words in the source the answer rests on, for the owner to check.
    quote: str


class Suggestions(BaseModel):
    source: str
    kind: Literal["page", "document"]
    answers: list[SuggestedAnswer]
    # Drafted answers left out because their quote isn't in the source.
    dropped: int


@router.post("/profile/knowledge/suggest", response_model=Suggestions)
def suggest(
    body: SuggestRequest, context: Account, db: DbSession, now: Now, settings: Config
) -> Suggestions:
    if (body.url is None) == (body.document_id is None):
        raise ApiError(422, "invalid_request", "Choose one web page or one document.")
    if not settings.openai_api_key:
        raise ApiError(503, "suggestions_unavailable", "Suggested answers aren't available yet.")
    account = context[0]
    profile = load_profile(db, account.id)
    details = ProfileDetails.from_stored(profile.details)

    # Only what the owner gave: a saved page address, or their own document.
    document = None
    if body.url is not None:
        if body.url not in details.knowledge_urls:
            raise ApiError(404, "source_not_found", "Save that web page in your profile first.")
        source, kind = body.url, "page"
    else:
        document = db.scalar(
            select(ProfileDocument).where(
                ProfileDocument.id == body.document_id,
                ProfileDocument.profile_id == profile.id,
            )
        )
        if document is None:
            raise ApiError(404, "document_not_found", "No document with that id.")
        source, kind = document.filename, "document"

    recent = db.scalar(
        select(func.count())
        .select_from(KnowledgeRun)
        .where(
            KnowledgeRun.account_id == account.id,
            KnowledgeRun.created_at > now - timedelta(hours=1),
        )
    )
    if recent >= settings.knowledge_runs_per_hour:
        raise ApiError(
            429, "too_many_requests", "That's a lot of suggestions for now. Try again in an hour."
        )
    # Counted before the work, so failures count too.
    db.add(KnowledgeRun(account_id=account.id, created_at=now))
    db.commit()

    try:
        if document is not None:
            text = knowledge.document_text(document.content_type, document.content)
        else:
            text = knowledge.fetch_page(source)
        if not text:
            raise KnowledgeError("no_text", "No text was found there.")
        answers, dropped = knowledge.draft_answers(
            settings,
            text,
            profile.business_name or "the business",
            business_kind(profile.business_type),
            str(account.id),
        )
    except KnowledgeError as error:
        raise ApiError(error.status, error.code, error.message) from None
    return Suggestions(
        source=source,
        kind=kind,
        answers=[SuggestedAnswer(**vars(a)) for a in answers],
        dropped=dropped,
    )


def business_kind(business_type: str | None) -> str:
    return BUSINESS_TYPE_LABELS.get(business_type or "", "local service").lower()
