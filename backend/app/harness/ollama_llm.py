"""Local-model adapter (Ollama native /api/chat) for offline end-to-end testing.

The harness speaks Anthropic Messages format internally (content blocks,
tool_use / tool_result). This adapter translates to Ollama's chat format and
back, so the Router, Workers and Watchdog run unchanged on a local model.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

from .llm import LLMResponse, TextCallback

log = logging.getLogger(__name__)


def _to_ollama_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    id_to_name: dict[str, str] = {}
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = []
            for b in content:
                if b.get("type") == "tool_use":
                    id_to_name[b["id"]] = b["name"]
                    calls.append({"function": {"name": b["name"], "arguments": b.get("input") or {}}})
            msg: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn: tool results and/or text
            texts = []
            for b in content:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                    prefix = "ERROR: " if b.get("is_error") else ""
                    out.append({"role": "tool", "tool_name": id_to_name.get(b["tool_use_id"], ""), "content": prefix + str(c)})
                elif b.get("type") == "text":
                    texts.append(b["text"])
                elif b.get("type") == "image":
                    texts.append("[image omitted: the local model cannot see images]")
            if texts:
                out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def _to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


class OllamaLLM:
    def __init__(self, model: str, base_url: str = "http://localhost:11434", num_ctx: int = 12288,
                 timeout: float = 600, think: str = "off"):
        self.model = model
        self.think = think  # off | on | auto (auto = think unless effort is low)
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.client = httpx.AsyncClient(timeout=timeout)

    async def complete(
        self,
        *,
        model: str,  # Claude model id from config - ignored; the local model is used
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        effort: str = "medium",
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _to_ollama_messages(system, messages),
            "stream": True,
            "think": self.think == "on" or (self.think == "auto" and effort != "low"),
            "options": {"num_ctx": self.num_ctx, "temperature": 0.3},
        }
        if tools:
            body["tools"] = _to_ollama_tools(tools)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        done_reason = "stop"
        in_tok = out_tok = 0
        async with self.client.stream("POST", f"{self.base_url}/api/chat", json=body) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Ollama error {r.status_code}: {(await r.aread()).decode()[:500]}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise RuntimeError(f"Ollama error: {chunk['error']}")
                msg = chunk.get("message") or {}
                if msg.get("content"):
                    text_parts.append(msg["content"])
                    if on_text:
                        await on_text(msg["content"])
                for tc in msg.get("tool_calls") or []:
                    tool_calls.append(tc)
                if chunk.get("done"):
                    done_reason = chunk.get("done_reason", "stop")
                    in_tok = chunk.get("prompt_eval_count", 0) or 0
                    out_tok = chunk.get("eval_count", 0) or 0

        content: list[dict[str, Any]] = []
        text = "".join(text_parts).strip()
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some models return a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            content.append({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:16]}", "name": fn.get("name", ""), "input": args})
        if not content:
            content.append({"type": "text", "text": ""})
        stop = "tool_use" if tool_calls else ("max_tokens" if done_reason == "length" else "end_turn")
        return LLMResponse(content=content, stop_reason=stop, input_tokens=in_tok, output_tokens=out_tok, model=self.model)
