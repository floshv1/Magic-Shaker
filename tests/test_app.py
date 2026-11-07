"""Test de l'interface Streamlit (parcours Importer → Choisir → Résultats) sur l'export réel, sans LLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "app" / "app.py")


@pytest.fixture
def app(monkeypatch, tmp_path) -> AppTest:
    monkeypatch.setenv("P9_OUT_DIR", str(tmp_path))          # pas d'écriture dans out/
    monkeypatch.setenv("P9_REVIEW_FILE", str(tmp_path / "review" / "decisions.json"))   # ni dans review/
    monkeypatch.setenv("P9_PROPOSALS_FILE", str(tmp_path / "review" / "llm_proposals.json"))
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
    return _ok(app.button(key="nav_translate").click().run())


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
    assert app.button(key="nav_translate").disabled


def test_selection_conservee_entre_les_etapes(app) -> None:
    _translate_all(app)
    _ok(_button(app, "Modifier la sélection").click().run())
    _ok(app.checkbox[0].uncheck().run())
    _ok(_button(app, "Retour").click().run())
    _ok(_button(app, "Continuer").click().run())
    assert app.checkbox[0].value is False and app.checkbox[1].value is True
    assert app.button(key="nav_translate").label == "Traduire 3 use cases"


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
    assert md.count("Règle appliquée") >= 3 and "kind = condition" in md     # règles affichées


def test_changer_de_fichier_revient_a_l_import(app) -> None:
    _load(app)
    _ok(_button(app, "Changer de fichier").click().run())
    assert "En attente" in _md(app)


def _open_review(app: AppTest) -> AppTest:
    _translate_all(app)
    tab = next(t.label for t in app.tabs if t.label.startswith("Points à valider"))
    app.session_state["result_tab"] = tab
    return _ok(app.run())


def _speed_finding_id(export) -> str:
    from p9.checks import finding_id, run_all
    f = next(f for f in run_all(export)["constraints_in_text_only"] if "10 km/h" in f.message)
    return finding_id(f)


def test_decision_sans_nom_refusee(app, export) -> None:
    _open_review(app)
    fid = _speed_finding_id(export)
    _ok(app.button(key=f"rv_save_{fid}").click().run())
    assert any("indiquez votre nom" in e.value for e in app.error)


def test_ajouter_une_garde_puis_annuler(app, export, tmp_path) -> None:
    from p9.review import load_review
    _open_review(app)
    fid = _speed_finding_id(export)
    total = len(load_review(tmp_path / "review" / "decisions.json").decisions)
    app.text_input(key="rv_author").set_value("Testeur")
    _ok(app.run())
    assert app.radio(key=f"rv_mode_{fid}").value.startswith("Condition décrite en texte")   # phrase avec un chiffre
    app.text_area(key=f"rv_text_{fid}").set_value("vitesse du véhicule < 10 km/h")
    _ok(app.button(key=f"rv_save_{fid}").click().run())
    saved = load_review(tmp_path / "review" / "decisions.json")
    d = saved.get(fid)
    assert len(saved.decisions) == total + 1 and d.action == "guard" and d.author == "Testeur"
    md = _md(app)
    assert "Garde ajoutée" in md and "par Testeur" in md
    # la traçabilité suit : la contrainte n'est plus « partielle »
    app.session_state["result_tab"] = "Fichiers .sysml"
    _ok(app.run())
    assert any(t.label == "ESpaceConceptReview.sysml" for t in app.tabs)
    tab = next(t.label for t in app.tabs if t.label.startswith("Points à valider"))
    app.session_state["result_tab"] = tab
    _ok(app.run())
    _ok(app.button(key=f"rv_undo_{fid}").click().run())
    assert load_review(tmp_path / "review" / "decisions.json").get(fid) is None


def test_fichier_de_decisions_illisible_jamais_ecrase(app, export, tmp_path) -> None:
    path = tmp_path / "review" / "decisions.json"
    path.parent.mkdir(parents=True)
    path.write_text('<<<<<<< HEAD\n{"decisions": []}\n=======\n{"decisions": []}\n>>>>>>> autre\n', encoding="utf-8")
    before = path.read_bytes()
    _open_review(app)
    assert any("illisible" in e.value for e in app.error)
    fid = _speed_finding_id(export)
    assert app.button(key=f"rv_save_{fid}").disabled
    # fichier abîmé pendant que la page est ouverte (git pull en conflit) : le clic n'écrit rien non plus
    path.write_text('{"decisions": []}', encoding="utf-8")
    app.text_input(key="rv_author").set_value("Testeur")
    _ok(app.run())
    path.write_bytes(before)
    _ok(app.button(key=f"rv_save_{fid}").click().run())
    assert path.read_bytes() == before
    assert any("Décision non enregistrée" in e.value and "illisible" in e.value for e in app.error)


def test_proposition_llm_affichee_sans_image(app, export, tmp_path) -> None:
    """Une image markdown dans un texte du LLM ferait charger une URL choisie par le modèle : elle est neutralisée."""
    from p9.llm import Proposal, ProposalStore, save_store
    step = next(s for s in export.steps if s.name == "Parking ratio")
    p = Proposal(kind="guard", title="Vitesse sous 10 km/h", text="Voir ![x](http://example.invalid/p.png) ici.",
                 rationale="trigger_text", target_ids=[step.step_id], proposal_id="abc123def456")
    save_store(ProposalStore(proposals=[p]), tmp_path / "review" / "llm_proposals.json")
    _open_review(app)
    md = _md(app)
    assert "Voir !\\[x](http://example.invalid/p.png) ici." in md     # liste des propositions + carte du point
    assert "![x](" not in md


def test_ecarter_exige_une_raison(app, export, tmp_path) -> None:
    from p9.review import load_review
    _open_review(app)
    fid = _speed_finding_id(export)
    app.text_input(key="rv_author").set_value("Testeur")
    _ok(app.run())
    app.radio(key=f"rv_act_{fid}").set_value("Écarter ce point")
    _ok(app.run())
    _ok(app.button(key=f"rv_save_{fid}").click().run())
    assert any("raison" in e.value for e in app.error)
    app.text_area(key=f"rv_reason_{fid}").set_value("la vitesse est gérée hors parcours")
    _ok(app.button(key=f"rv_save_{fid}").click().run())
    assert load_review(tmp_path / "review" / "decisions.json").get(fid).action == "dismiss"
    assert "Écarté" in _md(app)
