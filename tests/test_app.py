"""Test de l'interface Streamlit (parcours Importer → Choisir → Résultats) sur l'export réel, sans LLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "app" / "app.py")


@pytest.fixture
def app(monkeypatch, tmp_path) -> AppTest:
    monkeypatch.setenv("P9_OUT_DIR", str(tmp_path))          # pas d'écriture dans out/
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("P9_LLM_PROVIDER", "anthropic")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    return at


def _button(app: AppTest, prefix: str):
    return next(b for b in app.button if b.label.startswith(prefix))


def _ok(app: AppTest) -> AppTest:
    assert not app.exception, app.exception
    return app


def _load(app: AppTest) -> AppTest:
    return _ok(_button(app, "Utiliser l'export de démonstration").click().run())


def _translate_all(app: AppTest) -> AppTest:
    _load(app)
    _ok(_button(app, "Continuer").click().run())
    return _ok(app.button(key="translate").click().run())


def _md(app: AppTest) -> str:
    return " ".join(m.value for m in app.markdown)


def test_attente_sans_fichier(app) -> None:
    _ok(app)
    assert "En attente" in _md(app)
    assert app.session_state["step"] == 1


def test_export_reel_controles_verts(app) -> None:
    _load(app)
    md = _md(app)
    assert "Renault E-Space concept" in md and "Contrôles OK" in md
    assert "<b>4</b><span>use cases" in md and "<b>24</b><span>steps" in md


def test_rien_n_est_traduit_avant_le_clic(app) -> None:
    _load(app)
    _ok(_button(app, "Continuer").click().run())
    assert app.session_state["step"] == 2
    assert "translated" not in app.session_state
    assert [c.label for c in app.checkbox] == [
        "1. Welcome on board", "2. Long trip with personal audio zones",
        "3. Urban manoeuvre with steer-by-wire", "4. Lounge stop while charging"]


def test_traduction_puis_resultats(app) -> None:
    _translate_all(app)
    assert app.session_state["step"] == 3
    md = _md(app)
    assert "Votre modèle SysML v2 est prêt" in md
    assert "aucun n'est perdu" in md                    # bandeau de traçabilité
    assert len(app.get("iframe")) == 1                  # diagramme dans la visionneuse
    app.session_state["result_tab"] = "Fichiers .sysml"
    _ok(app.run())
    assert len([t for t in app.tabs if t.label.endswith(".sysml") and " " not in t.label]) == 5


def test_rien_coche(app) -> None:
    _load(app)
    _ok(_button(app, "Continuer").click().run())
    _ok(_button(app, "Tout décocher").click().run())
    assert any("Cochez au moins un use case" in w.value for w in app.warning)
    assert app.button(key="translate").disabled


def test_selection_conservee_entre_les_etapes(app) -> None:
    _translate_all(app)
    _ok(_button(app, "Modifier la sélection").click().run())
    _ok(app.checkbox[0].uncheck().run())
    _ok(_button(app, "Retour").click().run())
    _ok(_button(app, "Continuer").click().run())
    assert app.checkbox[0].value is False and app.checkbox[1].value is True
    assert app.button(key="translate").label == "Traduire 3 use cases"


def test_toutes_les_vues_de_diagramme(app) -> None:
    _translate_all(app)
    for view in ("Use cases", "Structure", "Machine à états", "Activité"):
        app.session_state["diag_kind"] = view
        _ok(app.run())
        assert len(app.get("iframe")) == 1


def test_points_a_valider(app) -> None:
    _translate_all(app)
    tab = next(t.label for t in app.tabs if t.label.startswith("Points à valider"))
    app.session_state["result_tab"] = tab
    _ok(app.run())
    md = _md(app)
    assert "Only below 10 km/h." in md and "front passenger" in md and "clearer distance cue" in md


def test_changer_de_fichier_revient_a_l_import(app) -> None:
    _load(app)
    _ok(_button(app, "Changer de fichier").click().run())
    assert "En attente" in _md(app)
