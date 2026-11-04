from __future__ import annotations

import re

import pytest

from p9.checks import build_matrix
from p9.config import CatiaSettings, load_settings
from p9.generate import generate_catia
from p9.generate.catia import OUT_OF_SCOPE, TRIMS, quoted
from p9.ingest import load_dir
from tests.conftest import DATA_DIR

SETTINGS = load_settings()


@pytest.fixture(scope="module")
def catia(export):
    return {f.name: f for f in generate_catia(export, cfg=SETTINGS.generate, catia=SETTINGS.catia)}


def _refs(text: str) -> set[str]:
    return set(re.findall(r'@UserShakerRef \{ :>> id = "([^"]+)"', text))


def _uc(export, position: int):
    return next(u for u in export.use_cases if u.position == position)


def test_un_fichier_autonome_par_use_case(catia) -> None:
    assert list(catia) == [
        "UC1_WelcomeOnBoard.sysml",
        "UC2_LongTripWithPersonalAudioZones.sysml",
        "UC3_UrbanManoeuvreWithSteerByWire.sysml",
        "UC4_LoungeStopWhileCharging.sysml",
    ]
    for name, f in catia.items():
        assert f.text.startswith(f"package {name[:-6]} {{")
        assert "import ESpaceCommon" not in f.text and "ESpaceCommon::" not in f.text
        assert "//" not in f.text                    # retirés par CATIA mais comptés à la saisie


def test_sous_la_limite_de_l_editeur_catia(catia) -> None:
    for f in catia.values():
        assert f.chars <= SETTINGS.catia.char_budget <= SETTINGS.catia.char_limit, (f.name, f.chars)


def test_generation_deterministe(catia) -> None:
    again = generate_catia(load_dir(DATA_DIR), cfg=SETTINGS.generate, catia=SETTINGS.catia)
    assert {f.name: f.text for f in again} == {n: f.text for n, f in catia.items()}


def test_perimetre_du_use_case(export, catia) -> None:
    uc1 = _uc(export, 1)
    text = catia["UC1_WelcomeOnBoard.sysml"].text
    refs = _refs(text)
    steps = export.steps_of(uc1.use_case_id)
    step_ids = {s.step_id for s in steps}
    mine = {r.component_id for r in export.use_case_components if r.use_case_id == uc1.use_case_id}
    expected = (
        {uc1.use_case_id} | step_ids | {s.group_id for s in steps if s.group_id} | mine
        | {r.persona_id for r in export.use_case_personas if r.use_case_id == uc1.use_case_id}
        | {r.user_need_id for r in export.step_user_needs if r.step_id in step_ids}
    )
    assert expected <= refs
    others = {c.component_id for c in export.components} - mine
    assert others and not (others & refs)          # composants des autres use cases absents


def test_etats_sous_leur_nom_d_origine_et_transitions(catia) -> None:
    text = catia["UC1_WelcomeOnBoard.sysml"].text
    assert "state 'Welcome pulse';" in text
    assert re.search(r"transition \w+ first 'Stowed' accept CentralTouchscreenIsStartPressed "
                     r"if doorSensorsIsAllClosed and gearSelectorIsP then 'Deploying';", text)
    # seuls les états atteints dans ce use case : la roue n'y passe jamais en ratio parking
    assert "'Parking ratio'" not in text
    # l'état d'entrée est celui du premier step, pas le premier de la liste du composant
    uc3 = catia["UC3_UrbanManoeuvreWithSteerByWire.sysml"].text
    block = uc3[uc3.index("state def SteerByWireHalfWheelStates"):]
    assert block.split("\n", 2)[1].strip() == "entry; then 'Normal ratio';"


def test_vues_catia(catia) -> None:
    text = catia["UC1_WelcomeOnBoard.sysml"].text
    assert "view structureView : DS_Views::SymbolicViews::gv {" in text
    assert "view flowView : DS_Views::SymbolicViews::afv {" in text
    assert "view statesView : DS_Views::SymbolicViews::stv {" in text
    assert "view needsView : DS_Views::SymbolicViews::gv {" in text


def test_feedback_sur_une_ligne_et_points_de_douleur(catia) -> None:
    uc1 = catia["UC1_WelcomeOnBoard.sysml"].text
    assert re.search(r"comment feedback /\* Jacques Martin \(\+1\) : Waits for the rear door to open\. / [^|*]+ "
                     r"\| Camille Martin \(\+2\) : Loads the bags without putting them down\.", uc1)
    uc4 = catia["UC4_LoungeStopWhileCharging.sysml"]
    assert "feedback_non_negative" in uc4.trims and "feedback" not in uc4.trims
    assert "Bored after two hours." in uc4.text            # émotion -2 : gardé
    assert "Finds it fun, wants to do it again." not in uc4.text   # émotion +3 : retiré


def test_reductions_dans_l_ordre(export) -> None:
    uc2 = _uc(export, 2)
    tiny = generate_catia(export, [uc2.use_case_id], cfg=SETTINGS.generate,
                          catia=CatiaSettings(char_limit=20000, char_budget=1000))
    assert tiny[0].trims == list(TRIMS)
    roomy = generate_catia(export, [uc2.use_case_id], cfg=SETTINGS.generate,
                           catia=CatiaSettings(char_limit=10**6, char_budget=10**6))
    assert roomy[0].trims == []
    assert "comment story" in roomy[0].text and ":>> kind = " in roomy[0].text


def test_tracabilite_du_fichier_catia(export, catia) -> None:
    uc4 = _uc(export, 4)
    f = catia["UC4_LoungeStopWhileCharging.sysml"]
    matrix = build_matrix(export, f.gen)
    by = {(e.kind, e.usershaker_id): e for e in matrix}
    step_ids = {s.step_id for s in export.steps_of(uc4.use_case_id)}
    # tout ce qui appartient au use case est traduit ou partiel, jamais perdu en silence
    for s in step_ids:
        assert by[("step", s)].status in {"mapped", "partial"}
    for r in export.step_component_states:
        if r.step_id in step_ids:
            assert by[("step_component_state", f"{r.step_id}:{r.component_id}:{r.sequence}")].status == "mapped"
    # les réductions sont expliquées
    for fb in export.persona_step_feedback:
        if fb.step_id in step_ids:
            e = by[("persona_step_feedback", fb.feedback_id)]
            assert (e.status == "partial") == (fb.emotion_score is not None and fb.emotion_score < 0)
            assert e.reason
    # le hors-périmètre est signalé comme tel
    mine = {r.component_id for r in export.use_case_components if r.use_case_id == uc4.use_case_id}
    for c in export.components:
        if c.component_id not in mine:
            assert by[("component", c.component_id)].reason == OUT_OF_SCOPE


def test_nom_quote() -> None:
    assert quoted("Lounge position (180°)") == "'Lounge position (180°)'"
    assert quoted("it's") == "'it\\'s'"
