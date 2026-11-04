"""Diagrammes d'aperçu : déterministes et alignés sur le modèle généré (mêmes noms SysML)."""

from __future__ import annotations

import re

from p9.generate import build_diagrams, generate


def test_deterministe(export) -> None:
    a, b = build_diagrams(export), build_diagrams(export)
    assert a.structure.dot == b.structure.dot
    assert [d.dot for d in a.activities.values()] == [d.dot for d in b.activities.values()]
    assert [d.dot for d in a.states.values()] == [d.dot for d in b.states.values()]


def test_une_vue_activite_par_use_case_selectionne(export) -> None:
    gen = generate(export)
    d = build_diagrams(export)
    assert [f"{p}.sysml" for p in d.activities] == list(gen.files)[1:]
    one = [u.use_case_id for u in export.use_cases_sorted()][:1]
    assert len(build_diagrams(export, one).activities) == 1


def test_noms_identiques_au_sysml(export) -> None:
    gen = generate(export)
    d = build_diagrams(export)
    for pkg, view in d.activities.items():
        src = gen.files[f"{pkg}.sysml"]
        for name in re.findall(r'"a_(\w+)"', view.dot):
            assert re.search(rf"\baction {name}\b", src), (pkg, name)
    common = next(iter(gen.files.values()))
    for name, view in d.states.items():
        assert f"state def {name}" in common
        found = re.findall(r'^\s*"t_(\w+)" \[', view.dot, re.M)
        assert len(found) == view.transitions
        for t in found:
            assert re.search(rf"\btransition {t}\b", common), (name, t)


def test_steps_relies_dans_l_ordre_et_ids_en_survol(export) -> None:
    d = build_diagrams(export)
    for uc in export.use_cases_sorted():
        view = next(v for p, v in d.activities.items() if p.startswith(f"UC{uc.position}_"))
        steps = export.steps_of(uc.use_case_id)
        assert view.dot.count("«action»") >= len(steps)
        for s in steps:
            assert s.step_id in view.dot          # UserShakerRef visible au survol


def test_requirements_optionnels(export) -> None:
    with_r = build_diagrams(export, with_requirements=True)
    without = build_diagrams(export, with_requirements=False)
    assert any("requirement def" in v.dot for v in with_r.activities.values())
    assert not any("requirement def" in v.dot for v in without.activities.values())


def test_use_cases_et_structure(export) -> None:
    d = build_diagrams(export)
    assert d.use_cases.dot.count("«use case def»") == len(export.use_cases)
    assert d.structure.dot.count("«part def»") == len(export.components) + 1
    assert sum(v.transitions for v in d.states.values()) > 0      # transitions déduites
