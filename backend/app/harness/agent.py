"""Shared manual agent loop used by the Router and every Worker.

A manual loop (instead of the SDK tool runner) because the harness needs:
mid-run inbox injection (direct delivery), per-turn persistence (resume after
restart), turn budgets (mandates) and cancellation by the Watchdog.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .llm import LLM, TextCallback
from .tools import Tool, ToolContext

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    text: str  # all assistant text across turns
    stop: str  # end_turn | max_turns | refusal | max_tokens
    turns: int
    input_tokens: int
    output_tokens: int
    messages: list[dict[str, Any]]
    final_text: str = ""  # text of the last assistant turn that had any


TurnCallback = Callable[[list[dict[str, Any]], "AgentResult"], Awaitable[None]]
InboxFetch = Callable[[], Awaitable[list[str]]]
ToolCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


async def run_agent_loop(
    llm: LLM,
    *,
    model: str,
    effort: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    ctx: ToolContext,
    max_turns: int,
    on_text: TextCallback | None = None,
    on_turn: TurnCallback | None = None,
    on_tool: ToolCallback | None = None,
    inbox_fetch: InboxFetch | None = None,
) -> AgentResult:
    by_name = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    texts: list[str] = []
    state = AgentResult("", "end_turn", 0, 0, 0, messages)

    while state.turns < max_turns:
        resp = await llm.complete(
            model=model, system=system, messages=messages, tools=specs, effort=effort, on_text=on_text
        )
        state.turns += 1
        state.input_tokens += resp.input_tokens
        state.output_tokens += resp.output_tokens
        messages.append({"role": "assistant", "content": resp.content})
        if resp.text:
            texts.append(resp.text)

        if resp.stop_reason == "refusal":
            # Fallbacks already ran server-side; the whole chain declined.
            state.stop = "refusal"
            texts.append("[The model declined to continue this request.]")
            break

        if resp.stop_reason == "pause_turn":
            if on_turn:
                await on_turn(messages, state)
            continue

        tool_uses = resp.tool_uses
        if not tool_uses:
            state.stop = "max_tokens" if resp.stop_reason == "max_tokens" else "end_turn"
            break

        results: list[dict[str, Any]] = []
        for tu in tool_uses:
            if resp.stop_reason == "max_tokens":
                content, is_error = "Tool input was cut off (max_tokens). Retry with a shorter input.", True
            elif tu["name"] not in by_name:
                content, is_error = f"Unknown tool {tu['name']}", True
            else:
                if on_tool:
                    await on_tool(tu["name"], tu.get("input") or {})
                content, is_error = await by_name[tu["name"]].run(ctx, tu.get("input"))
            block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tu["id"], "content": content}
            if is_error:
                block["is_error"] = True
            results.append(block)

        user_content: list[dict[str, Any]] = list(results)
        if inbox_fetch is not None:
            for note in await inbox_fetch():
                user_content.append({"type": "text", "text": note})
        messages.append({"role": "user", "content": user_content})
        if on_turn:
            await on_turn(messages, state)
    else:
        state.stop = "max_turns"

    state.text = "\n\n".join(t.strip() for t in texts if t.strip())
    state.final_text = next((t.strip() for t in reversed(texts) if t.strip()), "")
    if on_turn:
        await on_turn(messages, state)
    return state
