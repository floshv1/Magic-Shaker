from __future__ import annotations

import csv
import io

import pytest

from p9.checks import build_matrix, coverage, coverage_html, matrix_csv, run_all
from p9.checks.consistency import persona_inconsistencies, refers_to, seat_roles, sentences, tokens
from p9.generate import generate


@pytest.fixture(scope="module")
def findings(export):
    return run_all(export)


@pytest.fixture(scope="module")
def gen(export):
    return generate(export)


@pytest.fixture(scope="module")
def matrix(export, gen, findings):
    return build_matrix(export, gen, findings["constraints_in_text_only"])


# ------------------------------------------------------------ pertes connues du dataset (démo)

def test_contrainte_texte_libre_parking_ratio(findings) -> None:
    msgs = [f.message for f in findings["constraints_in_text_only"]]
    assert any("Parking ratio" in m and "Only below 10 km/h." in m and "seuil numerique" in m for m in msgs)


def test_contrainte_texte_libre_console_centre(findings) -> None:
    msgs = [f.message for f in findings["constraints_in_text_only"]]
    assert any("starts a game" in m and "The console is in the centre position." in m for m in msgs)


def test_conditions_structurees_non_signalees(findings) -> None:
    """« Only if all doors are closed and the gear selector is in P » est couvert par des conditions."""
    msgs = " ".join(f.message for f in findings["constraints_in_text_only"])
    assert "Wheel deploys" not in msgs and "Ask for lounge mode" not in msgs
    assert len(findings["constraints_in_text_only"]) == 2


def test_incoherence_personas_jacques_hugo(findings) -> None:
    msgs = [f.message for f in findings["persona_inconsistencies"]]
    assert any("Jacques" in m and "front passenger" in m and "Hugo" in m and "absent" in m for m in msgs)
    assert any("Long trip" in m and "Lounge stop while charging" in m and "Hugo seulement dans" in m for m in msgs)
    assert len(msgs) == 2


def _with_text(export, old: str, new: str):
    """Copie de l'export où `old` est remplacé par `new` dans les textes lus (story, description, trigger_text)."""
    def swap(row, fields):
        upd = {f: getattr(row, f).replace(old, new) for f in fields if old in (getattr(row, f) or "")}
        return row.model_copy(update=upd) if upd else row
    return export.model_copy(update={"use_cases": [swap(u, ("story", "description")) for u in export.use_cases],
                                     "steps": [swap(s, ("description", "trigger_text")) for s in export.steps]})


def test_role_attribue_au_persona_le_plus_proche(export) -> None:
    old = "Jacques has taken the front passenger seat"
    # deux personas cités : le rôle est celui de Hugo (nommé juste avant), conforme à sa biographie
    ex = _with_text(export, old, "Jacques sits behind Hugo, who is the front passenger,")
    assert not [f for f in persona_inconsistencies(ex) if f.detail.get("role")]
    # « co-driver » n'est pas « driver » (rôle que la biographie de Camille lui donne)
    ex = _with_text(export, old, "Jacques waves at the co-driver")
    assert not [f for f in persona_inconsistencies(ex) if f.detail.get("role")]
    assert [r for r, _ in seat_roles("The co-driver and the driver's seat.")] == ["co-driver", "driver's seat"]


def test_besoin_implicite_distance(findings) -> None:
    f = [f for f in findings["implicit_needs"] if "Wants a clearer distance cue." in f.message]
    assert f and f[0].detail["emotion_score"] == "-1" and "Obstacle close" in f[0].message
    assert len(findings["implicit_needs"]) == 8


def test_controles_structurels_verts(findings) -> None:
    assert findings["uncovered_needs"] == []
    assert findings["invalid_triggers"] == []
    assert findings["invalid_grid_states"] == []
    assert {f.message.split("'")[1] for f in findings["unreachable_states"]} == {"Degraded mode", "Tablet"}


