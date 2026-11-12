"""Fish Audio as Vapi's custom voice, with Fish replaced by a fake audio stream."""

import struct
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.fish_voice import FishError, fish_rate, pcm_chunks
from app.main import app
from app.vapi_api import get_fish_opener
from tests.test_vapi import AUTH, SECRET, URL, assistant_request, vapi  # noqa: F401 - fixture

VOICE_URL = "/api/v1/vapi/voice"


def fish_settings(**overrides) -> Settings:
    values = {
        "vapi_enabled": True,
        "vapi_secret": SECRET,
        "vapi_voice_provider": "fish",
        "fish_api_key": "fish-test-key-not-real",
        "fish_voice_id": "voice-123",
    }
    values.update(overrides)
    return Settings(**values)


class FakeStream:
    def __init__(self, data: bytes, size: int = 7) -> None:
        self.data = data
        self.size = size  # odd on purpose, so samples are split across reads
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk, self.data = self.data[: self.size], self.data[self.size :]
        return chunk

    def close(self) -> None:
        self.closed = True


def samples(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


@pytest.fixture
def fish() -> Iterator[dict]:
    state = {"settings": fish_settings(), "requests": [], "fail": False, "audio": samples(1, 2)}

    def opener(settings: Settings, text: str, rate: int):
        state["requests"].append((text, rate))
        if state["fail"]:
            raise FishError("Fish returned HTTP 402")
        return FakeStream(state["audio"])

    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_fish_opener] = lambda: opener
    yield state
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_fish_opener, None)


def voice_request(client: TestClient, text: str = "Hi there!", rate: int = 24000, headers=None):
    message = {"type": "voice-request", "text": text, "sampleRate": rate, "timestamp": 1}
    return client.post(
        VOICE_URL, json={"message": message}, headers=AUTH if headers is None else headers
    )


def test_the_assistant_uses_the_backend_voice_when_fish_is_chosen(client: TestClient, fish) -> None:
    assistant = assistant_request(client)["assistant"]
    voice = assistant["voice"]
    assert voice["provider"] == "custom-voice"
    assert voice["server"]["url"].endswith(VOICE_URL)
    assert voice["server"]["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_audio_streams_through_at_the_requested_rate(client: TestClient, fish) -> None:
    fish["audio"] = samples(*range(100))
    response = voice_request(client)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == samples(*range(100))
    assert fish["requests"] == [("Hi there!", 24000)]


def test_22050_is_made_from_44100(client: TestClient, fish) -> None:
    fish["audio"] = samples(*range(10))
    response = voice_request(client, rate=22050)
    assert fish["requests"][0][1] == 44100
    assert response.content == samples(0, 2, 4, 6, 8)


def test_a_fish_failure_is_an_error_not_silence(client: TestClient, fish) -> None:
    fish["fail"] = True
    response = voice_request(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "voice_unavailable"


def test_the_voice_needs_the_secret(client: TestClient, fish) -> None:
    assert voice_request(client, headers={}).status_code == 401
    assert fish["requests"] == []


def test_the_voice_is_hidden_unless_fish_is_chosen(client: TestClient, fish) -> None:
    fish["settings"] = fish_settings(vapi_voice_provider="11labs")
    assert voice_request(client).status_code == 404


def test_missing_fish_settings_are_reported(client: TestClient, fish) -> None:
    fish["settings"] = fish_settings(fish_voice_id="")
    assert voice_request(client).status_code == 503


@pytest.mark.parametrize(("text", "rate"), [("", 24000), ("x" * 2001, 24000), ("Hello", 12345)])
def test_bad_requests_are_refused(client: TestClient, fish, text: str, rate: int) -> None:
    assert voice_request(client, text=text, rate=rate).status_code == 400
    assert fish["requests"] == []


def test_samples_are_never_split_and_the_stream_is_closed() -> None:
    stream = FakeStream(samples(1, 2, 3) + b"\x01", size=3)
    assert b"".join(pcm_chunks(stream, 1)) == samples(1, 2, 3)
    assert stream.closed


def test_fish_rates() -> None:
    assert fish_rate(24000) == (24000, 1)
    assert fish_rate(22050) == (44100, 2)
    with pytest.raises(ValueError):
        fish_rate(11025)
