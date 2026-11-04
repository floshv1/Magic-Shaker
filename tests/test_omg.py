"""Tests qui lancent le vrai parseur OMG (lents) : `make test-omg`."""

from __future__ import annotations

import pytest

from p9.checks.sysml_parser import is_installed, order_by_imports, validate_texts
from p9.generate import generate

pytestmark = pytest.mark.omg


def test_ordre_des_imports() -> None:
    texts = {
        "a.sysml": "package UC1 {\n    private import Common::*;\n}\n",
        "b.sysml": "package Common {\n    private import ScalarValues::*;\n}\n",
    }
    assert order_by_imports(texts) == ["b.sysml", "a.sysml"]


@pytest.mark.skipif(not is_installed(), reason="parseur OMG absent (make validate-setup)")
def test_fichiers_generes_passent_le_parseur(export, tmp_path) -> None:
    gen = generate(export)
    res = validate_texts(gen.files, tmp_path / "split")
    assert res.ok, [i.model_dump() for i in res.issues][:10]
    assert len(res.files) == 5


@pytest.mark.skipif(not is_installed(), reason="parseur OMG absent (make validate-setup)")
def test_fichier_unique_et_propositions_llm_passent_le_parseur(export, tmp_path) -> None:
    from p9.llm import Proposal
    step = next(s for s in export.steps if s.name == "Parking ratio")
    fb = next(f for f in export.persona_step_feedback if f.insight == "Wants a clearer distance cue.")
    need = export.user_needs[0]
    accepted = [
        Proposal(kind="guard", title="Vitesse sous 10 km/h", text="Rapport parking actif sous 10 km/h.",
                 rationale="trigger_text", target_ids=[step.step_id], proposal_id="aaaaaaaaaaaa", status="accepted"),
        Proposal(kind="requirement", title="Indication de distance claire", text="Le vehicule doit afficher "
                 "la distance restante.", rationale="insight -1", target_ids=[fb.feedback_id, fb.step_id],
                 proposal_id="bbbbbbbbbbbb", status="accepted"),
        Proposal(kind="rephrase", title="Besoin reformule", text="Reformulation.", rationale="plus verifiable",
                 target_ids=[need.user_need_id], proposal_id="cccccccccccc", status="accepted"),
    ]
    gen = generate(export, accepted_proposals=accepted)
    res = validate_texts(gen.files, tmp_path / "with-llm")
    assert res.ok, [i.model_dump() for i in res.issues][:10]
    assert len(res.files) == 6
    single = validate_texts({"model.sysml": gen.single_file}, tmp_path / "single")
    assert single.ok, [i.model_dump() for i in single.issues][:10]


@pytest.mark.skipif(not is_installed(), reason="parseur OMG absent (make validate-setup)")
def test_erreur_detectee(tmp_path) -> None:
    res = validate_texts({"bad.sysml": "package Bad { part x : Inconnu; }\n"}, tmp_path / "bad")
    assert not res.ok
    assert any("Inconnu" in i.message for i in res.issues)
