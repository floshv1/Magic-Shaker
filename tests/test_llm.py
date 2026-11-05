"""Tests de la couche LLM sans appel réseau : fournisseur simulé."""

from __future__ import annotations

import json

import pytest

from p9.checks import build_matrix
from p9.generate import generate
from p9.llm import (
    TOOLS,
    Proposal,
    ProposalFileError,
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
    res = validate_proposals("pas du json", ctx.export, set())
    assert not res.proposals and "JSON invalide" in res.invalid[0]["detail"]


def test_une_proposition_non_conforme_n_ecarte_pas_les_autres(ctx) -> None:
    step = next(s for s in ctx.export.steps if s.name == "Parking ratio")
    ok = {"kind": "guard", "title": "Vitesse sous 10 km/h", "text": "Vitesse < 10 km/h.", "rationale": "trigger_text",
          "target_ids": [step.step_id]}
    raw = json.dumps({"proposals": [ok, {**ok, "title": "T" * 130}]})
    res = validate_proposals(raw, ctx.export, {step.step_id})
    assert [p.title for p in res.proposals] == ["Vitesse sous 10 km/h"]
    assert len(res.invalid) == 1 and "title" in res.invalid[0]["detail"]


def test_schema_de_sortie_sans_mot_cle_refuse_par_l_api() -> None:
    """minLength / maxLength / minItems… : refusés par les sorties structurées de l'API Claude (erreur 400)."""
    from p9.llm.enrich import output_schema

    def keys(node) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        return {k for v in node for k in keys(v)} if isinstance(node, list) else set()
    assert not keys(output_schema()) & {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}


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
    (tmp_path / "p.json").write_text("{ pas du json", encoding="utf-8")     # illisible : jamais lu comme vide
    with pytest.raises(ProposalFileError, match="illisible"):
        load_store(tmp_path / "p.json")


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
    # chaque proposition acceptée a sa ligne dans la matrice de traçabilité, avec son origine
    rows = [e for e in build_matrix(ctx.export, g) if e.kind == "llm_proposal"]
    assert sorted(e.usershaker_id for e in rows) == sorted(p.proposal_id for p in accepted)
    assert all("origin=llm" in e.reason and e.file == "ESpaceConceptLlmProposals.sysml" for e in rows)


# ------------------------------------------------------------ outils ajoutés, relance, mesure

def test_component_timeline(export, ctx) -> None:
    wheel = next(c.component_id for c in export.components if c.name == "Steer-by-wire half wheel")
    uc4 = next(u.use_case_id for u in export.use_cases if u.position == 4)
    out, err = run_tool(ctx, "component_timeline", {"component_id": wheel, "use_case_id": uc4})
    data = json.loads(out)
    assert not err and [u["use_case_id"] for u in data["use_cases"]] == [uc4]
    first = data["use_cases"][0]["steps"][0]
    assert first["step"] == "Park at the charger" and first["event"] == "Gear selector is P"
    assert [s["name"] for s in first["states"]] == ["Normal ratio"] and first["changed"] is False
    every, _ = run_tool(ctx, "component_timeline", {"component_id": wheel, "use_case_id": ""})
    assert len(json.loads(every)["use_cases"]) > 1
    assert "introuvable" in run_tool(ctx, "component_timeline", {"component_id": "x", "use_case_id": ""})[0]


def test_traceability_status_signale_la_troncature(ctx) -> None:
    data = json.loads(run_tool(ctx, "traceability_status", {"status": "mapped"})[0])
    assert data["total"] > data["shown"] == len(data["rows"]) and data["truncated"] is True


def test_index_dans_le_prompt(export, ctx) -> None:
    from p9.llm.agent import context_index
    idx = context_index(ctx)
    assert all(c.component_id in idx for c in export.components)
    assert all(p.biography not in idx for p in export.personas if p.biography)


def test_relance_sur_erreur_passagere(monkeypatch) -> None:
    from p9.config import load_settings
    from p9.llm import providers
    prov = providers.OllamaProvider(load_settings().llm)
    calls = []

    def flaky(payload, on_event=None):
        calls.append(1)
        if len(calls) == 1:
            raise providers._Transient("Ollama : Internal Server Error")
        return {"message": {"role": "assistant", "content": "ok"}, "done_reason": "stop"}

    monkeypatch.setattr(prov, "_post_once", flaky)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    assert prov._post({})["message"]["content"] == "ok" and len(calls) == 2

    def down(payload, on_event=None):
        raise providers._Transient("Ollama : Internal Server Error")

    monkeypatch.setattr(prov, "_post_once", down)
    with pytest.raises(providers.LlmUnavailable, match="3 essais"):
        prov._post({})
    assert not providers._is_transient("model 'x' not found")


def test_score_d_une_reponse() -> None:
    from p9.llm.agent import AgentAnswer
    from p9.llm.evaluate import EvalQuestion, score_answer
    q = EvalQuestion(id="q", question="?", must_cite=["aaaaaaaaaaaaaaaaaaaaaaaaa"],
                     must_mention=[["ratio normal", "normal ratio"]], tools_any=["component_timeline"])
    good = AgentAnswer(text="Le volant reste en Ratio normal (`aaaaaaaaaaaaaaaaaaaaaaaaa`).",
                       tool_calls=[{"name": "component_timeline"}])
    assert score_answer(q, good).passed
    bad = AgentAnswer(text="The wheel is in Parking ratio.", tool_calls=[{"name": "find_element"}],
                      unknown_ids=["zzz"])
    res = score_answer(q, bad)
    assert not res.passed and res.score == 0 and res.missing_ids == ["aaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert res.criteria["language"] == 0.0


def test_questions_de_mesure_valides(export) -> None:
    from p9.config import load_settings
    from p9.llm.evaluate import load_questions
    from p9.llm.tools import TOOLS_BY_NAME
    questions = load_questions(load_settings().llm_eval_file)
    ids = ({u.use_case_id for u in export.use_cases} | {s.step_id for s in export.steps}
           | {p.persona_id for p in export.personas} | {c.component_id for c in export.components}
           | {n.user_need_id for n in export.user_needs} | {f.feedback_id for f in export.persona_step_feedback})
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        assert set(q.must_cite) <= ids, q.id
        assert set(q.tools_any) <= TOOLS_BY_NAME.keys(), q.id
