from __future__ import annotations

import re
from pathlib import Path

import pytest

from p9.generate import generate, write
from p9.generate.naming import Namer, ident
from p9.ingest import load_dir
from tests.conftest import DATA_DIR

SRC = Path(__file__).parents[1] / "src" / "p9"


@pytest.fixture(scope="module")
def gen(export):
    return generate(export)


def test_fichiers_attendus(gen) -> None:
    assert list(gen.files) == [
        "ESpaceCommon.sysml",
        "UC1_WelcomeOnBoard.sysml",
        "UC2_LongTripWithPersonalAudioZones.sysml",
        "UC3_UrbanManoeuvreWithSteerByWire.sysml",
        "UC4_LoungeStopWhileCharging.sysml",
    ]
    for name, text in gen.files.items():
        assert text.startswith(f"package {name[:-6]} {{")
    for name in list(gen.files)[1:]:
        assert "private import ESpaceCommon::*;" in gen.files[name]


def test_generation_deterministe(export, gen) -> None:
    again = generate(load_dir(DATA_DIR))
    assert again.files == gen.files
    assert again.single_file == gen.single_file
    assert again.digest() == gen.digest()


def test_ecriture_octet_par_octet(gen, tmp_path) -> None:
    write(gen, tmp_path / "a")
    write(gen, tmp_path / "b")
    for p in sorted((tmp_path / "a" / "sysml").glob("*.sysml")):
        assert p.read_bytes() == (tmp_path / "b" / "sysml" / p.name).read_bytes()
        assert b"\r\n" not in p.read_bytes()


def _refs(text: str) -> set[str]:
    return set(re.findall(r'@UserShakerRef \{ id = "([^"]+)"', text))


def test_chaque_element_porte_son_id(export, gen) -> None:
    refs = set().union(*(_refs(t) for t in gen.files.values()))
    ids = (
        [u.use_case_id for u in export.use_cases] + [g.group_id for g in export.step_groups]
        + [s.step_id for s in export.steps] + [p.persona_id for p in export.personas]
        + [c.component_id for c in export.components] + [s.state_id for s in export.component_states]
        + [u.user_need_id for u in export.user_needs] + [f.feedback_id for f in export.persona_step_feedback]
        + [f"{r.use_case_id}:{r.persona_id}" for r in export.use_case_personas]
        + [f"{r.use_case_id}:{r.component_id}" for r in export.use_case_components]
        + [f"{r.step_id}:{r.user_need_id}" for r in export.step_user_needs]
    )
    missing = [i for i in ids if i not in refs]
    assert not missing


def test_use_case_structure(gen) -> None:
    uc1 = gen.files["UC1_WelcomeOnBoard.sysml"]
    assert "use case def WelcomeOnBoard {" in uc1
    assert "subject vehicle : ESpace;" in uc1
    assert "actor camilleMartin : Persona :> ESpaceCommon::camilleMartin" in uc1
    # phases dans l'ordre, steps enchaînés first/then
    assert uc1.index("action approachPhase") < uc1.index("action boardingPhase") < uc1.index("action departurePhase")
    assert re.search(r"first start;\s+then action approach \{", uc1)
    assert "then done;" in uc1
    assert "dependency from WelcomeOnBoard::approachPhase::approach to ESpaceCommon::" in uc1


def test_texte_libre_conserve(gen) -> None:
    uc3 = gen.files["UC3_UrbanManoeuvreWithSteerByWire.sysml"]
    assert "comment triggerText /* Camille selects R. Only below 10 km/h. */" in uc3


def test_feedback_et_emotion(gen) -> None:
    uc3 = gen.files["UC3_UrbanManoeuvreWithSteerByWire.sysml"]
    block = uc3[uc3.index("action obstacleClose"):]
    assert "Wants a clearer distance cue." in block[:3000]
    assert ":>> emotionScore = -1;" in block[:3000]


