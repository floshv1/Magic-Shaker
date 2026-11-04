"""Décisions humaines sur les points à valider : validation, stockage, rendu séparé, traçabilité."""

from __future__ import annotations

import pytest

from p9.checks import build_matrix, run_all
from p9.generate import generate
from p9.review import (
    Decision,
    ReviewStore,
    decision_errors,
    finding_id,
    load_review,
    review_csv,
    review_state,
    save_review,
)

REVIEW_PKG = "ESpaceConceptReview.sysml"


@pytest.fixture(scope="module")
def findings(export):
    return run_all(export)


def _text_constraint(findings, contains: str):
    return next(f for f in findings["constraints_in_text_only"] if contains in f.message)


def _dec(f, action: str, **kw) -> Decision:
    return Decision(finding_id=finding_id(f), check=f.check, action=action, author="Testeur",
                    decided_at="2026-11-04", target_ids=list(f.ids), finding_message=f.message, **kw)


def test_id_de_point_stable_et_unique(export, findings) -> None:
    again = run_all(export)
    for check in ("constraints_in_text_only", "persona_inconsistencies", "implicit_needs"):
        ids = [finding_id(f) for f in findings[check]]
        assert ids == [finding_id(f) for f in again[check]]
        assert len(set(ids)) == len(ids)


def test_validation_des_decisions(export, findings) -> None:
    speed = _text_constraint(findings, "10 km/h")
    assert decision_errors(_dec(speed, "guard", title="Vitesse", text="vitesse < 10 km/h"), export) == []
    assert decision_errors(_dec(speed, "guard", title="Vitesse"), export)            # ni texte ni état
    assert decision_errors(_dec(speed, "dismiss", reason=""), export)                # raison obligatoire
    assert decision_errors(_dec(speed, "requirement", title="x", text="y"), export)  # action impossible ici
    comp = export.components[0]
    other_state = next(s for s in export.component_states if s.component_id != comp.component_id)
    assert decision_errors(_dec(speed, "guard", title="Garde", component_id=comp.component_id,
                                state_id=other_state.state_id), export)               # état d'un autre composant
    insight = findings["implicit_needs"][0]
    assert decision_errors(_dec(insight, "link_need", need_id="inconnu"), export)
    assert decision_errors(_dec(insight, "link_need", need_id=export.user_needs[0].user_need_id), export) == []


def test_stockage_stable_et_relu(tmp_path, findings) -> None:
    path = tmp_path / "review" / "decisions.json"
    a = _dec(findings["implicit_needs"][0], "dismiss", reason="pas un besoin produit")
    b = _dec(_text_constraint(findings, "10 km/h"), "guard", title="Vitesse", text="vitesse < 10 km/h")
    store = ReviewStore()
    store.upsert(a)
    store.upsert(b)
    store.upsert(b.model_copy(update={"title": "Vitesse basse"}))     # remplace, ne duplique pas
    save_review(store, path)
    first = path.read_bytes()
    save_review(load_review(path), path)
    assert path.read_bytes() == first                                   # relu puis réécrit : identique
    assert [d.title for d in load_review(path).decisions] == ["Vitesse basse", ""]  # trié par contrôle
    assert load_review(tmp_path / "absent.json").decisions == []


def test_etat_de_la_revue_et_decisions_orphelines(findings) -> None:
    store = ReviewStore()
    store.upsert(_dec(findings["implicit_needs"][0], "dismiss", reason="pas un besoin produit"))
    store.upsert(Decision(finding_id="disparu00000", check="implicit_needs", action="dismiss", author="X",
                          decided_at="2026-11-01", reason="point d'un ancien export"))
    state = review_state(findings, store)
    assert state.total == sum(len(findings[k]) for k in
                              ("constraints_in_text_only", "persona_inconsistencies", "implicit_needs"))
    assert state.done == 1 and [d.finding_id for d in state.orphans] == ["disparu00000"]


def test_rendu_separe_et_traceabilite(export, findings) -> None:
    speed = _text_constraint(findings, "10 km/h")
    console = _text_constraint(findings, "console")
    gear = next(c for c in export.components if any(s.component_id == c.component_id for s in export.component_states))
    gear_state = next(s for s in export.component_states if s.component_id == gear.component_id)
    insight, other_insight = findings["implicit_needs"][:2]
    decisions = [
        _dec(speed, "guard", title="Vitesse sous 10 km/h", text="vitesse < 10 km/h"),
        _dec(console, "guard", title="Console au centre", component_id=gear.component_id,
             state_id=gear_state.state_id),
        _dec(insight, "requirement", title="Indication de distance", text="Le véhicule doit indiquer la distance."),
        _dec(other_insight, "link_need", need_id=export.user_needs[0].user_need_id),
        _dec(findings["persona_inconsistencies"][0], "fix_at_source", reason="Hugo est le passager avant"),
    ]
    base = generate(export)
    gen = generate(export, review_decisions=decisions)
    # le contenu d'origine n'est pas modifié : seul un fichier séparé s'ajoute
    assert {k: v for k, v in gen.files.items() if k != REVIEW_PKG} == base.files
    src = gen.files[REVIEW_PKG]
    assert generate(export, review_decisions=list(reversed(decisions))).files[REVIEW_PKG] == src  # déterministe
    assert src.count('origin = "human"') >= 5 and "Testeur" in src and 'decisionKind = "guard"' in src
    assert "constraint def VitesseSous10KmH" in src and "requirement def IndicationDeDistance" in src
    assert "Hugo est le passager avant" not in src                    # fix_at_source : rien dans le modèle
    # traçabilité : les deux contraintes en texte libre sont traitées par une garde humaine
    matrix = build_matrix(export, gen, findings["constraints_in_text_only"], decisions)
    rows = [e for e in matrix if e.kind == "text_constraint"]
    assert rows and all(e.status == "mapped" and "garde ajoutee par Testeur" in e.reason for e in rows)
    assert all(e.sysml_qualified_name.startswith("ESpaceConceptReview::") for e in rows)
    base_rows = [e for e in build_matrix(export, base, findings["constraints_in_text_only"]) if e.kind == "text_constraint"]
    assert all(e.status == "partial" for e in base_rows)
    assert "Testeur" in review_csv(ReviewStore(decisions=decisions))


def test_ecarter_un_point_le_trace_avec_sa_raison(export, findings) -> None:
    speed = _text_constraint(findings, "10 km/h")
    d = _dec(speed, "dismiss", reason="la vitesse est geree par le calculateur, hors parcours")
    gen = generate(export, review_decisions=[d])
    assert REVIEW_PKG not in gen.files                                # rien à ajouter au modèle
    row = next(e for e in build_matrix(export, gen, findings["constraints_in_text_only"], [d])
               if e.kind == "text_constraint" and speed.step_id in e.usershaker_id)
    assert row.status == "mapped" and "ecarte par Testeur" in row.reason and "calculateur" in row.reason


def test_use_case_non_selectionne_signale(export, findings) -> None:
    speed = _text_constraint(findings, "10 km/h")
    d = _dec(speed, "guard", title="Vitesse sous 10 km/h", text="vitesse < 10 km/h")
    others = [u.use_case_id for u in export.use_cases if u.use_case_id != speed.use_case_id]
    gen = generate(export, others, review_decisions=[d])
    entry = next(e for e in gen.trace if e.kind == "review_decision")
    assert entry.status == "partial" and "hors des use cases generes" in entry.reason
