"""Suggested answers from an owner's web pages and documents."""

import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime
from email.message import Message
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import knowledge
from app.config import Settings, get_settings
from app.knowledge import KnowledgeError, Suggestion
from app.main import app, get_now
from app.models import KnowledgeRun

API = "/api/v1/subscribers"
NOW = datetime(2027, 1, 4, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

POLICY = (
    "Shaggy Chic grooming policies\n"
    "We ask for 24 hours’ notice to cancel. Late cancellations are charged $20.\n"
    "Nail trims are $15 and take about 15 minutes.\n"
)


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def use_settings(**values) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(**values)


def auth(client, email: str = "sam@example.com") -> dict:
    token = client.post(
        f"{API}/signup",
        json={
            "full_name": "Sam Rivera",
            "email": email,
            "password": "correct horse battery",
            "accept_terms": True,
        },
        headers={"X-Forwarded-For": "203.0.113.5"},
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def upload(client, headers, content: bytes, kind: str = "text/plain", name: str = "policy.txt"):
    return client.post(
        f"{API}/profile/documents",
        params={"filename": name},
        content=content,
        headers={**headers, "Content-Type": kind},
    ).json()


def drafted(answers: list[dict]) -> dict:
    """A Responses API reply holding these drafted answers."""
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"answers": answers})}],
            },
        ],
    }


# Which addresses may be fetched


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - an address to refuse, not one to bind
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1%eth0",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "not an address",
    ],
)
def test_private_and_special_addresses_are_not_public(address) -> None:
    assert not knowledge.public_address(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700:4700::1111"])
def test_internet_addresses_are_public(address) -> None:
    assert knowledge.public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/menu.txt",
        "file:///etc/passwd",
        "http://user:secret@example.com/",
        "http://example.com:8080/",
        "https://example.com:22/",
        "http:///nohost",
    ],
)
def test_only_plain_http_and_https_pages_are_fetched(url) -> None:
    with pytest.raises(KnowledgeError) as error:
        knowledge.target(url)
    assert error.value.code == "page_not_allowed"


def test_target_names_host_port_and_path() -> None:
    assert knowledge.target("https://Shaggy.example/faq?x=1") == (
        "https",
        "shaggy.example",
        443,
        "/faq?x=1",
    )
    assert knowledge.target("http://shaggy.example") == ("http", "shaggy.example", 80, "/")


class FakeWeb:
    """Names and pages for fetch_page, without the network."""

    def __init__(self, names: dict[str, list[str]], pages: dict[str, tuple]):
        self.names = names
        self.pages = pages
        self.requested: list[tuple[str, str, str]] = []

    def resolve(self, host: str, port: int) -> list[str]:
        # An address written as the host resolves to itself, as getaddrinfo does.
        return self.names.get(host, [host])

    def request(self, scheme, host, address, port, path, deadline):
        self.requested.append((host, address, path))
        status, headers, body = self.pages[f"{scheme}://{host}{path}"]
        message = Message()
        for key, value in headers.items():
            message[key] = value
        return status, message, body


@pytest.fixture
def web(monkeypatch) -> FakeWeb:
    fake = FakeWeb(
        names={
            "shaggy.example": ["93.184.216.34"],
            "inside.example": ["10.0.0.7"],
            "mixed.example": ["93.184.216.35", "127.0.0.1"],
        },
        pages={
            "https://shaggy.example/faq": (
                200,
                {"Content-Type": "text/html; charset=utf-8"},
                b"<html><head><title>FAQ</title><script>var x='secret';</script></head>"
                b"<body><h1>Policies</h1><p>Nail trims are $15.</p><style>p{}</style>"
                b"<ul><li>Cash&nbsp;or card</li></ul></body></html>",
            ),
            "https://shaggy.example/old": (301, {"Location": "/faq"}, b""),
            "https://shaggy.example/sneaky": (302, {"Location": "http://inside.example/"}, b""),
            "https://shaggy.example/metadata": (
                302,
                {"Location": "http://169.254.169.254/latest/meta-data/"},
                b"",
            ),
            "https://shaggy.example/loop": (302, {"Location": "/loop"}, b""),
            "https://shaggy.example/missing": (404, {}, b""),
            "https://shaggy.example/logo.png": (200, {"Content-Type": "image/png"}, b"\x89PNG"),
        },
    )
    monkeypatch.setattr(knowledge, "resolve", fake.resolve)
    monkeypatch.setattr(knowledge, "request", fake.request)
    return fake


