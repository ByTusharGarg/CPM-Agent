"""Model configuration: per-project selection, org default, availability, permissions."""
import time

from sqlmodel import select

from app.db import session_scope
from app.harness import models_registry as reg
from app.models import ModelConfig, Project

from .test_api_auth import client, login  # noqa: F401  (fixture)


def _fake_ollama(models):
    reg._ollama_cache = (time.time(), [{"model": m, "size_gb": 5.2, "family": "qwen3"} for m in models] if models is not None else None)


def test_resolution_order(db):
    with session_scope() as s:
        assert reg.resolve(s, 1)[:2] == ("ollama", "qwen3:8b")  # org default
        sonnet = s.exec(select(ModelConfig).where(ModelConfig.model == "claude-sonnet-5-5")).one()
        p = s.get(Project, 1)
        p.model_config_id = sonnet.id
        s.add(p)
    with session_scope() as s:
        assert reg.resolve(s, 1)[:2] == ("anthropic", "claude-sonnet-5-5")  # project choice wins
        sonnet = s.exec(select(ModelConfig).where(ModelConfig.model == "claude-sonnet-5-5")).one()
        sonnet.enabled = False
        s.add(sonnet)
    with session_scope() as s:
        assert reg.resolve(s, 1)[1] == "qwen3:8b"  # disabled choice falls back to the org default


def test_runtime_uses_project_model(db):
    from app.harness.llm import StubLLM
    from app.harness.ollama_llm import OllamaLLM
    from app.harness.runtime import Runtime

    rt = Runtime()  # dynamic: per-project selection
    llm, model = rt.llm_for(1)
    assert isinstance(llm, OllamaLLM) and llm.model == "qwen3:8b" and model == "qwen3:8b"
    with session_scope() as s:
        stub = s.exec(select(ModelConfig).where(ModelConfig.provider == "stub")).one()
        p = s.get(Project, 2)
        p.model_config_id = stub.id
        s.add(p)
    assert isinstance(rt.llm_for(2)[0], StubLLM)
    fixed = Runtime(StubLLM())  # tests/CLI: a fixed model wins
    assert fixed.llm_for(1)[0] is fixed.llm


def test_availability_reporting():
    _fake_ollama(["qwen3:8b"])
    assert reg.availability("ollama", "qwen3:8b") == (True, "ready (local)")
    ok, note = reg.availability("ollama", "llama3.1:8b")
    assert not ok and "ollama pull llama3.1:8b" in note
    _fake_ollama(None)
    assert reg.availability("ollama", "qwen3:8b") == (False, "Ollama is not running")
    assert reg.availability("stub", "stub")[0]


def test_models_api_and_chooser(client):  # noqa: F811
    _fake_ollama(["qwen3:8b", "llama3.1:8b"])
    ho = login(client, "owner@demo.local")
    models = client.get("/api/models", headers=ho).json()
    assert [m["label"] for m in models][:1] == ["Qwen3 8B (local)"] and models[0]["available"] and models[0]["is_default"]
    # admin adds a newly pulled Ollama model and makes it the default
    r = client.post("/api/models", headers=ho, json={"label": "Llama 3.1 8B (local)", "provider": "ollama", "model": "llama3.1:8b", "is_default": True})
    assert r.status_code == 200 and r.json()["available"]
    assert client.post("/api/models", headers=ho, json={"label": "dup", "provider": "ollama", "model": "llama3.1:8b"}).status_code == 409
    assert sum(m["is_default"] for m in client.get("/api/models", headers=ho).json()) == 1
    # non-admins cannot manage models, but the owner's rep can choose one for the project chat
    hp = login(client, "pm@demo.local")
    assert client.post("/api/models", headers=hp, json={"label": "xx", "provider": "stub", "model": "x"}).status_code == 403
    stub_id = next(m["id"] for m in models if m["provider"] == "stub")
    r = client.put("/api/projects/1/model", headers=hp, json={"model_config_id": stub_id})
    assert r.status_code == 200 and r.json()["provider"] == "stub"
    hv = login(client, "viewer@demo.local")
    assert client.put("/api/projects/1/model", headers=hv, json={"model_config_id": None}).status_code == 403
    assert client.get("/api/projects/1/model", headers=hv).json()["label"] == "Offline placeholder"
    # disabling the chosen model makes the project fall back to the org default
    client.patch(f"/api/models/{stub_id}", headers=ho, json={"enabled": False})
    assert client.get("/api/projects/1/model", headers=ho).json()["model"] == "llama3.1:8b"
    assert client.put("/api/projects/1/model", headers=hp, json={"model_config_id": stub_id}).status_code == 400