def test_etats_et_transitions(gen) -> None:
    common = gen.files["ESpaceCommon.sysml"]
    assert "state def DoorsStates {" in common
    assert "entry; then locked;" in common
    assert "exhibit state states : DoorsStates;" in common
    # accept = événement du step ; if = condition structurée
    assert re.search(r"transition \w+\s+first \w+\s+accept ESpaceCommon::CentralTouchscreenIsStartPressed\s+"
                     r"if doorSensorsIsAllClosed and gearSelectorIsP", common)


def test_selection_partielle(export) -> None:
    uc2 = next(u for u in export.use_cases if u.position == 2)
    g = generate(export, [uc2.use_case_id])
    assert list(g.files) == ["ESpaceCommon.sysml", "UC2_LongTripWithPersonalAudioZones.sysml"]
    # transitions uniquement issues de UC2
    assert "UC1 'Welcome on board'" not in g.files["ESpaceCommon.sysml"]
    assert "UC2 'Long trip" in g.files["ESpaceCommon.sysml"]


def test_phase_non_contigue_signalee(export) -> None:
    """Steps d'une phase séparés par une autre phase : deux actions, et la scission est dans la traçabilité."""
    uc1 = next(u for u in export.use_cases if u.position == 1)
    steps = export.steps_of(uc1.use_case_id)
    first = steps[0].group_id
    k = next(i for i, s in enumerate(steps) if s.group_id != first) + 1    # step après le début de la 2e phase
    moved = steps[k].model_copy(update={"group_id": first})
    ex = export.model_copy(update={"steps": [moved if s.step_id == moved.step_id else s for s in export.steps]})
    g = generate(ex, [uc1.use_case_id])
    assert re.findall(r"then action (approachPhase\d*)", g.files["UC1_WelcomeOnBoard.sysml"]) == [
        "approachPhase", "approachPhase2"]
    e = next(e for e in g.trace if e.kind == "step_group" and e.usershaker_id == first)
    assert "groupe non contigu : scinde en 2 actions (approachPhase, approachPhase2)" in e.reason


def test_fichier_unique(gen) -> None:
    assert gen.single_file.startswith("package ESpaceConcept {")
    assert gen.single_file.count("\npackage ") == 5


def test_echappement_chaines() -> None:
    from p9.generate.render import comment_block, doc_block, sysml_string
    assert sysml_string('a "b" \\ c\nd') == '"a \\"b\\" \\\\ c\\nd"'
    assert doc_block("x */ y", "") == "doc /* x * / y */"
    assert comment_block("l1\nl2", "n", "  ") == "  comment n\n  /*\n   * l1\n   * l2\n   */"


@pytest.mark.parametrize(("text", "upper", "expected"), [
    ("Léa starts a game", False, "leaStartsAGame"),
    ("Léa starts a game", True, "LeaStartsAGame"),
    ("openR panorama screen", True, "OpenrPanoramaScreen"),
    ("Lounge position (180°)", False, "loungePosition180"),
    ("P", False, "p"),
    ("2nd row", False, "_2ndRow"),
    ("State", False, "state_"),
    ("MVP · Lounge", False, "mvpLounge"),
    ("MVP · Lounge", True, "MVPLounge"),
    ("", False, "x"),
])
def test_ident(text: str, upper: bool, expected: str) -> None:
    assert ident(text, upper) == expected


def test_namer_collisions() -> None:
    n = Namer({"start"})
    assert [n.name("Start"), n.name("start"), n.name("Doors"), n.name("Doors")] == ["start_", "start_2", "doors", "doors2"]


def test_pas_d_id_ni_de_nom_du_dataset_dans_le_code(export) -> None:
    """Le générateur doit marcher sur n'importe quel export : aucun id ni nom codé en dur."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    code += "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.j2"))
    ids = [s.step_id for s in export.steps] + [c.component_id for c in export.components] + \
          [p.persona_id for p in export.personas] + [u.use_case_id for u in export.use_cases]
    assert not [i for i in ids if i in code]
    names = [p.name for p in export.personas] + [u.name for u in export.use_cases] + \
            [c.name for c in export.components if len(c.name) > 6]
    assert not [n for n in names if n in code]