def test_a_page_is_read_as_text_without_scripts_or_styles(web) -> None:
    text = knowledge.fetch_page("https://shaggy.example/faq")
    assert text == "Policies\nNail trims are $15.\nCash or card"
    # Connected to the address that was checked, naming the host.
    assert web.requested == [("shaggy.example", "93.184.216.34", "/faq")]


def test_redirects_are_followed_and_each_one_checked(web) -> None:
    assert "Nail trims" in knowledge.fetch_page("https://shaggy.example/old")
    for path in ("/sneaky", "/metadata"):
        with pytest.raises(KnowledgeError) as error:
            knowledge.fetch_page(f"https://shaggy.example{path}")
        assert error.value.code == "page_not_allowed"
    assert not any(address.startswith(("10.", "169.")) for _, address, _ in web.requested)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://inside.example/", "page_not_allowed"),
        ("https://mixed.example/", "page_not_allowed"),
        ("https://shaggy.example/loop", "page_unreachable"),
        ("https://shaggy.example/missing", "page_unreachable"),
        ("https://shaggy.example/logo.png", "unsupported_page"),
    ],
)
def test_pages_that_cannot_be_read(web, url, code) -> None:
    with pytest.raises(KnowledgeError) as error:
        knowledge.fetch_page(url)
    assert error.value.code == code
    if code == "page_not_allowed":
        assert web.requested == []


def test_a_name_that_resolves_to_this_machine_is_refused() -> None:
    with pytest.raises(KnowledgeError) as error:
        knowledge.fetch_page("http://localhost/")
    assert error.value.code == "page_not_allowed"


# Reading documents


