"""Tool framework: pydantic-validated tools that agents can call."""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from .runtime import Runtime


class Args(BaseModel):
    """Base for tool argument models (rejects unknown fields)."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


@dataclass
class ToolContext:
    runtime: "Runtime"
    project_id: int
    actor: str  # "router" | "worker:<name>#<work_item_id>"
    work_item_id: int | None = None


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        _strip_titles(schema)
        schema.setdefault("properties", {})
        return {"name": self.name, "description": self.description, "input_schema": schema}

    async def run(self, ctx: ToolContext, raw: Any) -> tuple[str, bool]:
        """Returns (content, is_error). Input is validated before the tool runs -
        eagerly-streamed inputs are not validated by the API."""
        if not isinstance(raw, dict):
            return json.dumps({"INVALID_JSON": str(raw)[:2000]}), True
        try:
            args = self.args.model_validate(raw)
        except ValidationError as e:
            return f"Invalid arguments for {self.name}: {e.errors(include_url=False)}", True
        try:
            out = self.fn(ctx, args)
            if inspect.isawaitable(out):
                out = await out
        except ToolError as e:
            return str(e), True
        except Exception as e:  # tool bugs should not kill the agent loop
            return f"Tool {self.name} failed: {type(e).__name__}: {e}", True
        if isinstance(out, str):
            return out, False
        return json.dumps(out, default=str, ensure_ascii=False), False


class ToolError(Exception):
    """Raise inside a tool to return an is_error result with a helpful message."""


def _strip_titles(node: Any, is_property_map: bool = False) -> None:
    """Remove pydantic's auto-generated "title" annotations from schema nodes
    (but never a *property* that happens to be called "title")."""
    if isinstance(node, dict):
        if not is_property_map and isinstance(node.get("title"), str):
            node.pop("title")
        for k, v in node.items():
            _strip_titles(v, is_property_map=(k in ("properties", "$defs") and not is_property_map))
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)
