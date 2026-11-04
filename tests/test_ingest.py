from __future__ import annotations

import pytest

from p9.ingest import ExportUnreadable, load_zip, sheet_name
from tests.conftest import real_files, zip_bytes

EXPECTED = {
    "use_cases": 4, "step_groups": 10, "steps": 24, "step_triggers": 29, "personas": 4,
    "use_case_personas": 9, "persona_step_feedback": 54, "components": 20, "component_states": 72,
    "use_case_components": 44, "step_component_states": 279, "step_unused_components": 0,
    "user_needs": 10, "step_user_needs": 27,
}


def test_export_reel_tous_controles_verts(export) -> None:
    r = export.report
    assert r.ok, r.summary_lines()
    assert {c.sheet: c.loaded for c in r.counts} == EXPECTED
    assert export.meta.project_name == "Renault E-Space concept"
    assert export.meta.schema_version == "1.0"


def test_export_reel_types(export) -> None:
    lea = next(p for p in export.personas if p.name.startswith("L"))
    assert lea.name == "Léa Martin"                      # UTF-8 conservé
    assert all(isinstance(s.position, int) for s in export.steps)
    assert all(isinstance(f.is_present, bool) for f in export.persona_step_feedback)
    obstacle = next(s for s in export.steps if s.name == "Obstacle close")
    fb = [f for f in export.persona_step_feedback if f.step_id == obstacle.step_id and f.emotion_score == -1]
    assert fb and fb[0].insight == "Wants a clearer distance cue."
    # cellule vide → None
    assert all(c.parent_component_id is None for c in export.components)


def test_bom_component_states_lu(export) -> None:
    assert all(s.state_id and not s.state_id.startswith("﻿") for s in export.component_states)


def test_zip_sous_dossier_et_fichiers_parasites(export_zip) -> None:
    ex = load_zip(export_zip)
    assert ex.report.ok
    assert len(ex.use_cases) == 4 and len(ex.steps) == 24


def test_zip_avec_prefixe_et_macosx() -> None:
    files = {f"proj-{n}": raw for n, raw in real_files().items()}
    files["__MACOSX/._steps.csv"] = b"junk"
    files["._components.csv"] = b"junk"
    ex = load_zip(zip_bytes(files))
    assert ex.report.ok, ex.report.summary_lines()
    assert ex.report.ignored_files == []


@pytest.mark.parametrize(("path", "expected"), [
    ("components.csv", "components"),
    ("use_case_components.csv", "use_case_components"),
    ("component_states.csv", "component_states"),
    ("step_component_states.csv", "step_component_states"),
    ("abc-components.csv", "components"),
    ("abc-use_case_components.csv", "use_case_components"),
    ("dir/sub/x-step_component_states.csv", "step_component_states"),
    ("other_components.csv", None),
    ("__MACOSX/components.csv", None),
    ("._components.csv", None),
    ("components.txt", None),
])
def test_sheet_name(path: str, expected: str | None) -> None:
    assert sheet_name(path) == expected


def test_zip_illisible() -> None:
    with pytest.raises(ExportUnreadable):
        load_zip(b"pas un zip")


def test_feuille_obligatoire_manquante() -> None:
    files = real_files()
    del files["personas.csv"]
    ex = load_zip(zip_bytes(files))
    assert "personas" in ex.report.missing_required
    assert not ex.report.ok


def test_cle_etrangere_cassee() -> None:
    files = real_files()
    files["steps.csv"] = files["steps.csv"].replace(b"c332fdf81ef4364da159b19bd", b"cXXXXXXXXXXXXXXXXXXXXXXXX", 1)
    ex = load_zip(zip_bytes(files))
    fk = {(e.sheet, e.column) for e in ex.report.fk_errors}
    assert ("steps", "use_case_id") in fk
    assert not ex.report.ok


def test_compte_different_de_meta() -> None:
    files = real_files()
    files["meta.csv"] = files["meta.csv"].replace(b"count_steps,24", b"count_steps,25")
    ex = load_zip(zip_bytes(files))
    bad = [c for c in ex.report.counts if not c.ok]
    assert [(c.sheet, c.expected, c.loaded) for c in bad] == [("steps", 25, 24)]


def test_valeur_enum_invalide_signalee_sans_crash() -> None:
    files = real_files()
    files["components.csv"] = files["components.csv"].replace(b",reactive,", b",bizarre,", 1)
    ex = load_zip(zip_bytes(files))
    assert any(e.sheet == "components" and e.line for e in ex.report.row_errors)
    assert len(ex.components) == 19


def test_position_non_contigue() -> None:
    header = b"use_case_id,position,name,description,story,status,folder\n"
    files = real_files()
    files["use_cases.csv"] = header + b"u1,1,A,,,new,\nu2,3,B,,,new,\n"
    ex = load_zip(zip_bytes(files))
    assert any(i.sheet == "use_cases" for i in ex.report.order_errors)