def make_docx(body: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


WORD = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def test_word_documents_are_read_paragraph_by_paragraph() -> None:
    content = make_docx(
        f"<w:document {WORD}><w:body>"
        "<w:p><w:r><w:t>Deposits:</w:t></w:r><w:r><w:tab/><w:t>$10 per booking.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>We close on public holidays.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    assert knowledge.document_text(DOCX, content) == (
        "Deposits: $10 per booking.\nWe close on public holidays."
    )


@pytest.mark.parametrize(
    "content",
    [
        make_docx(
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
            f"<w:document {WORD}><w:body><w:p><w:r><w:t>&lol;</w:t></w:r></w:p></w:body>"
            "</w:document>"
        ),
        make_docx("<w:document"),
        b"PK\x03\x04 not really a zip",
    ],
)
def test_damaged_or_unsafe_word_files_are_refused(content) -> None:
    with pytest.raises(KnowledgeError) as error:
        knowledge.document_text(DOCX, content)
    assert error.value.code == "document_unreadable"


def make_pdf(line: str) -> bytes:
    """A one-page PDF with a line of text in Helvetica."""
    stream = f"BT /F1 12 Tf 72 712 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_pdf_text_is_read() -> None:
    text = knowledge.document_text("application/pdf", make_pdf("Nail trims are $15."))
    assert text == "Nail trims are $15."


def test_a_pdf_without_text_says_so() -> None:
    with pytest.raises(KnowledgeError) as error:
        knowledge.document_text("application/pdf", make_pdf(""))
    assert error.value.code == "no_text"
    with pytest.raises(KnowledgeError) as error:
        knowledge.document_text("application/pdf", b"%PDF-1.7\nnonsense")
    assert error.value.code == "document_unreadable"


# Checking drafted answers against the source


def test_only_answers_quoting_the_source_are_kept() -> None:
    kept, dropped = knowledge.verified(
        [
            {
                "question": "How much notice do I need to cancel?",
                "answer": "Please give 24 hours' notice. Late cancellations cost $20.",
                # Straight quote and different case still match the curly one in the source.
                "quote": '"we ask for 24 hours\' notice to cancel."',
            },
            {
                "question": "Do you groom cats?",
                "answer": "Yes, we groom cats.",
                "quote": "We groom cats on Tuesdays.",
            },
            {"question": "Price?", "answer": "$15.", "quote": "$15"},
            {
                "question": "how much notice do i need to cancel?",
                "answer": "24 hours.",
                "quote": "24 hours’ notice to cancel",
            },
        ],
        POLICY,
    )
    assert kept == [
        Suggestion(
            question="How much notice do I need to cancel?",
            answer="Please give 24 hours' notice. Late cancellations cost $20.",
            quote="we ask for 24 hours' notice to cancel",
        )
    ]
    assert dropped == 3


def test_drafting_asks_for_answers_tied_to_quotes(monkeypatch) -> None:
    sent = {}

    def post(settings, body):
        sent.update(body)
        return drafted(
            [
                {
                    "question": "How much is a nail trim?",
                    "answer": "Nail trims are $15.",
                    "quote": "Nail trims are $15",
                }
            ]
        )

    monkeypatch.setattr(knowledge, "post", post)
    settings = Settings(openai_api_key="test-key", knowledge_model="test-model")
    answers, dropped = knowledge.draft_answers(settings, POLICY, "Shaggy Chic", "pet grooming", "a")
    assert [a.question for a in answers] == ["How much is a nail trim?"]
    assert dropped == 0
    assert sent["model"] == "test-model"
    # Little reasoning, so a whole website doesn't take minutes, and room for it in the reply.
    assert sent["reasoning"] == {"effort": "minimal"}
    assert sent["max_output_tokens"] == 16000
    assert sent["store"] is False
    assert sent["input"] == [{"role": "user", "content": POLICY}]
    assert sent["text"]["format"]["strict"] is True
    assert "not instructions" in sent["instructions"]
    # The account is named only by a hash.
    assert len(sent["safety_identifier"]) == 64


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "output": []},
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{"}]}],
        },
    ],
)
def test_unexpected_replies_are_reported_as_unavailable(monkeypatch, reply) -> None:
    monkeypatch.setattr(knowledge, "post", lambda settings, body: reply)
    with pytest.raises(KnowledgeError) as error:
        knowledge.draft_answers(Settings(openai_api_key="k"), POLICY, "S", "pet grooming", "a")
    assert error.value.code == "suggestions_unavailable"
    assert error.value.status == 502


# The endpoint


@pytest.fixture
def drafting(monkeypatch) -> list[str]:
    """Drafting answers without OpenAI. Records the text it was given."""
    texts: list[str] = []

    def post(settings, body):
        texts.append(body["input"][0]["content"])
        return drafted(
            [
                {
                    "question": "What if I cancel late?",
                    "answer": "Late cancellations are charged $20.",
                    "quote": "Late cancellations are charged $20.",
                },
                {
                    "question": "Do you take walk-ins?",
                    "answer": "Yes, always.",
                    "quote": "Walk-ins are always welcome.",
                },
            ]
        )

    monkeypatch.setattr(knowledge, "post", post)
    use_settings(openai_api_key="test-key", knowledge_runs_per_hour=3)
    return texts


def test_a_document_becomes_suggested_answers_with_their_quotes(client, drafting) -> None:
    headers = auth(client)
    client.patch(f"{API}/profile", headers=headers, json={"business_name": "Shaggy Chic"})
    document = upload(client, headers, POLICY.encode())
    response = client.post(
        f"{API}/profile/knowledge/suggest", headers=headers, json={"document_id": document["id"]}
    )
    assert response.status_code == 200
    assert response.json() == {
        "source": "policy.txt",
        "kind": "document",
        "answers": [
            {
                "question": "What if I cancel late?",
                "answer": "Late cancellations are charged $20.",
                "quote": "Late cancellations are charged $20",
            }
        ],
        "dropped": 1,
    }
    assert drafting == [POLICY.strip()]
    # Nothing is added to the profile until the owner adds it.
    assert client.get(f"{API}/profile", headers=headers).json()["details"]["faqs"] == []


