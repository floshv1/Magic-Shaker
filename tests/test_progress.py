"""Suivi en direct des opérations longues : estimations, événements du LLM (sans réseau)."""

from __future__ import annotations

import io
import json

import pytest

from p9.config import LlmSettings
from p9.llm import ToolContext, ask, enrich
from p9.llm import providers as prov
from p9.progress import estimate, record


def test_estimation_mediane_des_dernieres_mesures(tmp_path) -> None:
    path = tmp_path / "timings.json"
    assert estimate(path, "omg") is None
    for s in (50, 70, 60, 1000, 55, 65):          # seules les 5 dernières comptent
        record(path, "omg", s)
    assert estimate(path, "omg") == 65
    assert estimate(path, "autre") is None


def test_fichier_de_mesures_illisible(tmp_path) -> None:
    path = tmp_path / "timings.json"
    path.write_text("pas du json", encoding="utf-8")
    assert estimate(path, "omg") is None
    record(path, "omg", 12)
    assert estimate(path, "omg") == 12


@pytest.fixture(scope="module")
def ctx(export) -> ToolContext:
    return ToolContext(export)


class _Fake:
    name = "fake"

    def structured(self, system, prompt, schema, on_event=None):
        on_event("step", "appel simule")
        return json.dumps({"proposals": []})

    def chat_with_tools(self, system, question, history, ctx, on_event=None):
        on_event("tool", "outil simule")
        return prov.ChatResult(text="ok", provider="fake", model="fake")


def test_enrich_et_ask_annoncent_leurs_etapes(ctx) -> None:
    seen: list[tuple[str, str]] = []
    enrich(_Fake(), ctx, on_event=lambda k, t: seen.append((k, t)))
    texts = [t for _, t in seen]
    assert texts[0].startswith("Preparation de ") and "appel simule" in texts and texts[-1].startswith("Verification")
    seen.clear()
    ask(_Fake(), ctx, "question", on_event=lambda k, t: seen.append((k, t)))
    assert seen[0] == ("tool", "outil simule") and seen[-1][0] == "step"


def _ndjson(*chunks: dict) -> io.BytesIO:
    return io.BytesIO(b"".join(json.dumps(c).encode() + b"\n" for c in chunks))


CFG = LlmSettings(provider="ollama", anthropic_model="-", effort="low", ollama_host="http://x", ollama_model="m")


def test_ollama_streaming_agrege_la_reponse_et_compte_les_tokens(monkeypatch) -> None:
    stream = _ndjson({"message": {"thinking": "je"}}, {"message": {"thinking": " pense"}},
                     {"message": {"content": '{"proposals"'}}, {"message": {"content": ": []}"}},
                     {"done": True, "done_reason": "stop"})
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return stream

    monkeypatch.setattr(prov.urllib.request, "urlopen", fake_urlopen)
    seen: list[tuple[str, str]] = []
    out = prov.OllamaProvider(CFG).structured("sys", "prompt", {"type": "object"},
                                              on_event=lambda k, t: seen.append((k, t)))
    assert out == '{"proposals": []}' and sent["stream"] is True
    assert seen[0][0] == "step"
    assert seen[-1] == ("tokens", "m : 2 tokens de reflexion, 2 tokens de reponse")


def test_ollama_streaming_appels_d_outils(monkeypatch, ctx) -> None:
    replies = iter([
        _ndjson({"message": {"tool_calls": [{"function": {"name": "uncovered_needs", "arguments": {}}}]}},
                {"done": True}),
        _ndjson({"message": {"content": "Aucun besoin non couvert."}}, {"done": True, "done_reason": "stop"}),
    ])
    monkeypatch.setattr(prov.urllib.request, "urlopen", lambda req, timeout: next(replies))
    seen: list[tuple[str, str]] = []
    res = prov.OllamaProvider(CFG).chat_with_tools("sys", "q", [], ctx, on_event=lambda k, t: seen.append((k, t)))
    assert res.text == "Aucun besoin non couvert." and [c.name for c in res.tool_calls] == ["uncovered_needs"]
    kinds = [k for k, _ in seen]
    assert kinds.count("step") == 2 and "tool" in kinds


def test_ollama_erreur_dans_le_flux(monkeypatch) -> None:
    monkeypatch.setattr(prov.urllib.request, "urlopen", lambda req, timeout: _ndjson({"error": "model not found"}))
    with pytest.raises(prov.LlmUnavailable, match="model not found"):
        prov.OllamaProvider(CFG).structured("sys", "p", {})