# ------------------------------------------------------------ traçabilité

def test_matrice_couvre_tout_l_export(export, matrix) -> None:
    by_kind: dict[str, int] = {}
    for e in matrix:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    assert by_kind["step"] == 24 and by_kind["step_component_state"] == 279 and by_kind["step_trigger"] == 29
    assert by_kind["text_constraint"] == 2
    assert not [e for e in matrix if e.status == "not_mapped"]


def test_matrice_csv(matrix) -> None:
    rows = list(csv.DictReader(io.StringIO(matrix_csv(matrix))))
    assert list(rows[0]) == ["usershaker_id", "kind", "sysml_qualified_name", "status", "reason", "file"]
    assert len(rows) == len(matrix)
    assert all(r["status"] in {"mapped", "partial", "not_mapped"} for r in rows)


def test_selection_partielle_non_mapped_avec_raison(export, findings) -> None:
    uc1 = next(u for u in export.use_cases if u.position == 1)
    g = generate(export, [uc1.use_case_id])
    m = build_matrix(export, g, findings["constraints_in_text_only"])
    steps = [e for e in m if e.kind == "step"]
    assert sum(e.status == "mapped" for e in steps) == 6
    assert all(e.reason == "use case non selectionne" for e in steps if e.status == "not_mapped")
    assert not [e for e in m if e.kind == "text_constraint"]   # les 2 contraintes sont dans UC2 et UC3


def test_couverture_jamais_100_si_incomplet(export, gen, matrix) -> None:
    cov = {r.element: r for r in coverage(export, gen, matrix)}
    assert cov["Steps"].percent == 100
    trig = cov["Triggers / conditions"]
    assert trig.in_export == 29 and trig.translated == 25 and trig.percent == 86
    text = cov["Contraintes en texte libre"]
    assert text.percent == 0 and text.partial == 2 and "a modeliser" in text.note


def test_rapport_html(export, gen, matrix, findings) -> None:
    html = coverage_html(export, gen, matrix, coverage(export, gen, matrix), findings)
    assert html.startswith("<!DOCTYPE html>") and "Only below 10 km/h." in html


# ------------------------------------------------------------ outils texte

def test_outils_texte() -> None:
    assert sentences("A b. C d? E") == ["A b.", "C d?", "E"]
    assert sentences("Within 1.5 m of the car.") == ["Within 1.5 m of the car."]
    assert "detect" in tokens("detected")
    assert refers_to("An hour after the charging stop, the car cruises.", {"lounge", "stop", "charg"})
    assert not refers_to("Before she takes the steer-by-wire wheel.", {"urban", "manoeuvre", "steer", "wire"})


def test_chaque_controle_a_sa_regle_expliquee() -> None:
    from p9.checks import ALL_CHECKS, CHECK_RULES
    assert set(CHECK_RULES) == set(ALL_CHECKS)
    assert all(CHECK_RULES[k] and all(r.strip() for r in CHECK_RULES[k]) for k in CHECK_RULES)


# ------------------------------------------------------------ grille du blueprint vs triggers

def test_condition_contredite_par_la_grille(export, findings) -> None:
    rows = findings["conditions_vs_grid"]
    assert len(rows) == 1
    step = next(s for s in export.steps if s.step_id == rows[0].step_id)
    assert step.name == "Back to drive configuration"
    assert rows[0].detail["expected_state"] == "Not connected" and rows[0].detail["grid_state"] == "Charge complete"


def test_meme_evenement_reactions_differentes(export, findings) -> None:
    wheel = next(c.component_id for c in export.components if c.name == "Steer-by-wire half wheel")
    rows = [f for f in findings["trigger_reaction_conflicts"] if f.ids[0] == wheel]
    assert len(rows) == 1
    assert rows[0].detail["event"] == "Gear selector is P"
    assert {rows[0].detail["state_a"], rows[0].detail["state_b"]} == {"Stowing", "Normal ratio"}
