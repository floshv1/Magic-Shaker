"""Tests de la couche LLM sans appel réseau : fournisseur simulé."""

from __future__ import annotations

import json

import pytest

from p9.generate import generate
from p9.llm import (
    TOOLS,
    Proposal,
    ProposalStore,
    ToolContext,
    ask,
    build_payload,
    load_store,
    run_tool,
    save_store,
    validate_proposals,
)
from p9.llm.providers import ChatResult, ToolCall


@pytest.fixture(scope="module")
def ctx(export) -> ToolContext:
    return ToolContext(export)


def test_schemas_outils_stricts() -> None:
    for t in TOOLS:
        p = t.parameters
        assert p["type"] == "object" and p["additionalProperties"] is False
        assert set(p["required"]) == set(p["properties"]), t.name   # requis par strict: true


def test_outils_repondent_avec_ids(ctx) -> None:
    out, err = run_tool(ctx, "constraints_in_text_only", {})
    data = json.loads(out)
    assert not err and len(data) == 2 and all(d["ids"] for d in data)
    out, err = run_tool(ctx, "find_element", {"element_id": data[0]["ids"][0]})
    el = json.loads(out)
    assert not err and el["kind"] == "step" and el["traceability"]
    assert "image_url" not in el["data"]


def test_outil_erreurs_sans_exception(ctx) -> None:
    assert run_tool(ctx, "inconnu", {})[1]
    assert run_tool(ctx, "find_element", {})[1]
    assert run_tool(ctx, "find_element", {"element_id": "x", "extra": 1})[1]
    out, err = run_tool(ctx, "find_element", {"element_id": "absent"})
    assert not err and "introuvable" in out


class FakeProvider:
    """Simule un LLM : appelle deux outils puis répond en citant des ids."""

    name = "fake"

    def chat_with_tools(self, system, question, history, ctx, on_event=None):
        calls = []
        for name, args in [("constraints_in_text_only", {}), ("implicit_needs", {})]:
            out, err = run_tool(ctx, name, args)
            calls.append(ToolCall(name, args, out, err))
        first = json.loads(calls[0].output)[0]["ids"][0]
        return ChatResult(text=f"Contrainte non structuree sur `{first}` ; id invente `zzzzzzzzzzzzzzzzzzzzzzzzz`.",
                          tool_calls=calls, provider=self.name, model="fake")

    def structured(self, system, prompt, schema, on_event=None):
        items = json.loads(prompt.split("\n", 1)[1])
        step = next(i for i in items if i["type"] == "text_constraint")
        return json.dumps({"proposals": [
            {"kind": "guard", "title": "Vitesse inferieure a 10 km/h", "text": "Le rapport parking n'est actif "
             "que si la vitesse est inferieure a 10 km/h.", "rationale": "Contrainte du trigger_text.",
             "target_ids": [step["step_id"]]},
            {"kind": "requirement", "title": "Id invente", "text": "x" * 10, "rationale": "y" * 10,
             "target_ids": ["nexistepas"]},
        ]})


def test_agent_cite_et_signale_ids_inconnus(ctx) -> None:
    ans = ask(FakeProvider(), ctx, "Quelles contraintes sont seulement en texte libre ?")
    assert [c["name"] for c in ans.tool_calls] == ["constraints_in_text_only", "implicit_needs"]
    assert ans.cited_ids and ans.unknown_ids == ["zzzzzzzzzzzzzzzzzzzzzzzzz"]


def test_payload_minimal(ctx) -> None:
    items = build_payload(ctx)
    blob = json.dumps(items, ensure_ascii=False)
    assert {i["type"] for i in items} == {"text_constraint", "negative_insight", "persona_inconsistency"}
    for p in ctx.export.personas:          # pas de biographie envoyée
        assert not p.biography or p.biography not in blob
    assert "image_url" not in blob and "usershaker.com" not in blob


def test_enrichissement_valide_et_marque(ctx) -> None:
    from p9.llm import enrich
    res = enrich(FakeProvider(), ctx)
    assert len(res.proposals) == 1 and len(res.invalid) == 1
    p = res.proposals[0]
    assert p.origin == "llm" and p.status == "to_validate" and p.kind == "guard"


def test_sortie_non_conforme(ctx) -> None:
    res = validate_proposals('{"proposals": [{"kind": "autre"}]}', ctx.export, set())
    assert not res.proposals and res.invalid


def _proposal(ctx, **kw) -> Proposal:
    step = next(s for s in ctx.export.steps if s.name == "Parking ratio")
    base = {"kind": "guard", "title": "Vitesse sous 10 km/h", "text": "Vitesse < 10 km/h.",
            "rationale": "trigger_text", "target_ids": [step.step_id], "proposal_id": "abc123def456"}
    return Proposal(**{**base, **kw})


def test_store_decisions(ctx, tmp_path) -> None:
    store = ProposalStore()
    p = _proposal(ctx)
    store.merge([p])
    store.decide(p.proposal_id, "accepted")
    store.merge([p.model_copy(update={"status": "to_validate"})])   # ne réécrase pas la décision
    save_store(store, tmp_path / "p.json")
    again = load_store(tmp_path / "p.json")
    assert [q.status for q in again.proposals] == ["accepted"] and len(again.accepted()) == 1


def test_rendu_propositions_acceptees_separees(ctx) -> None:
    need = ctx.export.user_needs[0]
    accepted = [_proposal(ctx), _proposal(ctx, kind="rephrase", title="Reformulation", proposal_id="fff000aaa111",
                                          target_ids=[need.user_need_id])]
    g = generate(ctx.export, accepted_proposals=accepted)
    text = g.files["ESpaceConceptLlmProposals.sysml"]
    assert 'origin = "llm"' in text and "constraint def VitesseSous10KmH" in text
    assert "dependency from UC3_UrbanManoeuvreWithSteerByWire::" in text
    assert ":> ESpaceCommon::" in text
    assert "LlmOrigin" not in g.single_file              # jamais mélangé au modèle d'origine
    assert "LlmOrigin" not in g.files["ESpaceCommon.sysml"]
    assert {e.kind for e in g.trace if e.kind == "llm_proposal"} == {"llm_proposal"}
