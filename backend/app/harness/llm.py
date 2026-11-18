"""LLM adapter.

`ClaudeLLM` talks to the Claude API (Anthropic Python SDK, streaming, adaptive
thinking, server-side refusal fallbacks). `StubLLM` / `ScriptedLLM` let the
whole harness run offline for UI work and tests.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

log = logging.getLogger(__name__)

TextCallback = Callable[[str], Awaitable[None]]


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]  # assistant content blocks, ready to append to history unchanged
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    stop_details: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLM(Protocol):
    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str = "medium",
        on_text: TextCallback | None = None,
    ) -> LLMResponse: ...


class ClaudeLLM:
    """Claude via the Anthropic SDK.

    * Streams every request (long agent turns + large max_tokens).
    * Adaptive thinking is always on for Claude Opus 5.5; depth is set with `effort`.
    * `fallbacks="default"` re-runs a safety-declined request on Anthropic's recommended
      fallback model server-side instead of failing the turn.
    * Tool inputs stream eagerly; they are validated by pydantic before any tool runs.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None, max_tokens: int = 32000):
        self.client = client or anthropic.AsyncAnthropic()
        self.max_tokens = max_tokens

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str = "medium",
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        params: dict[str, Any] = dict(
            model=model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},  # stable system prompt + tools are cached
            betas=[self.FALLBACK_BETA],
            fallbacks="default",
        )
        if tools:
            params["tools"] = [{**t, "eager_input_streaming": True} for t in tools]

        json_retries = 0
        while True:
            try:
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        if event.type == "text" and on_text is not None:
                            await on_text(event.text)
                    msg = await stream.get_final_message()
                break
            except ValueError as e:  # unparseable eagerly-streamed tool input -> re-issue
                json_retries += 1
                log.warning("tool input JSON could not be parsed (attempt %s): %s", json_retries, e)
                if json_retries >= 3:
                    raise

        stop_details = None
        if getattr(msg, "stop_details", None) is not None:
            try:
                stop_details = msg.stop_details.to_dict()
            except Exception:  # pragma: no cover - defensive
                stop_details = {"raw": str(msg.stop_details)}
        return LLMResponse(
            content=[b.to_dict(mode="json", exclude_none=True) for b in msg.content],
            stop_reason=msg.stop_reason or "end_turn",
            input_tokens=(msg.usage.input_tokens or 0)
            + (getattr(msg.usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(msg.usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=msg.usage.output_tokens or 0,
            model=msg.model,
            stop_details=stop_details,
        )


# --------------------------------------------------------------------------- offline LLMs


@dataclass
class ScriptedLLM:
    """Test double. Each entry is either an LLMResponse or a callable(request)->LLMResponse."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    default_text: str = "ok"

    async def complete(self, *, model, system, messages, tools, effort="medium", on_text=None) -> LLMResponse:
        req = {"model": model, "system": system, "messages": json.loads(json.dumps(messages, default=str)), "tools": tools}
        self.calls.append(req)
        if self.script:
            item = self.script.pop(0)
            resp = item(req) if callable(item) else item
        else:
            resp = text_response(self.default_text)
        if on_text and resp.text:
            await on_text(resp.text)
        return resp


def text_response(text: str) -> LLMResponse:
    return LLMResponse(content=[{"type": "text", "text": text}], stop_reason="end_turn")


_tool_counter = 0


def tool_response(name: str, args: dict[str, Any], text: str = "") -> LLMResponse:
    global _tool_counter
    _tool_counter += 1
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": f"toolu_stub_{_tool_counter}", "name": name, "input": args})
    return LLMResponse(content=content, stop_reason="tool_use")


class StubLLM:
    """Offline stand-in used with CPM_STUB_LLM=1 so the UI works without an API key.

    It does not reason - it echoes the request shape back so the wiring is visible.
    """

    async def complete(self, *, model, system, messages, tools, effort="medium", on_text=None) -> LLMResponse:
        last = messages[-1]["content"]
        if isinstance(last, list):
            if any(b.get("type") == "tool_result" for b in last if isinstance(b, dict)):
                text = "(stub) Done."
                if on_text:
                    await on_text(text)
                return text_response(text)
            last = " ".join(b.get("text", "") for b in last if isinstance(b, dict))
        tool_names = {t["name"] for t in tools}
        if "get_health" in tool_names and "owner_message" in str(last):
            return tool_response("get_health", {}, "")
        text = (
            "(stub LLM - set ANTHROPIC_API_KEY and unset CPM_STUB_LLM for real answers) "
            f"I received {len(str(last))} characters of context."
        )
        if on_text:
            await on_text(text)
        return text_response(text)


def make_llm() -> LLM:
    from ..config import settings

    if settings.use_stub_llm or settings.llm_provider == "stub":
        return StubLLM()
    if settings.llm_provider == "ollama":
        from .ollama_llm import OllamaLLM

        return OllamaLLM(settings.ollama_model, settings.ollama_url, settings.ollama_num_ctx, think=settings.ollama_think)
    return ClaudeLLM()