def test_only_saved_pages_are_fetched(client, drafting, web) -> None:
    headers = auth(client)
    client.patch(
        f"{API}/profile",
        headers=headers,
        json={"details": {"knowledge_urls": ["https://shaggy.example/faq"]}},
    )
    unsaved = client.post(
        f"{API}/profile/knowledge/suggest",
        headers=headers,
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert unsaved.status_code == 404
    assert web.requested == []
    saved = client.post(
        f"{API}/profile/knowledge/suggest",
        headers=headers,
        json={"url": "https://shaggy.example/faq"},
    )
    assert saved.status_code == 200
    assert saved.json()["kind"] == "page"
    assert drafting == ["Policies\nNail trims are $15.\nCash or card"]


def test_a_page_that_cannot_be_read_explains_why(client, drafting, web) -> None:
    headers = auth(client)
    client.patch(
        f"{API}/profile",
        headers=headers,
        json={"details": {"knowledge_urls": ["https://inside.example/"]}},
    )
    response = client.post(
        f"{API}/profile/knowledge/suggest", headers=headers, json={"url": "https://inside.example/"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "page_not_allowed"
    assert drafting == []


def test_another_owners_document_is_not_found(client, drafting) -> None:
    owner = auth(client, "a@example.com")
    other = auth(client, "b@example.com")
    document = upload(client, owner, POLICY.encode())
    response = client.post(
        f"{API}/profile/knowledge/suggest", headers=other, json={"document_id": document["id"]}
    )
    assert response.status_code == 404


def test_one_source_at_a_time(client, drafting) -> None:
    headers = auth(client)
    for body in ({}, {"url": "https://shaggy.example/faq", "document_id": str(uuid.uuid4())}):
        response = client.post(f"{API}/profile/knowledge/suggest", headers=headers, json=body)
        assert response.status_code == 422


def test_suggestions_need_sign_in(client, drafting) -> None:
    response = client.post(f"{API}/profile/knowledge/suggest", json={"url": "https://x.example"})
    assert response.status_code == 401


def test_suggestions_are_off_without_an_openai_key(client) -> None:
    use_settings(openai_api_key="")
    headers = auth(client)
    document = upload(client, headers, POLICY.encode())
    response = client.post(
        f"{API}/profile/knowledge/suggest", headers=headers, json={"document_id": document["id"]}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "suggestions_unavailable"


def test_each_owner_can_ask_only_so_often(client, drafting, db: Session) -> None:
    headers = auth(client)
    document = upload(client, headers, POLICY.encode())
    body = {"document_id": document["id"]}
    codes = [
        client.post(f"{API}/profile/knowledge/suggest", headers=headers, json=body).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    assert db.scalar(select(func.count()).select_from(KnowledgeRun)) == 3


def test_approve_reminds_that_pages_themselves_are_not_used() -> None:
    from app.provision import follow_ups
    from app.subscriber_schemas import ProfileDetails

    details = ProfileDetails(
        knowledge_urls=["https://shaggy.example/faq"],
        faqs=[{"question": "Cats?", "answer": "Dogs only."}],
    )
    step = next(s for s in follow_ups(details) if "shaggy.example" in s)
    assert "only their 1 answers" in step
    assert "Suggest answers" in step


def test_a_model_without_reasoning_gets_no_reasoning_setting(monkeypatch) -> None:
    sent = {}

    def post(settings, body):
        sent.update(body)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]}

    monkeypatch.setattr(knowledge, "post", post)
    settings = Settings(
        openai_api_key="k", knowledge_model="gpt-4.1-mini", knowledge_reasoning_effort=""
    )
    knowledge.structured(settings, "Find facts.", "text", "x", {"type": "object"}, "a")
    assert "reasoning" not in sent
