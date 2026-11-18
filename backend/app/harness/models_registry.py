"""Which LLM runs a project's agents.

Resolution order for a project: its chosen ModelConfig -> the organization's
default ModelConfig -> the server default from the environment (CPM_LLM_PROVIDER).
LLM clients are cached per (provider, model) so switching models is cheap.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from sqlmodel import Session, select

from ..config import settings
from ..models import ModelConfig, Project
from .llm import LLM, ClaudeLLM, StubLLM

log = logging.getLogger(__name__)

PROVIDERS = ("ollama", "anthropic", "stub")
CLAUDE_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
]

_clients: dict[tuple[str, str], LLM] = {}
_ollama_cache: tuple[float, list[dict[str, Any]] | None] = (0.0, None)


def client_for(provider: str, model: str) -> LLM:
    key = (provider, model if provider == "ollama" else "")
    if key not in _clients:
        if provider == "ollama":
            from .ollama_llm import OllamaLLM

            _clients[key] = OllamaLLM(model, settings.ollama_url, settings.ollama_num_ctx, think=settings.ollama_think)
        elif provider == "anthropic":
            _clients[key] = ClaudeLLM()
        else:
            _clients[key] = StubLLM()
    return _clients[key]


def ollama_models(force: bool = False) -> list[dict[str, Any]] | None:
    """Installed Ollama models (cached 30 s); None if Ollama is not reachable."""
    global _ollama_cache
    ts, cached = _ollama_cache
    if not force and time.time() - ts < 30:
        return cached
    try:
        r = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=2.5)
        r.raise_for_status()
        models = [{"model": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
                   "family": (m.get("details") or {}).get("family", "")} for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        models = None
    _ollama_cache = (time.time(), models)
    return models


def availability(provider: str, model: str) -> tuple[bool, str]:
    if provider == "stub":
        return True, "offline placeholder answers"
    if provider == "anthropic":
        import os

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return has_key, "ready" if has_key else "no ANTHROPIC_API_KEY on the server"
    installed = ollama_models()
    if installed is None:
        return False, "Ollama is not running"
    if not any(m["model"] == model or m["model"] == f"{model}:latest" for m in installed):
        return False, f"not installed - run: ollama pull {model}"
    return True, "ready (local)"


def config_view(c: ModelConfig) -> dict[str, Any]:
    ok, note = availability(c.provider, c.model)
    return {"id": c.id, "label": c.label, "provider": c.provider, "model": c.model, "enabled": c.enabled,
            "is_default": c.is_default, "available": ok, "status": note}


def server_default() -> tuple[str, str]:
    p = settings.llm_provider if settings.llm_provider in PROVIDERS else "anthropic"
    if settings.use_stub_llm:
        p = "stub"
    if p == "ollama":
        return p, settings.ollama_model
    if p == "anthropic":
        return p, settings.router_model
    return "stub", "stub"


def resolve(s: Session, project_id: int) -> tuple[str, str, str]:
    """(provider, model, label) for a project's agents."""
    p = s.get(Project, project_id)
    cfg = None
    if p is not None and p.model_config_id:
        cfg = s.get(ModelConfig, p.model_config_id)
        if cfg is not None and not cfg.enabled:
            cfg = None
    if cfg is None and p is not None and p.org_id:
        cfg = s.exec(select(ModelConfig).where(ModelConfig.org_id == p.org_id, ModelConfig.is_default == True,  # noqa: E712
                                               ModelConfig.enabled == True)).first()  # noqa: E712
    if cfg is not None:
        return cfg.provider, cfg.model, cfg.label
    prov, model = server_default()
    return prov, model, f"server default ({prov})"
