"""Fish Audio as the voice of Vapi calls.

Vapi's "custom voice" sends each sentence the assistant says to this backend, and plays back
whatever raw audio comes back: 16-bit signed, mono, little-endian PCM at the sample rate it
asked for. This module asks Fish Audio for the same and streams it straight through, so the
caller hears the first words while the rest is still being made.

Fish offers 8, 16, 24, 32 and 44.1 kHz. Vapi may also ask for 22.05 kHz; then Fish makes
44.1 kHz and every other sample is kept.
"""

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("pawcare.voice.fish")

TTS_URL = "https://api.fish.audio/v1/tts"
FISH_RATES = {8000, 16000, 24000, 32000, 44100}
VAPI_RATES = {8000, 16000, 22050, 24000, 44100}
MAX_TEXT = 2000
READ_SIZE = 4096


class FishError(Exception):
    """Fish could not make the audio. Holds a status, never the response body."""


class AudioStream(Protocol):
    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


def fish_rate(sample_rate: int) -> tuple[int, int]:
    """The rate to ask Fish for, and how many samples to step over to reach the wanted rate."""
    if sample_rate in FISH_RATES:
        return sample_rate, 1
    if sample_rate == 22050:
        return 44100, 2
    raise ValueError(f"unsupported sample rate {sample_rate}")


def open_fish(settings: Settings, text: str, sample_rate: int) -> AudioStream:
    """Starts the request. Raises FishError before any audio if Fish refuses."""
    body = {
        "text": text,
        "reference_id": settings.fish_voice_id,
        "format": "pcm",
        "sample_rate": sample_rate,
        "latency": settings.fish_latency,
        "normalize": True,
    }
    request = urllib.request.Request(  # noqa: S310 - fixed https URL
        TTS_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.fish_api_key}",
            "Content-Type": "application/json",
            "model": settings.fish_model,
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=15)  # noqa: S310
    except urllib.error.HTTPError as error:
        raise FishError(f"Fish returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise FishError("Fish could not be reached") from None


def pcm_chunks(stream: AudioStream, step: int) -> Iterator[bytes]:
    """Streams 16-bit samples, keeping one in every `step`. Never splits a sample."""
    leftover = b""
    position = 0  # index of the next sample in the whole stream
    try:
        while True:
            chunk = stream.read(READ_SIZE)
            if not chunk:
                break
            data = leftover + chunk
            usable = len(data) - len(data) % 2
            leftover = data[usable:]
            samples = data[:usable]
            if step == 1:
                if samples:
                    yield samples
                continue
            kept = bytearray()
            for offset in range(0, usable, 2):
                if position % step == 0:
                    kept += samples[offset : offset + 2]
                position += 1
            if kept:
                yield bytes(kept)
    finally:
        stream.close()
