"""Test de l'interface Streamlit sur l'export réel (sans navigateur, sans LLM)."""

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


def test_attente_sans_fichier(app) -> None:
    assert not app.exception
    assert any("En attente" in i.value for i in app.info)


def test_export_reel_controles_verts(app) -> None:
    app.toggle[0].set_value(True).run()
    assert not app.exception, app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {"Projet": "Renault E-Space concept", "Use cases": "4", "Steps": "24", "Contrôles": "OK"}
    titles = [s.value for s in app.subheader]
    assert titles[:4] == ["1. Welcome on board", "2. Long trip with personal audio zones",
                          "3. Urban manoeuvre with steer-by-wire", "4. Lounge stop while charging"]
    # 5 fichiers générés (commun + 4 use cases) dans les onglets
    assert len([t for t in app.tabs if t.label.endswith(".sysml")]) == 5
    # diagrammes : structure, use cases, activité du use case choisi, state def choisi
    assert [t.label for t in app.tabs if not t.label.endswith(".sysml")] == [
        "Structure (part def)", "Use cases", "Activités (actions)", "Machines à états (state def)"]
    assert len(app.get("graphviz_chart")) == 4
    # points à valider : les pertes connues sont affichées
    md = " ".join(m.value for m in app.markdown)
    assert "Only below 10 km/h." in md and "front passenger" in md and "clearer distance cue" in md


def test_rien_coche(app) -> None:
    app.toggle[0].set_value(True).run()
    deselect = next(b for b in app.button if b.label == "Tout désélectionner")
    deselect.click().run()
    assert not app.exception
    assert any("Cochez au moins un use case" in w.value for w in app.warning)
    assert len(app.tabs) == 0
