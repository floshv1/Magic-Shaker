"""Interface de démo : export UserShaker (.zip) → use cases → sélection → fichiers SysML v2.

Lancement : `make demo` (équivalent de `streamlit run app/app.py`, thème dans .streamlit/config.toml).
Aucune logique métier ici : l'interface appelle uniquement src/p9 (ingest, generate, checks, llm).

Parcours guidé en trois étapes : Importer → Choisir → Résultats. La traduction ne part qu'au clic
sur « Traduire ». Les détails techniques (contrôles, matrice, parseur OMG) sont repliés.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from collections.abc import Callable
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from p9.checks import ADDED_KINDS, CHECK_RULES, build_matrix, coverage, matrix_csv, run_all
from p9.checks import sysml_parser
from p9.config import load_settings
from p9.progress import estimate, record
from p9.review import (
    ACTION_LABELS,
    ACTIONS_BY_CHECK,
    Decision,
    ReviewFileError,
    ReviewStore,
    decision_errors,
    load_review,
    review_csv,
    review_state,
    save_review,
)
from p9.generate import Diagram, build_diagrams, generate, generate_catia, make_zip
from p9.ingest import REQUIRED_SHEETS, Export, ExportUnreadable, load_dir, load_zip, use_case_summaries
from p9.llm import (
    LlmUnavailable,
    ProposalFileError,
    ProposalStore,
    ToolContext,
    ask,
    enrich,
    load_store,
    make_provider,
    provider_status,
    save_store,
)

APP_DIR = Path(__file__).resolve().parent
SETTINGS = load_settings(APP_DIR)
PROPOSALS_PATH = SETTINGS.out_dir / "proposals.json"
STEPS = ["Importer", "Choisir", "Résultats"]
RESULT_TABS = ["Diagrammes", "Fichiers .sysml", "Points à valider", "Assistant"]

st.set_page_config(page_title="UserShaker → SysML v2", layout="wide")
st.markdown("""
<style>
:root{--paper:#F4F6F8;--surface:#FFFFFF;--ink:#17233B;--ink-soft:#5B6B82;--line:#D3DAE3;
      --trace:#2148B8;--signal:#D99A0B;--ok:#2F7D52;--loss:#B42318}
.block-container{max-width:1160px;padding-top:2.2rem;padding-bottom:4rem}
[data-testid="stHeader"]{background:transparent}
h1,h2,h3{letter-spacing:-0.01em}

/* en-tête */
.p9-brand{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:22px}
.p9-brand b{font-size:15px;font-weight:600;color:var(--ink)}
.p9-brand span{font-size:14px;color:var(--ink-soft)}

/* indicateur d'étapes : une ligne de traçabilité */
.p9-steps{display:flex;list-style:none;margin:0 0 30px;padding:0;counter-reset:s}
.p9-steps li{flex:1;position:relative;display:flex;gap:12px;align-items:flex-start;padding-right:16px}
.p9-steps li::after{content:"";position:absolute;left:40px;right:12px;top:15px;height:2px;background:var(--line)}
.p9-steps li:last-child::after{display:none}
.p9-steps li.done::after{background:var(--trace)}
.p9-steps .dot{flex:none;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-size:14px;
  font-weight:600;border:2px solid var(--line);background:var(--surface);color:var(--ink-soft);position:relative;z-index:1}
.p9-steps .txt{background:var(--paper);position:relative;z-index:1;padding:0 12px 0 10px;margin-left:-12px}
.p9-steps .txt b{display:block;font-size:15px;font-weight:500;color:var(--ink-soft)}
.p9-steps .txt small{display:block;font-size:13px;color:var(--ink-soft);margin-top:1px}
.p9-steps li.current .dot{border-color:var(--trace);background:var(--trace);color:#fff}
.p9-steps li.current .txt b{color:var(--ink);font-weight:600}
.p9-steps li.done .dot{border-color:var(--trace);color:var(--trace)}
.p9-steps li.done .txt b{color:var(--ink)}

/* titres d'étape */
[data-testid="stMarkdownContainer"] p.p9-lead{font-size:26px;font-weight:600;color:var(--ink);margin:0 0 6px;
  line-height:1.25;letter-spacing:-0.01em}
[data-testid="stMarkdownContainer"] p.p9-sub{font-size:15.5px;color:var(--ink-soft);margin:0 0 18px;max-width:68ch;
  line-height:1.5}

/* zone de dépôt */
[data-testid="stFileUploaderDropzone"]{background:var(--surface);border:1.5px dashed #AEB9C7;padding:34px 24px}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--trace)}
.p9-hint{font-size:13.5px;color:var(--ink-soft);margin-top:4px}

/* résumé de l'export */
.p9-facts{display:flex;flex-wrap:wrap;gap:28px;margin:6px 0 4px}
.p9-facts div b{display:block;font-size:24px;font-weight:600;color:var(--ink);line-height:1.1}
.p9-facts div span{font-size:13px;color:var(--ink-soft)}
.p9-check{display:flex;gap:10px;align-items:center;font-size:15px;margin:14px 0 2px}
.p9-check i{font-style:normal;width:22px;height:22px;border-radius:50%;display:grid;place-items:center;
  color:#fff;font-size:13px;background:var(--ok)}
.p9-check.warn i{background:var(--signal)}

/* lignes de use case */
[class*="st-key-ucrow_"]{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 18px 6px}
[class*="st-key-ucrow_"] [data-testid="stCheckbox"] p{font-size:17px;font-weight:600;color:var(--ink)}
.p9-desc{font-size:14px;color:var(--ink-soft);margin:-4px 0 8px 28px;max-width:72ch;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.p9-meta{font-size:13px;color:var(--ink-soft);margin:2px 0 6px}
.p9-meta b{color:var(--ink);font-weight:500}
.pill{display:inline-block;font-size:12.5px;padding:1px 9px;border-radius:10px;margin:0 4px 4px 0;border:1.5px solid;
  background:var(--surface)}
.p9-phases{font-size:13px;color:var(--ink-soft)}

/* boutons de navigation : un bouton resté d'une étape précédente pendant le rechargement (élément
   « périmé » de Streamlit) n'a plus de sens, on le masque au lieu de l'afficher grisé */
[data-testid="stElementContainer"][data-stale="true"][class*="st-key-nav_"]{display:none}

/* barre d'action */
[class*="st-key-actionbar"]{border-top:1px solid var(--line);padding-top:16px;margin-top:10px}

/* bandeau de traçabilité (le point fort de la page) */
.p9-ledger{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--ok);
  padding:20px 24px 16px;margin:0 0 18px}
.p9-ledger.has-loss{border-left-color:var(--loss)}
[data-testid="stMarkdownContainer"] .p9-ledger p{font-size:20px;font-weight:600;color:var(--ink);margin:0 0 14px;max-width:62ch;line-height:1.3}
.p9-bar{display:flex;height:12px;gap:2px;margin-bottom:10px}
.p9-bar span{display:block;min-width:3px}
.p9-bar .m{background:var(--ok)} .p9-bar .p{background:var(--signal)} .p9-bar .n{background:var(--loss)}
.p9-legend{display:flex;flex-wrap:wrap;gap:22px;font-size:13.5px;color:var(--ink-soft)}
.p9-legend b{color:var(--ink);font-weight:600;font-size:15px;margin-right:4px}
.p9-legend i{display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:0}

/* points à valider, propositions */
.p9-rules{font-style:italic;color:var(--ink-soft);font-size:13px;line-height:1.45;margin:2px 0 8px;
  padding:6px 0 6px 12px;border-left:2px solid var(--line)}
.p9-rules span{font-weight:500}
.p9-rules ol{margin:2px 0 0;padding-left:18px}
.p9-rules li{margin:1px 0}
.p9-decision{font-size:13.5px;color:var(--ink);background:#F7F9FB;border-left:3px solid var(--ok);
  padding:6px 10px;margin:6px 0}
.p9-finding{padding:8px 0;border-bottom:1px solid var(--line);font-size:14.5px}
.p9-finding:last-child{border-bottom:none}
.ids{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--ink-soft);word-break:break-all}
.badge{display:inline-block;font-size:12px;padding:1px 9px;border-radius:10px;background:#E9EDF2;margin:0 4px 4px 0}
.muted{color:var(--ink-soft);font-size:13.5px}

.stTabs [data-baseweb="tab"]{font-size:15px;padding:8px 4px;margin-right:18px}
@media (max-width:720px){.p9-steps .txt small{display:none}.p9-lead{font-size:22px}}
</style>
""", unsafe_allow_html=True)


# ============================================================ cache (clé = contenu du zip)

@st.cache_data(show_spinner=False)
def cached_load(data: bytes) -> Export:
    return load_zip(data)


@st.cache_data(show_spinner=False)
def cached_load_repo(path: str) -> Export:
    return load_dir(path)


@st.cache_data(show_spinner=False)
def cached_findings(key: str, _ex: Export) -> dict:
    return run_all(_ex)


@st.cache_data(show_spinner="Traduction en SysML v2…")
def cached_generate(key: str, _ex: Export, selected: tuple[str, ...], accepted_json: str, review_json: str):
    store = ProposalStore.model_validate_json(accepted_json)
    decisions = ReviewStore.model_validate_json(review_json).decisions
    gen = generate(_ex, list(selected), SETTINGS.generate, accepted_proposals=store.proposals,
                   review_decisions=decisions)
    findings = run_all(_ex)
    matrix = build_matrix(_ex, gen, findings["constraints_in_text_only"], decisions)
    return gen, matrix, coverage(_ex, gen, matrix), matrix_csv(matrix)


@st.cache_data(show_spinner="Fichiers CATIA…")
def cached_catia(key: str, _ex: Export, selected: tuple[str, ...]):
    return generate_catia(_ex, list(selected), SETTINGS.generate, SETTINGS.catia)


@st.cache_data(show_spinner="Dessin des diagrammes…")
def cached_diagrams(key: str, _ex: Export, selected: tuple[str, ...], with_requirements: bool):
    return build_diagrams(_ex, list(selected), SETTINGS.generate, with_requirements=with_requirements)


@st.cache_resource(show_spinner="Préparation des outils de l'agent…")
def cached_tool_context(key: str, _ex: Export) -> ToolContext:
    return ToolContext(_ex)


@st.cache_resource(show_spinner=False)
def viewer_assets() -> tuple[str, str]:
    assets = APP_DIR / "assets"
    return (assets / "viewer.html").read_text(encoding="utf-8"), (assets / "viz-global.js").read_text(encoding="utf-8")


# ============================================================ helpers d'affichage

def html(text: str) -> None:
    st.markdown(text, unsafe_allow_html=True)


def badges(*texts: str) -> str:
    return "".join(f"<span class='badge'>{escape(t)}</span>" for t in texts)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def persist(key: str, default):
    """Garde la valeur d'un widget quand il n'est pas affiché (Streamlit l'oublierait)."""
    saved = st.session_state.setdefault("_ui", {})
    if key not in st.session_state:
        st.session_state[key] = saved.get(key, default)
    return key


def remember(key: str) -> None:
    st.session_state.setdefault("_ui", {})[key] = st.session_state[key]


def go(step: int) -> None:
    st.session_state["step"] = step


def diagram_viewer(d: Diagram, height: int = 620) -> None:
    """Visionneuse autonome (viz.js embarqué, hors ligne) : zoom, déplacement, plein écran, nouvel onglet, SVG."""
    page, viz = viewer_assets()
    as_js = lambda v: json.dumps(v).replace("</", "<\\/")   # noqa: E731
    doc = (page.replace("__VIZ_JS__", viz.replace("</script", "<\\/script"))
               .replace("__DOT_JSON__", as_js(d.dot)).replace("__TITLE_JSON__", as_js(d.title)))
    st.iframe(doc, height=height)


TIMINGS = SETTINGS.out_dir / "timings.json"
DEFAULT_SECONDS = {"omg": 60.0, "enrich": 60.0, "chat": 30.0}


def llm_key(op: str) -> str:
    llm = SETTINGS.llm
    model = llm.anthropic_model if llm.provider == "anthropic" else llm.ollama_model
    return f"{op}:{llm.provider}:{model}"


def run_live(title: str, timing_key: str, work: Callable[[Callable[[str, str], None]], object]):
    """Lance `work(on_event)` dans un fil séparé et affiche en direct : étapes, outils, tokens, temps écoulé
    et estimation (médiane des exécutions précédentes, sinon valeur indicative). Relance l'exception éventuelle."""
    events: queue.Queue[tuple[str, str]] = queue.Queue()
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["result"] = work(lambda kind, text: events.put((kind, text)))
        except BaseException as e:  # noqa: BLE001 (relancée dans le fil principal)
            box["error"] = e

    measured = estimate(TIMINGS, timing_key)
    est = measured or DEFAULT_SECONDS.get(timing_key.split(":", 1)[0], 30.0)
    basis = "d'après les exécutions précédentes" if measured else "estimation indicative, affinée après ce premier essai"
    worker = threading.Thread(target=target, daemon=True)
    t0 = time.monotonic()
    worker.start()
    log: list[str] = []
    live = ""
    with st.status(title, expanded=True) as status:
        bar = st.progress(0.0)
        lines = st.empty()
        while True:
            while not events.empty():
                kind, text = events.get()
                if kind == "tokens":
                    live = text
                else:
                    log.append(text)
                    live = ""
            elapsed = time.monotonic() - t0
            if elapsed <= est:
                bar.progress(min(elapsed / est, 0.97),
                             text=f"{elapsed:.0f} s écoulées sur environ {est:.0f} s ({basis})")
            else:
                bar.progress(0.97, text=f"{elapsed:.0f} s écoulées : plus long que prévu (environ {est:.0f} s), "
                                        "toujours en cours")
            shown = [f"- {escape(t)}" for t in log[-10:]]
            if live:
                shown.append(f"- *{escape(live)}*")
            lines.markdown("\n".join(shown) or "- Démarrage…")
            if not worker.is_alive() and events.empty():
                break
            worker.join(0.4)
        elapsed = time.monotonic() - t0
        if "error" in box:
            status.update(label=f"{title} : échec après {elapsed:.0f} s", state="error", expanded=True)
            raise box["error"]  # type: ignore[misc]
        record(TIMINGS, timing_key, elapsed)
        bar.progress(1.0, text=f"Terminé en {elapsed:.0f} s")
        status.update(label=f"{title} : terminé en {elapsed:.0f} s", state="complete", expanded=False)
    return box.get("result")


def review_json_pretty(store: ReviewStore) -> str:
    return json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def stepper(current: int, notes: list[str]) -> None:
    items = []
    for i, (name, note) in enumerate(zip(STEPS, notes), start=1):
        state = "done" if i < current else "current" if i == current else "todo"
        dot = "✓" if state == "done" else str(i)
        items.append(f"<li class='{state}'><span class='dot'>{dot}</span><span class='txt'><b>{name}</b>"
                     f"<small>{escape(note)}</small></span></li>")
    html("<ol class='p9-steps'>" + "".join(items) + "</ol>")


# ============================================================ source (zip déposé ou export du dépôt)

def _on_upload() -> None:
    f = st.session_state.get("upload")
    if f is not None:
        st.session_state["source"] = {"kind": "zip", "name": f.name, "data": f.getvalue()}
        st.session_state.pop("translated", None)


def _use_repo() -> None:
    st.session_state["source"] = {"kind": "repo"}
    st.session_state.pop("translated", None)


def _reset() -> None:
    for k in ("source", "translated", "upload"):
        st.session_state.pop(k, None)
    go(1)


st.session_state.setdefault("step", 1)
source = st.session_state.get("source")
ex: Export | None = None
load_error = None
export_key = ""
try:
    if source and source["kind"] == "zip":
        ex = cached_load(source["data"])
        export_key = hashlib.sha256(source["data"]).hexdigest()
    elif source and source["kind"] == "repo":
        ex = cached_load_repo(str(SETTINGS.data_dir))
        export_key = "repo:" + hashlib.sha256(ex.model_dump_json().encode()).hexdigest()
except ExportUnreadable as e:
    load_error = f"Ce fichier n'a pas pu être lu : {e}. Déposez l'export UserShaker zippé, tel que téléchargé."
except Exception as e:  # jamais de stack trace pour l'utilisateur
    load_error = f"Ce fichier n'a pas pu être lu ({type(e).__name__}). Il est peut-être corrompu : réexportez-le."

usable = ex is not None and not ex.report.missing_required
if not usable:
    st.session_state["step"] = 1

summaries = use_case_summaries(ex) if usable else []
sel_store: dict[str, bool] = (st.session_state.setdefault(f"selection_{export_key}",
                                                          {u.use_case_id: True for u in summaries})
                              if usable else {})
selected = tuple(u.use_case_id for u in summaries if sel_store.get(u.use_case_id))
done = st.session_state.get("translated")
translated: tuple[str, ...] = done["selected"] if done and done["key"] == export_key else ()
if st.session_state["step"] == 3 and not translated:
    st.session_state["step"] = 2
step = st.session_state["step"]


def _translate() -> None:
    st.session_state["translated"] = {"key": export_key, "selected": selected}
    go(3)


# ============================================================ en-tête + étapes
project = ex.meta.project_name if ex is not None else None
html(f"<div class='p9-brand'><b>UserShaker → SysML v2</b><span>{escape(project or 'Aucun export chargé')}</span></div>")
by_id = {u.use_case_id: u for u in summaries}
stepper(step, [
    source.get("name", "Export de démonstration") if usable and source else "Déposer l'export .zip",
    f"{len(selected)} sur {plural(len(summaries), 'use case')}" if usable else "Cocher les use cases",
    plural(len(translated), "use case traduit", "use cases traduits") if translated else "Fichiers et diagrammes",
])


# ============================================================ 1. importer
def _controls_detail(rep) -> None:
    st.dataframe(pd.DataFrame([{"feuille": c.sheet, "attendu (meta.csv)": c.expected, "chargé": c.loaded,
                                "ok": "✅" if c.ok else "❌"} for c in rep.counts]), hide_index=True, width="stretch")
    if rep.missing_sheets:
        st.warning("Feuilles manquantes : " + ", ".join(rep.missing_sheets))
    for e in rep.fk_errors:
        st.warning(f"`{e.sheet}.{e.column}` → `{e.target}` : {len(e.missing_ids)} id introuvable(s) : "
                   + ", ".join(f"`{i}`" for i in e.missing_ids[:5]))
    for i in rep.row_errors + rep.order_errors + rep.duplicate_keys:
        st.warning(f"`{i.sheet}`" + (f" ligne {i.line}" if i.line else "") + f" : {i.message}")
    for i in rep.warnings:
        st.info(f"`{i.sheet}` : {i.message}")
    if rep.ignored_files:
        st.info("Fichiers CSV non reconnus (ignorés) : " + ", ".join(rep.ignored_files))


if step == 1:
    with st.container(key="stepbody_1"):   # nouveau bloc à chaque étape : rien ne reste de la précédente
        if not usable:
            html("<p class='p9-lead'>Importez l'export UserShaker</p>"
                 "<p class='p9-sub'>Déposez le fichier .zip téléchargé depuis UserShaker. Les CSV sont lus et "
                 "contrôlés, rien n'est modifié.</p>")
            st.file_uploader("Fichier .zip de l'export", type=["zip"], key="upload", on_change=_on_upload,
                             label_visibility="collapsed")
            c = st.columns([1.3, 3])
            c[0].button("Utiliser l'export de démonstration", on_click=_use_repo, width="stretch", key="nav_demo",
                        help=f"Charge l'export rangé dans {SETTINGS.data_dir.relative_to(SETTINGS.root).as_posix()}")
            if load_error:
                st.error(load_error)
            elif ex is not None and ex.report.missing_required:
                st.error("Cet export est incomplet : il manque " + ", ".join(ex.report.missing_required)
                         + f". Un export doit contenir au moins {', '.join(REQUIRED_SHEETS)}.")
            else:
                html("<div class='p9-hint'>En attente d'un fichier .zip. Pas d'export sous la main ? "
                     "Utilisez l'export de démonstration.</div>")
        else:
            rep = ex.report
            meta = ex.meta
            html(f"<p class='p9-lead'>{escape(meta.project_name or 'Export chargé')}</p>"
                 f"<p class='p9-sub'>Exporté le {escape((meta.exported_at or '?')[:10])} par "
                 f"{escape(meta.company_name or '?')}, schéma {escape(meta.schema_version or '?')}.</p>")
            html("<div class='p9-facts'>"
                 f"<div><b>{len(ex.use_cases)}</b><span>use cases</span></div>"
                 f"<div><b>{len(ex.steps)}</b><span>steps</span></div>"
                 f"<div><b>{len(ex.personas)}</b><span>personas</span></div>"
                 f"<div><b>{len(ex.components)}</b><span>composants</span></div>"
                 f"<div><b>{len(ex.user_needs)}</b><span>user needs</span></div></div>")
            if rep.ok:
                html("<div class='p9-check'><i>✓</i>Contrôles OK : les comptes correspondent à meta.csv et toutes "
                     "les clés étrangères pointent vers un id existant.</div>")
            else:
                html("<div class='p9-check warn'><i>!</i>Certains contrôles sont à vérifier : voir le détail.</div>")
            with st.expander("Détail des contrôles", expanded=not rep.ok):
                _controls_detail(rep)
            with st.container(key="actionbar_1"):
                a = st.columns([1.2, 3, 1.6])
                a[0].button("Changer de fichier", on_click=_reset, width="stretch", key="nav_reset")
                a[2].button("Continuer", on_click=go, args=(2,), type="primary", width="stretch", key="nav_continue")


# ============================================================ 2. choisir
def _toggle(uc_id: str) -> None:
    sel_store[uc_id] = st.session_state[f"sel_{uc_id}"]


def _select_all(value: bool) -> None:
    for u in summaries:
        sel_store[u.use_case_id] = value
        st.session_state[f"sel_{u.use_case_id}"] = value


def _uc_row(u) -> None:
    with st.container(key=f"ucrow_{u.use_case_id}"):
        left, right = st.columns([0.6, 0.4], gap="large")
        with left:
            st.checkbox(u.title, key=f"sel_{u.use_case_id}", on_change=_toggle, args=(u.use_case_id,))
            if u.description:
                html(f"<div class='p9-desc'>{escape(u.description)}</div>")
        with right:
            html(f"<div class='p9-meta'><b>{plural(len(u.steps), 'step')}</b>, "
                 f"{plural(len(u.personas), 'persona')}, {plural(len(u.needs), 'user need')}"
                 f"{', statut ' + escape(u.status) if u.status else ''}</div>")
            html("".join(f"<span class='pill' style='border-color:{escape(p.color)}'>{escape(p.name)}</span>"
                         for p in u.personas))
            if u.phases:
                html("<div class='p9-phases'>" + " → ".join(escape(p) for p in u.phases) + "</div>")
        with st.expander("Voir le parcours, les besoins et les émotions"):
            if u.story:
                st.markdown(f"*{u.story}*")
            st.dataframe(u.steps, hide_index=True, width="stretch")
            c1, c2 = st.columns(2, gap="large")
            with c1:
                st.markdown("**User needs servis**")
                for _, name in u.needs:
                    st.markdown(f"- {name}")
            with c2:
                if not u.emotions.empty:
                    st.markdown("**Émotion par persona, de -5 à +5**")
                    st.line_chart(u.emotions, width="stretch", height=200)


if step == 2:
    with st.container(key="stepbody_2"):   # nouveau bloc à chaque étape : rien ne reste de la précédente
        for u in summaries:                           # l'état des cases survit au changement d'étape
            st.session_state[f"sel_{u.use_case_id}"] = sel_store.get(u.use_case_id, True)
        html("<p class='p9-lead'>Choisissez les use cases à traduire</p>"
             "<p class='p9-sub'>Chaque use case devient un fichier .sysml. Les éléments partagés (personas, "
             "composants, besoins) vont dans un fichier commun.</p>")
        head = st.columns([1, 1, 2.4, 1.6], vertical_alignment="center")
        head[0].button("Tout cocher", on_click=_select_all, args=(True,), width="stretch")
        head[1].button("Tout décocher", on_click=_select_all, args=(False,), width="stretch")
        head[3].button(f"Traduire {plural(len(selected), 'use case')}", on_click=_translate, type="primary",
                       disabled=not selected, width="stretch", key="nav_translate_top")
        for u in summaries:
            _uc_row(u)
        with st.container(key="actionbar_2"):
            a = st.columns([1.2, 3, 1.6], vertical_alignment="center")
            a[0].button("Retour", on_click=go, args=(1,), width="stretch", key="nav_back")
            if not selected:
                a[1].warning("Cochez au moins un use case.")
            elif translated and translated != selected:
                a[1].caption("La sélection a changé depuis la dernière traduction.")
            a[2].button(f"Traduire {plural(len(selected), 'use case')}", on_click=_translate, type="primary",
                        disabled=not selected, width="stretch", key="nav_translate")


# ============================================================ 3. résultats
def _findings_block(title: str, check: str, empty: str, why: str = "") -> None:
    items = findings[check]
    st.markdown(f"**{title}** ({len(items)})")
    if why:
        st.caption(why)
    rules = CHECK_RULES.get(check, [])
    if rules:
        html("<div class='p9-rules'><span>Règle appliquée</span><ol>"
             + "".join(f"<li>{escape(r)}</li>" for r in rules) + "</ol></div>")
    if not items:
        st.caption(empty)
    for f in items:
        icon = {"error": "🔴", "warning": "🟠", "info": "🔵"}[f.severity]
        html(f"<div class='p9-finding'>{icon} {escape(f.message)}<br>"
             f"<span class='ids'>{' · '.join(f.ids)}</span></div>")


def _decide(pid: str, status: str) -> None:
    try:
        s = load_store(PROPOSALS_PATH)
    except ProposalFileError:
        return          # fichier illisible : rien n'est écrasé, le bandeau d'erreur l'explique
    s.decide(pid, status)  # type: ignore[arg-type]
    save_store(s, PROPOSALS_PATH)


def _ledger(matrix) -> None:
    rows = [e for e in matrix if e.kind not in ADDED_KINDS]     # éléments de l'export (sans décisions ni LLM)
    counts = {s: sum(1 for e in rows if e.status == s) for s in ("mapped", "partial", "not_mapped")}
    total = sum(counts.values())
    lost = counts["not_mapped"]
    if lost:
        line = (f"{plural(lost, 'élément')} de l'export sur {total} ne sont pas traduits : "
                "ils sont listés avec leur raison dans la matrice de traçabilité.")
    else:
        line = f"Les {total} éléments de l'export sont suivis jusqu'au modèle : aucun n'est perdu."
    bar = "".join(f"<span class='{c}' style='flex:{n}'></span>"
                  for c, n in (("m", counts["mapped"]), ("p", counts["partial"]), ("n", lost)) if n)
    html(f"<div class='p9-ledger{' has-loss' if lost else ''}'><p>{line}</p><div class='p9-bar'>{bar}</div>"
         "<div class='p9-legend'>"
         f"<span><i style='background:var(--ok)'></i><b>{counts['mapped']}</b>traduits</span>"
         f"<span><i style='background:var(--signal)'></i><b>{counts['partial']}</b>partiels, raison indiquée</span>"
         f"<span><i style='background:var(--loss)'></i><b>{lost}</b>non traduits</span></div></div>")


REVIEW_SECTIONS = [
    ("constraints_in_text_only", "Contraintes présentes seulement dans le texte libre",
     "Une phrase du déclencheur (trigger_text) n'a aucune condition structurée équivalente dans step_triggers. "
     "Elle reste en commentaire dans le .sysml mais ne devient pas une garde : ajoutez la garde, ou écartez "
     "le point si la condition existe déjà.",
     "Aucune : chaque phrase de trigger_text a une condition structurée."),
    ("persona_inconsistencies", "Incohérences entre personas",
     "Les textes d'un use case contredisent les personas : rôle d'occupant attribué à un autre persona dans sa "
     "biographie, persona cité mais absent du use case, renvoi à un autre use case avec d'autres personas. "
     "La source est à corriger dans UserShaker ; le modèle ne la réécrit pas.",
     "Aucune détectée."),
    ("implicit_needs", "Insights négatifs non reliés à un user need",
     "Un ressenti négatif (emotion_score < 0) d'un persona sur un step. Le format UserShaker ne le relie à aucun "
     "user need : créez le requirement correspondant, rattachez-le à un besoin existant, ou écartez-le.",
     "Aucun."),
]
STATUS_BADGE = {
    None: ("À traiter", "#8a5a00", "#FCEFD2"),
    "guard": ("Garde ajoutée", "#1f5c3a", "#DCEFE3"),
    "requirement": ("Requirement créé", "#1f5c3a", "#DCEFE3"),
    "link_need": ("Rattaché à un user need", "#1f5c3a", "#DCEFE3"),
    "fix_at_source": ("À corriger dans UserShaker", "#1d3f8f", "#DFE7F7"),
    "dismiss": ("Écarté", "#4b5568", "#E6E9EE"),
}


def _status_badge(action: str | None) -> str:
    label, fg, bg = STATUS_BADGE[action]
    return f"<span class='badge' style='color:{fg};background:{bg};font-weight:500'>{escape(label)}</span>"


def _save_decision(d: Decision) -> None:
    rs = load_review(SETTINGS.review_file)
    rs.upsert(d)
    save_review(rs, SETTINGS.review_file)


def _undo_decision(fid: str) -> None:
    try:
        rs = load_review(SETTINGS.review_file)
    except ReviewFileError:
        return          # fichier illisible : rien n'est écrasé, le bandeau d'erreur l'explique
    rs.remove(fid)
    save_review(rs, SETTINGS.review_file)
    st.session_state.pop(f"rv_edit_{fid}", None)


def _edit_decision(fid: str) -> None:
    st.session_state[f"rv_edit_{fid}"] = True


def _use_proposal(fid: str, p, action: str) -> None:
    """Pré-remplit le formulaire avec une proposition du LLM : l'humain la relit, la modifie, puis enregistre."""
    st.session_state[f"rv_act_{fid}"] = ACTION_LABELS[action]
    st.session_state[f"rv_title_{fid}"] = p.title
    st.session_state[f"rv_text_{fid}"] = p.text
    st.session_state[f"rv_mode_{fid}"] = GUARD_MODES[1]
    st.session_state[f"rv_llm_{fid}"] = p.proposal_id
    st.session_state[f"rv_edit_{fid}"] = True


GUARD_MODES = ["Condition structurée : un composant dans un état", "Condition décrite en texte"]


def _review_form(item) -> None:
    f, fid = item.finding, item.finding_id
    d = item.decision
    actions = ACTIONS_BY_CHECK[f.check]
    labels = [ACTION_LABELS[a] for a in actions]
    comps = sorted(ex.components, key=lambda c: (c.name.lower(), c.component_id))
    comp_ids = [c.component_id for c in comps]
    comp_name = {c.component_id: c.name for c in comps}
    needs = sorted(ex.user_needs, key=lambda u: (u.name.lower(), u.user_need_id))
    need_ids = [u.user_need_id for u in needs]
    sentence = f.detail.get("sentence", "")
    insight = f.detail.get("insight", "")
    # valeurs de départ : la décision existante, sinon un pré-remplissage tiré du point
    st.session_state.setdefault(f"rv_act_{fid}", ACTION_LABELS[d.action] if d else labels[0])
    st.session_state.setdefault(f"rv_title_{fid}", d.title if d and d.title else (sentence or insight)[:80])
    st.session_state.setdefault(f"rv_text_{fid}", d.text if d and d.text else (
        sentence if f.check == "constraints_in_text_only"
        else f"Le véhicule doit répondre au besoin exprimé : « {insight} »." if insight else ""))
    st.session_state.setdefault(f"rv_reason_{fid}", d.reason if d else "")
    if d is not None:
        default_mode = GUARD_MODES[0] if d.state_id else GUARD_MODES[1]
    else:   # une phrase avec un chiffre (« below 10 km/h ») vise souvent une grandeur absente des composants
        default_mode = GUARD_MODES[1] if any(ch.isdigit() for ch in sentence) else GUARD_MODES[0]
    st.session_state.setdefault(f"rv_mode_{fid}", default_mode)
    if d and d.component_id in comp_ids:
        st.session_state.setdefault(f"rv_comp_{fid}", d.component_id)
    closest = f.detail.get("closest_need_id", "")
    default_need = d.need_id if d and d.need_id in need_ids else closest if closest in need_ids else need_ids[0]
    st.session_state.setdefault(f"rv_need_{fid}", default_need)

    choice = st.radio("Décision", labels, key=f"rv_act_{fid}", horizontal=True)
    action = actions[labels.index(choice)]
    fields: dict = {}
    if action == "guard":
        fields["title"] = st.text_input("Nom de la garde", key=f"rv_title_{fid}")
        mode = st.radio("Type de condition", GUARD_MODES, key=f"rv_mode_{fid}", horizontal=True)
        if mode == GUARD_MODES[0]:
            c1, c2 = st.columns(2)
            cid = c1.selectbox("Composant", comp_ids, key=f"rv_comp_{fid}", format_func=lambda c: comp_name[c])
            states = sorted((s for s in ex.component_states if s.component_id == cid), key=lambda s: s.position)
            state_ids = [s.state_id for s in states]
            if d and d.state_id in state_ids:
                st.session_state.setdefault(f"rv_state_{fid}", d.state_id)
            if st.session_state.get(f"rv_state_{fid}") not in state_ids:
                st.session_state.pop(f"rv_state_{fid}", None)
            sname = {s.state_id: s.name for s in states}
            sid = c2.selectbox("État attendu", state_ids, key=f"rv_state_{fid}",
                               format_func=lambda x: sname.get(x, x)) if state_ids else ""
            fields.update(component_id=cid, state_id=sid or "")
            if not state_ids:
                st.caption("Ce composant n'a aucun état : choisissez-en un autre ou décrivez la condition en texte.")
        else:
            fields["text"] = st.text_area("Condition", key=f"rv_text_{fid}", height=70,
                                          help="Ex. : vitesse du véhicule < 10 km/h")
            st.caption("Aucun composant de l'export ne représente cette grandeur : la garde est créée avec cette "
                       "description. Pensez à ajouter le composant dans UserShaker pour qu'elle devienne structurée.")
        fields["reason"] = st.text_input("Motif (facultatif)", key=f"rv_reason_{fid}")
    elif action == "requirement":
        fields["title"] = st.text_input("Titre du requirement", key=f"rv_title_{fid}")
        fields["text"] = st.text_area("Texte (une phrase vérifiable : « Le véhicule doit… »)",
                                      key=f"rv_text_{fid}", height=80)
        fields["reason"] = st.text_input("Motif (facultatif)", key=f"rv_reason_{fid}")
    elif action == "link_need":
        nname = {u.user_need_id: u.name for u in needs}
        fields["need_id"] = st.selectbox("User need existant", need_ids, key=f"rv_need_{fid}",
                                         format_func=lambda x: nname.get(x, x))
        if closest:
            st.caption(f"Pré-sélection : le besoin le plus proche par les mots ({nname.get(closest, closest)}).")
        fields["reason"] = st.text_input("Motif (facultatif)", key=f"rv_reason_{fid}")
    else:
        fields["reason"] = st.text_area(
            "Ce qu'il faut corriger dans UserShaker" if action == "fix_at_source" else "Pourquoi écarter ce point ?",
            key=f"rv_reason_{fid}", height=70)

    err = st.session_state.pop(f"rv_err_{fid}", None)
    if err:
        st.error(err)
    st.button("Enregistrer la décision", key=f"rv_save_{fid}", type="primary", on_click=_submit_decision,
              args=(f, fid, action, fields), disabled=bool(review_error))


def _submit_decision(f, fid: str, action: str, fields: dict) -> None:
    """Callback : exécuté avant le réaffichage, la page montre donc tout de suite la décision enregistrée."""
    author = (st.session_state.get("rv_author") or "").strip()
    # relit les champs dans l'état des widgets (valeurs au moment du clic)
    values = {}
    for k, v in fields.items():
        wkey = {"title": "rv_title_", "text": "rv_text_", "reason": "rv_reason_", "component_id": "rv_comp_",
                "state_id": "rv_state_", "need_id": "rv_need_"}[k] + fid
        cur = st.session_state.get(wkey, v)
        values[k] = (cur or "").strip() if isinstance(cur, str) or cur is None else cur
    dec = Decision(
        finding_id=fid, check=f.check, action=action, author=author or "?", decided_at=date.today().isoformat(),
        target_ids=list(f.ids), finding_message=f.message,
        llm_proposal_id=st.session_state.get(f"rv_llm_{fid}", "") if action in {"guard", "requirement"} else "",
        **values,
    )
    errors = ([] if author else ["indiquez votre nom en haut de l'onglet"]) + decision_errors(dec, ex)
    if errors:
        st.session_state[f"rv_err_{fid}"] = "Décision non enregistrée : " + " ; ".join(errors) + "."
        return
    try:
        _save_decision(dec)
    except ReviewFileError as e:
        st.session_state[f"rv_err_{fid}"] = f"Décision non enregistrée : {e}"
        return
    st.session_state.pop(f"rv_edit_{fid}", None)


def _review_card(item) -> None:
    f, fid, d = item.finding, item.finding_id, item.decision
    with st.container(border=True, key=f"rvcard_{fid}"):
        html(f"{_status_badge(d.action if d else None)} {escape(f.message)}<br>"
             f"<span class='ids'>{' · '.join(f.ids)}</span>")
        if d:
            detail = {"guard": d.title, "requirement": d.title, "link_need": next(
                (u.name for u in ex.user_needs if u.user_need_id == d.need_id), d.need_id)}.get(d.action, d.reason)
            src = " (à partir d'une proposition du LLM)" if d.llm_proposal_id else ""
            html(f"<div class='p9-decision'><b>{escape(ACTION_LABELS[d.action])}</b> par {escape(d.author)} "
                 f"le {escape(d.decided_at)}{src} : {escape(detail)}</div>")
        editing = d is None or st.session_state.get(f"rv_edit_{fid}", False)
        if d is not None:
            b1, b2, _ = st.columns([1, 1, 3])
            b1.button("Modifier", key=f"rv_mod_{fid}", on_click=_edit_decision, args=(fid,), width="stretch",
                      disabled=editing)
            b2.button("Annuler la décision", key=f"rv_undo_{fid}", on_click=_undo_decision, args=(fid,),
                      width="stretch")
        if editing:
            related = [p for p in store.proposals if p.status != "rejected" and set(p.target_ids) & set(f.ids)
                       and p.kind in {"guard", "requirement"}
                       and p.kind in ACTIONS_BY_CHECK[f.check]]
            with st.expander("Traiter ce point", expanded=d is not None):
                for p in related:
                    q1, q2 = st.columns([4, 1.3], vertical_alignment="center")
                    q1.markdown(f"<span class='muted'>Proposition du LLM ({escape(p.kind)}) : <b>{escape(p.title)}</b>"
                                f" : {escape(p.text)}</span>", unsafe_allow_html=True)
                    q2.button("Reprendre et modifier", key=f"rv_use_{fid}_{p.proposal_id}", on_click=_use_proposal,
                              args=(fid, p, p.kind), width="stretch")
                _review_form(item)


if step == 3:
    with st.container(key="stepbody_3"):   # nouveau bloc à chaque étape : rien ne reste de la précédente
        # Fichier illisible : on continue sans son contenu, on le signale, et rien n'y est écrit (pas d'écrasement).
        try:
            store, store_error = load_store(PROPOSALS_PATH), ""
        except ProposalFileError as e:
            store, store_error = ProposalStore(), str(e)
        accepted = ProposalStore(proposals=store.accepted())
        try:
            review, review_error = load_review(SETTINGS.review_file), ""
        except ReviewFileError as e:
            review, review_error = ReviewStore(), str(e)
        review_json = review.model_dump_json()
        gen, matrix, cov, trace_csv = cached_generate(
            f"{export_key}|{'|'.join(translated)}|{hashlib.sha256(review_json.encode()).hexdigest()}", ex, translated,
            accepted.model_dump_json(), review_json)
        catia_files = cached_catia(f"{export_key}|{'|'.join(translated)}", ex, translated)
        findings = cached_findings(export_key, ex)
        rv_state = review_state(findings, review)
        n_open = rv_state.total - rv_state.done

        head = st.columns([3, 1.3, 1.6], vertical_alignment="bottom")
        with head[0]:
            names = ", ".join(f"UC{by_id[i].position}" for i in translated if i in by_id)
            html(f"<p class='p9-lead'>Votre modèle SysML v2 est prêt</p>"
                 f"<p class='p9-sub'>{plural(len(translated), 'use case traduit', 'use cases traduits')} ({names}), "
                 f"{plural(len(gen.files), 'fichier')} .sysml.</p>")
        head[1].button("Modifier la sélection", on_click=go, args=(2,), width="stretch", key="nav_modify")
        bundle = dict(gen.files)
        bundle["traceability.csv"] = trace_csv
        if review.decisions:
            bundle["review/decisions.json"] = review_json_pretty(review)
            bundle["review/review.csv"] = review_csv(review)
        bundle.update({f"catia/{f.name}": f.text for f in catia_files})
        head[2].download_button("Télécharger le modèle (.zip)", make_zip(bundle), file_name="sysml_export.zip",
                                mime="application/zip", type="primary", width="stretch",
                                help="Fichiers .sysml (commun + un par use case), traceability.csv, et catia/ : "
                                     "un fichier autonome par use case pour CATIA Community")
        if translated != selected:
            st.info("La sélection a changé depuis cette traduction. Revenez à l'étape Choisir pour traduire à nouveau.")
        for err in (review_error, store_error):
            if err:
                st.error(err)
        _ledger(matrix)

        # libellés fixes : un libellé qui change (compteur) ferait perdre l'onglet ouvert à Streamlit
        labels = RESULT_TABS
        if n_open:
            st.caption(f"{plural(n_open, 'point')} à valider sur {rv_state.total} : onglet « Points à valider ».")
        t_diag, t_files, t_review, t_chat = st.tabs(labels, key="result_tab", on_change="rerun")

        # ---------------------------------------------------------------- diagrammes
        if t_diag.open:
            with t_diag:
                ctl = st.columns([2.4, 2.2, 1.3], vertical_alignment="bottom")
                kind = ctl[0].segmented_control("Vue", ["Activité", "Use cases", "Structure", "Machine à états"],
                                                key=persist("diag_kind", "Activité"), on_change=remember,
                                                args=("diag_kind",)) or "Activité"
                with_reqs = True
                if kind == "Activité":
                    with_reqs = ctl[2].toggle("Besoins servis", key=persist("diag_reqs", True), on_change=remember,
                                              args=("diag_reqs",))
                diagrams = cached_diagrams(f"{export_key}|{'|'.join(translated)}|{with_reqs}", ex, translated, with_reqs)
                if kind == "Structure":
                    view = diagrams.structure
                elif kind == "Use cases":
                    view = diagrams.use_cases
                elif kind == "Activité":
                    pkgs = list(diagrams.activities)
                    if st.session_state.get("diag_uc") not in pkgs:
                        st.session_state.pop("diag_uc", None)
                        st.session_state.get("_ui", {}).pop("diag_uc", None)
                    uc = ctl[1].selectbox("Use case", pkgs, key=persist("diag_uc", pkgs[0]), on_change=remember,
                                          args=("diag_uc",))
                    view = diagrams.activities[uc]
                else:
                    active = [n for n, d in diagrams.states.items() if d.transitions]
                    choices = active or list(diagrams.states)
                    if st.session_state.get("diag_sd") not in choices:
                        st.session_state.pop("diag_sd", None)
                        st.session_state.get("_ui", {}).pop("diag_sd", None)
                    sd = ctl[1].selectbox("Composant", choices, key=persist("diag_sd", choices[0]), on_change=remember,
                                          args=("diag_sd",),
                                          format_func=lambda n: f"{n} ({plural(diagrams.states[n].transitions, 'transition')})")
                    view = diagrams.states[sd]
                diagram_viewer(view)
                st.caption("Aperçu dessiné depuis le modèle généré, avec les mêmes noms que les fichiers .sysml. "
                           "Survolez un bloc pour voir son texte d'origine et son id UserShaker. La mise en page "
                           "définitive se fait dans CATIA Magic après import.")

        # ---------------------------------------------------------------- fichiers
        if t_files.open:
            with t_files:
                st.markdown("**Couverture des use cases traduits**")
                st.dataframe(pd.DataFrame([{"élément": r.element, "dans l'export": r.in_export, "traduit": r.translated,
                                            "couverture": r.percent_label, "note": r.note} for r in cov]),
                             hide_index=True, width="stretch")
                tabs = st.tabs(list(gen.files))
                for tab, (name, src) in zip(tabs, gen.files.items()):
                    with tab:
                        st.download_button(f"Télécharger {name}", src.encode("utf-8"), file_name=name,
                                           mime="text/plain", key=f"dl_{name}")
                        st.code(src, language=None, line_numbers=True, height=480)
                st.markdown("**Pour CATIA Magic Community**")
                st.caption(f"L'éditeur textuel de l'édition gratuite refuse plus de {SETTINGS.catia.char_limit:,} "
                           "caractères : un fichier autonome par use case, à coller dans l'éditeur textuel d'un nouveau "
                           "projet puis Alt+S (affichage des vues : voir le README). Ce qui est laissé de côté est noté "
                           "par `make trace`.")
                for f in catia_files:
                    left, right = st.columns([2, 3], vertical_alignment="center")
                    left.download_button(f"Télécharger {f.name}", f.text.encode("utf-8"), file_name=f.name,
                                         mime="text/plain", key=f"dl_catia_{f.name}", width="stretch")
                    right.caption(f"{f.chars:,} / {SETTINGS.catia.char_limit:,} caractères"
                                  + (f" · réductions : {', '.join(f.trims)}" if f.trims else " · aucune réduction"))
                with st.expander(f"Matrice de traçabilité complète ({len(matrix)} lignes)"):
                    df = pd.DataFrame([e.model_dump() for e in matrix])
                    status = st.multiselect("Statut", ["mapped", "partial", "not_mapped"],
                                            default=["mapped", "partial", "not_mapped"])
                    st.dataframe(df[df.status.isin(status)], hide_index=True, width="stretch")
                with st.expander("Vérifier la syntaxe avec le parseur OMG SysML v2"):
                    if not sysml_parser.is_installed():
                        st.caption("Parseur OMG non installé : lancez `make validate-setup` une fois.")
                    elif st.button("Lancer la vérification"):
                        try:
                            res = run_live("Vérification par le parseur OMG SysML v2", "omg",
                                           lambda ev: sysml_parser.validate_texts(
                                               gen.files, SETTINGS.out_dir / "ui-validate", on_event=ev))
                        except Exception as e:
                            st.error(f"Vérification impossible : {e}")
                        else:
                            if res.ok:
                                st.success(f"Les {len(res.files)} fichiers passent le parseur OMG, sans erreur.")
                            else:
                                st.error(f"{sum(f.errors for f in res.files)} erreur(s) OMG")
                                st.dataframe(pd.DataFrame([i.model_dump() for i in res.issues]), hide_index=True,
                                             width="stretch")

        # ---------------------------------------------------------------- points à valider (+ propositions LLM)
        if t_review.open:
            with t_review:
                st.caption("Règles Python fixes appliquées à l'export, sans LLM : le résultat est le même à chaque "
                           "lancement. Elles repèrent ce que le modèle SysML ne peut pas représenter tel quel. "
                           "Pour chaque point, c'est vous qui décidez : ajouter la garde ou le requirement qui manque, "
                           "rattacher à un besoin existant, renvoyer la correction à UserShaker, ou écarter le point. "
                           "Les décisions sont signées, enregistrées dans review/decisions.json et générées dans un "
                           "package séparé (origin = \"human\").")
                top = st.columns([1.4, 2.6], vertical_alignment="bottom")
                top[0].text_input("Votre nom (enregistré avec chaque décision)", key=persist("rv_author", ""),
                                  on_change=remember, args=("rv_author",), placeholder="Prénom Nom")
                with top[1]:
                    st.progress(rv_state.done / rv_state.total if rv_state.total else 1.0,
                                text=f"{rv_state.done} / {rv_state.total} points traités")
                for check, title, why, empty in REVIEW_SECTIONS:
                    rows = rv_state.items.get(check, [])
                    st.markdown(f"##### {title} ({len(rows)})")
                    st.caption(why)
                    html("<div class='p9-rules'><span>Règle appliquée</span><ol>"
                         + "".join(f"<li>{escape(r)}</li>" for r in CHECK_RULES.get(check, [])) + "</ol></div>")
                    if not rows:
                        st.caption(empty)
                    for item in rows:
                        _review_card(item)
                if rv_state.orphans:
                    with st.expander(f"Décisions dont le point n'est plus détecté ({len(rv_state.orphans)})"):
                        st.caption("L'export a changé et ces points ne sont plus signalés. La décision est gardée tant "
                                   "que vous ne la supprimez pas, mais elle n'est plus appliquée à la traçabilité.")
                        for d in rv_state.orphans:
                            o1, o2 = st.columns([4, 1], vertical_alignment="center")
                            o1.markdown(f"**{ACTION_LABELS[d.action]}** par {d.author} le {d.decided_at} : "
                                        f"{d.finding_message or d.finding_id}")
                            o2.button("Supprimer", key=f"rv_orph_{d.finding_id}", on_click=_undo_decision,
                                      args=(d.finding_id,), width="stretch")
                with st.expander("Autres contrôles (informatifs)"):
                    _findings_block("User needs non couverts", "uncovered_needs", "Tous les user needs sont servis.")
                    _findings_block("États jamais atteints", "unreachable_states", "Tous les états sont atteints.")
                    _findings_block("Triggers invalides", "invalid_triggers", "Aucun.")

                st.divider()
                st.markdown("##### Propositions du LLM")
                st.caption("Le LLM propose, l'humain valide. Seules les propositions acceptées entrent dans le "
                           "modèle, dans un fichier séparé, et restent marquées `origin = \"llm\"`. Données envoyées : "
                           "uniquement les points ci-dessus et le nom des éléments voisins (ni biographies, ni images).")
                llm_ok, llm_label = provider_status(SETTINGS.llm)
                st.markdown(f"Fournisseur : **{llm_label}**" if llm_ok else f"⚠️ {llm_label}")

                if st.button("Générer des propositions", disabled=not llm_ok or bool(store_error)):
                    try:
                        provider, tctx = make_provider(SETTINGS.llm), cached_tool_context(export_key, ex)
                        res = run_live("Le LLM analyse les points à valider", llm_key("enrich"),
                                       lambda ev: enrich(provider, tctx, on_event=ev))
                    except LlmUnavailable as e:
                        st.error(str(e))
                    else:
                        store.merge(res.proposals)
                        save_store(store, PROPOSALS_PATH)
                        st.success(f"{len(res.proposals)} proposition(s) reçue(s)"
                                   + (f", {len(res.invalid)} écartée(s) automatiquement (ids inconnus ou "
                                      "format invalide)" if res.invalid else ""))

                valid_ids = {s.step_id for s in ex.steps} | {f.feedback_id for f in ex.persona_step_feedback} | \
                            {u.user_need_id for u in ex.user_needs} | {u.use_case_id for u in ex.use_cases} | \
                            {p.persona_id for p in ex.personas}
                shown = [p for p in store.proposals if all(t in valid_ids for t in p.target_ids)]
                if not shown:
                    st.caption("Aucune proposition pour cet export.")
                for p in shown:
                    with st.container(border=True):
                        st.markdown(badges(p.kind, p.status, f"origin = {p.origin}"), unsafe_allow_html=True)
                        st.markdown(f"**{p.title}**  \n{p.text}")
                        html(f"<span class='muted'>{escape(p.rationale)}</span><br>"
                             f"<span class='ids'>cibles : {' · '.join(p.target_ids)} · id {p.proposal_id}</span>")
                        b1, b2, _ = st.columns([1, 1, 1.5])
                        b1.button("Accepter", key=f"acc_{p.proposal_id}", on_click=_decide,
                                  args=(p.proposal_id, "accepted"), disabled=p.status == "accepted",
                                  type="primary", width="stretch")
                        b2.button("Rejeter", key=f"rej_{p.proposal_id}", on_click=_decide,
                                  args=(p.proposal_id, "rejected"), disabled=p.status == "rejected", width="stretch")

        # ---------------------------------------------------------------- assistant (chat)
        if t_chat.open:
            with t_chat:
                st.caption("L'assistant ne répond jamais de mémoire : il interroge le modèle via des outils "
                           "(uncovered_needs, constraints_in_text_only, find_element…) et cite les ids.")
                llm_ok, llm_label = provider_status(SETTINGS.llm)
                if not llm_ok:
                    st.info(llm_label)
                else:
                    hist_key = f"chat_{export_key}"
                    history = st.session_state.setdefault(hist_key, [])
                    for turn in history:
                        with st.chat_message(turn["role"]):
                            st.markdown(turn["content"])
                            if turn.get("tools"):
                                st.caption("Outils appelés : " + ", ".join(turn["tools"]))
                    question = st.chat_input("Ex. : quels besoins implicites ne sont couverts par aucun user need ?")
                    if question:
                        with st.chat_message("user"):
                            st.markdown(question)
                        with st.chat_message("assistant"):
                            try:
                                past = [{"role": t["role"], "content": t["content"]} for t in history]
                                provider, tctx = make_provider(SETTINGS.llm), cached_tool_context(export_key, ex)
                                ans = run_live("L'assistant interroge le modèle", llm_key("chat"),
                                               lambda ev: ask(provider, tctx, question, past, on_event=ev))
                            except LlmUnavailable as e:
                                st.error(str(e))
                                st.stop()
                            st.markdown(ans.text)
                            tools = [c["name"] for c in ans.tool_calls]
                            if tools:
                                st.caption("Outils appelés : " + ", ".join(tools))
                            if ans.unknown_ids:
                                st.warning("Ids cités introuvables dans l'export : " + ", ".join(ans.unknown_ids))
                        history += [{"role": "user", "content": question},
                                    {"role": "assistant", "content": ans.text, "tools": tools}]
