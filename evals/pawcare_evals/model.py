"""Talks to the Realtime model in text mode, with the same instructions and tools as voice."""

import asyncio
import json
import time
from dataclasses import dataclass, field

import websockets

REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"
# Errors that would fail every scenario the same way, so the run stops instead.
ACCOUNT_ERRORS = {"invalid_api_key", "insufficient_quota", "model_not_found", "account_deactivated"}
EVENT_TIMEOUT_SECONDS = 60


class ProviderRejected(Exception):
    """OpenAI refused the connection, so no scenario can run. Stops the whole run."""


@dataclass
class ModelReply:
    text: str
    calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # From response.create to the first text or function call event.
    first_output_ms: float | None = None
    status: str = ""


class RealtimeTextModel:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.socket = None

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        try:
            self.socket = await websockets.connect(
                REALTIME_URL.format(model=self.model),
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                max_size=2**22,
            )
        except websockets.exceptions.InvalidStatus as error:
            status = error.response.status_code
            raise ProviderRejected(
                f"OpenAI refused the connection with HTTP {status}. Check the API key, the "
                "model name, and that the account has credit."
            ) from None
        await self.wait_for("session.created")
        await self.send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["text"],
                    "instructions": instructions,
                    "tools": tools,
                    "tool_choice": "auto",
                },
            }
        )
        await self.wait_for("session.updated")

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()

    async def send(self, event: dict) -> None:
        await self.socket.send(json.dumps(event))

    async def receive(self) -> dict:
        raw = await asyncio.wait_for(self.socket.recv(), timeout=EVENT_TIMEOUT_SECONDS)
        event = json.loads(raw)
        if event.get("type") == "error":
            code = event.get("error", {}).get("code")
            if code in ACCOUNT_ERRORS:
                raise ProviderRejected(
                    f"OpenAI rejected the request ({code}). Check the API key, the model name, "
                    "and that the account has credit."
                )
            raise RuntimeError(f"Realtime error: {code}")
        return event

    async def wait_for(self, event_type: str) -> dict:
        while True:
            event = await self.receive()
            if event.get("type") == event_type:
                return event

    async def add_message(self, role: str, text: str) -> None:
        await self.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )

    async def add_tool_output(self, call_id: str, output: dict) -> None:
        await self.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output),
                },
            }
        )

    async def respond(self) -> ModelReply:
        await self.send({"type": "response.create"})
        started = time.perf_counter()
        first = None
        text = []
        while True:
            event = await self.receive()
            kind = event.get("type", "")
            if first is None and kind in (
                "response.output_text.delta",
                "response.function_call_arguments.delta",
            ):
                first = (time.perf_counter() - started) * 1000
            if kind == "response.output_text.delta":
                text.append(event.get("delta", ""))
            if kind == "response.done":
                response = event.get("response", {})
                calls = [
                    {
                        "name": item["name"],
                        "call_id": item["call_id"],
                        "arguments": item.get("arguments", ""),
                    }
                    for item in response.get("output", [])
                    if item.get("type") == "function_call"
                ]
                return ModelReply(
                    text="".join(text).strip(),
                    calls=calls,
                    usage=response.get("usage") or {},
                    first_output_ms=first,
                    status=response.get("status", ""),
                )
