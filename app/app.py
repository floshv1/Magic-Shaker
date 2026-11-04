"""Interface de démo : export UserShaker (.zip) → use cases → sélection → fichiers SysML v2.

Lancement : `make demo` (équivalent de `streamlit run app/app.py`).
Aucune logique métier ici : l'interface appelle uniquement src/p9 (ingest, generate, checks, llm).
"""

from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from p9.checks import build_matrix, coverage, matrix_csv, run_all
from p9.checks import sysml_parser
from p9.config import load_settings
from p9.generate import build_diagrams, generate, make_zip
from p9.ingest import REQUIRED_SHEETS, Export, ExportUnreadable, load_dir, load_zip, use_case_summaries
from p9.llm import (
    LlmUnavailable,
    ProposalStore,
    ToolContext,
    ask,
    enrich,
    load_store,
    make_provider,
    provider_status,
    save_store,
)

SETTINGS = load_settings(Path(__file__).resolve().parent)
PROPOSALS_PATH = SETTINGS.out_dir / "proposals.json"

st.set_page_config(page_title="UserShaker → SysML v2", layout="wide")
st.markdown("""
<style>
.badge{display:inline-block;font-size:12px;padding:1px 9px;border-radius:10px;background:rgba(127,127,127,.15);margin:0 4px 4px 0}
.pill{display:inline-block;font-size:12px;padding:1px 9px;border-radius:10px;margin:0 4px 4px 0;border:1.5px solid}
.muted{color:rgba(127,127,127,.95);font-size:13px}
.ids{font-family:ui-monospace,monospace;font-size:11px;color:rgba(127,127,127,.95)}
</style>
""", unsafe_allow_html=True)


# ============================================================ cache (clé = contenu du zip)

@st.cache_data(show_spinner=False)
def cached_load(data: bytes) -> Export:
    return load_zip(data)


@st.cache_data(show_spinner=False)
def cached_load_repo(path: str) -> Export:
    return load_dir(path)


@st.cache_data(show_spinner="Génération SysML v2…")
def cached_generate(key: str, _ex: Export, selected: tuple[str, ...], accepted_json: str):
    store = ProposalStore.model_validate_json(accepted_json)
    gen = generate(_ex, list(selected), SETTINGS.generate, accepted_proposals=store.proposals)
    findings = run_all(_ex)
    matrix = build_matrix(_ex, gen, findings["constraints_in_text_only"])
    return gen, findings, matrix, coverage(_ex, gen, matrix), matrix_csv(matrix)


@st.cache_data(show_spinner="Diagrammes…")
def cached_diagrams(key: str, _ex: Export, selected: tuple[str, ...], with_requirements: bool):
    return build_diagrams(_ex, list(selected), SETTINGS.generate, with_requirements=with_requirements)


@st.cache_resource(show_spinner="Préparation des outils de l'agent…")
def cached_tool_context(key: str, _ex: Export) -> ToolContext:
    return ToolContext(_ex)


def badges(*texts: str) -> str:
    return "".join(f"<span class='badge'>{escape(t)}</span>" for t in texts)


st.title("UserShaker → SysML v2")
st.caption("Importer l'export → vérifier les use cases → choisir → exporter les fichiers .sysml → valider les points ouverts")

# ============================================================ 1. import
st.header("1. Importer l'export")
up = st.file_uploader("Déposez le .zip de l'export UserShaker (les CSV peuvent être dans un sous-dossier)", type=["zip"])
use_repo = st.toggle(f"Utiliser l'export du dépôt ({SETTINGS.data_dir.relative_to(SETTINGS.root).as_posix()})",
                     value=False, disabled=up is not None)

try:
    if up is not None:
        raw = up.getvalue()
        ex = cached_load(raw)
        export_key = hashlib.sha256(raw).hexdigest()
    elif use_repo:
        ex = cached_load_repo(str(SETTINGS.data_dir))
        export_key = "repo:" + hashlib.sha256(ex.model_dump_json().encode()).hexdigest()
    else:
        st.info("En attente d'un fichier .zip.")
        st.stop()
except ExportUnreadable as e:
    st.error(f"Impossible de lire ce fichier : {e}. Vérifiez qu'il s'agit bien d'un export UserShaker zippé.")
    st.stop()
except Exception as e:  # jamais de stack trace pour l'utilisateur
    st.error(f"Lecture impossible : {type(e).__name__}. Le fichier est peut-être corrompu.")
    st.stop()

rep = ex.report
if rep.missing_required:
    st.error("Feuilles obligatoires absentes : " + ", ".join(rep.missing_required)
             + f". Un export doit contenir au moins : {', '.join(REQUIRED_SHEETS)}.")
    st.stop()

c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1.2])
c1.metric("Projet", ex.meta.project_name or "—")
c2.metric("Use cases", len(ex.use_cases))
c3.metric("Steps", len(ex.steps))
c4.metric("Contrôles", "OK" if rep.ok else "À vérifier")
st.caption(f"Exporté le {ex.meta.exported_at or '?'} · schéma {ex.meta.schema_version or '?'} · {ex.meta.company_name or ''}")

with st.expander("Détail des contrôles", expanded=not rep.ok):
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
    if rep.ok:
        st.success("Les comptes correspondent à meta.csv et toutes les clés étrangères pointent vers un id existant.")

# ============================================================ 2 + 3. use cases et sélection
st.header("2. Use cases importés")
summaries = use_case_summaries(ex)
for u in summaries:
    st.session_state.setdefault(f"sel_{u.use_case_id}", True)


def _select_all(value: bool) -> None:
    for u in summaries:
        st.session_state[f"sel_{u.use_case_id}"] = value


st.markdown("**3. Choisir les use cases à traduire**")
b1, b2, _ = st.columns([1, 1, 4])
b1.button("Tout sélectionner", on_click=_select_all, args=(True,), width="stretch")
b2.button("Tout désélectionner", on_click=_select_all, args=(False,), width="stretch")

for u in summaries:
    with st.container(border=True):
        left, right = st.columns([0.05, 0.95])
        left.checkbox("Traduire", key=f"sel_{u.use_case_id}", label_visibility="collapsed")
        with right:
            st.subheader(u.title)
            st.markdown(badges(u.status, f"{len(u.steps)} steps", f"{len(u.personas)} personas",
                               f"{len(u.needs)} user needs"), unsafe_allow_html=True)
            if u.description:
                st.write(u.description)
            st.markdown("".join(
                f"<span class='pill' style='border-color:{escape(p.color)}'>{escape(p.name)} · {escape(p.age_category)}</span>"
                for p in u.personas), unsafe_allow_html=True)
            if u.phases:
                st.markdown("<span class='muted'>Phases : " + " → ".join(escape(p) for p in u.phases) + "</span>",
                            unsafe_allow_html=True)
            with st.expander("Parcours, besoins et émotions"):
                if u.story:
                    st.markdown(f"*{u.story}*")
                st.dataframe(u.steps, hide_index=True, width="stretch")
                cl, cr = st.columns(2)
                with cl:
                    st.markdown("**User needs servis**")
                    for _, name in u.needs:
                        st.markdown(f"- {name}")
                with cr:
                    if not u.emotions.empty:
                        st.markdown("**Émotion par persona (-5 à +5), steps où le persona est présent**")
                        st.line_chart(u.emotions, width="stretch")

selected = tuple(u.use_case_id for u in summaries if st.session_state.get(f"sel_{u.use_case_id}"))

# ============================================================ 4. export
st.header("4. Exporter les fichiers .sysml")
if not selected:
    st.warning("Cochez au moins un use case.")
    st.stop()

store = load_store(PROPOSALS_PATH)
accepted = ProposalStore(proposals=store.accepted())
gen, findings, matrix, cov, trace_csv = cached_generate(
    f"{export_key}|{'|'.join(selected)}", ex, selected, accepted.model_dump_json())

common = f"{SETTINGS.generate.common_package}.sysml"
st.write(f"**{len(selected)}** use case(s) sélectionné(s) → un fichier par use case + `{common}` "
         "(métadonnée UserShakerRef, personas, composants, états, requirements), importé par chaque fichier "
         "pour éviter les doublons dans CATIA.")
if accepted.proposals:
    st.info(f"{len(accepted.proposals)} proposition(s) LLM acceptée(s) incluse(s) dans un fichier séparé "
            "(marquées `origin = \"llm\"`).")

st.subheader("Couverture")
st.dataframe(pd.DataFrame([{"élément": r.element, "dans l'export": r.in_export, "traduit": r.translated,
                            "couverture": r.percent_label, "note": r.note} for r in cov]),
             hide_index=True, width="stretch")

bundle = dict(gen.files)
bundle["traceability.csv"] = trace_csv
st.download_button("Tout télécharger (.zip : fichiers .sysml + traceability.csv)", make_zip(bundle),
                   file_name="sysml_export.zip", mime="application/zip", type="primary", width="stretch")

if sysml_parser.is_installed():
    if st.button("Valider avec le parseur OMG SysML v2 (≈ 1 min)"):
        with st.spinner("Validation OMG en cours…"):
            try:
                res = sysml_parser.validate_texts(gen.files, SETTINGS.out_dir / "ui-validate")
            except Exception as e:
                st.error(f"Validation impossible : {e}")
            else:
                if res.ok:
                    st.success(f"Les {len(res.files)} fichiers passent le parseur OMG (0 erreur).")
                else:
                    st.error(f"{sum(f.errors for f in res.files)} erreur(s) OMG")
                    st.dataframe(pd.DataFrame([i.model_dump() for i in res.issues]), hide_index=True, width="stretch")
else:
    st.caption("Parseur OMG non installé : `make validate-setup` pour activer la validation depuis l'interface.")

tabs = st.tabs(list(gen.files))
for tab, (name, src) in zip(tabs, gen.files.items()):
    with tab:
        st.download_button(f"Télécharger {name}", src.encode("utf-8"), file_name=name, mime="text/plain",
                           key=f"dl_{name}")
        st.code(src, language=None, line_numbers=True, height=520)

with st.expander(f"Matrice de traçabilité ({len(matrix)} lignes)"):
    df = pd.DataFrame([e.model_dump() for e in matrix])
    status = st.multiselect("Statut", ["mapped", "partial", "not_mapped"], default=["mapped", "partial", "not_mapped"])
    st.dataframe(df[df.status.isin(status)], hide_index=True, width="stretch")

st.subheader("Diagrammes (aperçu du modèle, style CATIA Magic)")
st.caption("Vues dessinées à partir du modèle généré : mêmes noms SysML que les fichiers .sysml. "
           "Survoler un élément affiche sa description et son id UserShaker. "
           "Aperçu seulement : la mise en page dans CATIA Magic se fait après import des fichiers.")
with_reqs = st.toggle("Afficher les requirements servis dans les vues d'activité", value=True, key="diag_reqs")
diagrams = cached_diagrams(f"{export_key}|{'|'.join(selected)}|{with_reqs}", ex, selected, with_reqs)
v_struct, v_uc, v_act, v_state = st.tabs(["Structure (part def)", "Use cases", "Activités (actions)",
                                          "Machines à états (state def)"])
with v_struct:
    st.caption(diagrams.structure.title)
    st.graphviz_chart(diagrams.structure.dot, width="stretch")
with v_uc:
    st.caption(diagrams.use_cases.title)
    st.graphviz_chart(diagrams.use_cases.dot, width="stretch")
with v_act:
    uc_pkg = st.selectbox("Use case", list(diagrams.activities), key="diag_uc")
    st.caption(diagrams.activities[uc_pkg].title)
    st.graphviz_chart(diagrams.activities[uc_pkg].dot, width="stretch")
with v_state:
    only_active = st.toggle("Seulement les state def avec au moins une transition", value=True, key="diag_active")
    choices = [n for n, d in diagrams.states.items() if not only_active or d.edges > 1]
    if choices:
        sd_name = st.selectbox("state def", choices, key="diag_sd",
                               format_func=lambda n: f"{n} ({diagrams.states[n].edges - 1} transition(s))")
        st.caption(diagrams.states[sd_name].title)
        st.graphviz_chart(diagrams.states[sd_name].dot, width="stretch")
    else:
        st.info("Aucune transition déduite pour les use cases sélectionnés.")

# ============================================================ 5. points à valider
st.header("5. Points à valider")


def _findings_block(title: str, items, empty: str) -> None:
    st.markdown(f"**{title}** ({len(items)})")
    if not items:
        st.caption(empty)
    for f in items:
        icon = {"error": "🔴", "warning": "🟠", "info": "🔵"}[f.severity]
        st.markdown(f"{icon} {escape(f.message)}<br><span class='ids'>{' · '.join(f.ids)}</span>",
                    unsafe_allow_html=True)


_findings_block("Contraintes présentes seulement dans le texte libre", findings["constraints_in_text_only"],
                "Aucune : chaque phrase de trigger_text a une condition structurée.")
_findings_block("Incohérences entre personas", findings["persona_inconsistencies"], "Aucune détectée.")
_findings_block("Insights négatifs non reliés à un user need", findings["implicit_needs"], "Aucun.")
with st.expander("Autres contrôles"):
    _findings_block("User needs non couverts", findings["uncovered_needs"], "Tous les user needs sont servis.")
    _findings_block("États jamais atteints", findings["unreachable_states"], "Tous les états sont atteints.")
    _findings_block("Triggers invalides", findings["invalid_triggers"], "Aucun.")

st.subheader("Propositions du LLM")
st.caption("Le LLM propose, l'humain valide. Seules les propositions acceptées entrent dans le modèle, "
           "dans un fichier séparé, et restent marquées `origin = \"llm\"`. Données envoyées : uniquement les "
           "points ci-dessus et le nom des éléments voisins (ni biographies, ni images).")
llm_ok, llm_label = provider_status(SETTINGS.llm)
st.markdown(f"Fournisseur : **{llm_label}**" if llm_ok else f"⚠️ {llm_label}")

if st.button("Générer des propositions", disabled=not llm_ok):
    with st.spinner("Le LLM analyse les points à valider…"):
        try:
            res = enrich(make_provider(SETTINGS.llm), cached_tool_context(export_key, ex))
        except LlmUnavailable as e:
            st.error(str(e))
        else:
            store.merge(res.proposals)
            save_store(store, PROPOSALS_PATH)
            st.success(f"{len(res.proposals)} proposition(s) reçue(s)"
                       + (f", {len(res.invalid)} écartée(s) automatiquement (ids inconnus ou format invalide)"
                          if res.invalid else ""))


def _decide(pid: str, status: str) -> None:
    s = load_store(PROPOSALS_PATH)
    s.decide(pid, status)  # type: ignore[arg-type]
    save_store(s, PROPOSALS_PATH)


valid_ids = {s.step_id for s in ex.steps} | {f.feedback_id for f in ex.persona_step_feedback} | \
            {u.user_need_id for u in ex.user_needs} | {u.use_case_id for u in ex.use_cases} | \
            {p.persona_id for p in ex.personas}
shown = [p for p in store.proposals if all(t in valid_ids for t in p.target_ids)]
if not shown:
    st.caption("Aucune proposition pour cet export.")
for p in shown:
    with st.container(border=True):
        head, act = st.columns([0.75, 0.25])
        with head:
            st.markdown(badges(p.kind, p.status, f"origin = {p.origin}"), unsafe_allow_html=True)
            st.markdown(f"**{p.title}**  \n{p.text}")
            st.markdown(f"<span class='muted'>{escape(p.rationale)}</span><br>"
                        f"<span class='ids'>cibles : {' · '.join(p.target_ids)} · id {p.proposal_id}</span>",
                        unsafe_allow_html=True)
        with act:
            st.button("Accepter", key=f"acc_{p.proposal_id}", on_click=_decide, args=(p.proposal_id, "accepted"),
                      disabled=p.status == "accepted", type="primary", width="stretch")
            st.button("Rejeter", key=f"rej_{p.proposal_id}", on_click=_decide, args=(p.proposal_id, "rejected"),
                      disabled=p.status == "rejected", width="stretch")

# ============================================================ 6. chat (bonus)
st.header("6. Discuter avec le modèle")
st.caption("L'agent ne répond jamais de mémoire : il interroge le modèle via des outils "
           "(uncovered_needs, constraints_in_text_only, find_element…) et cite les ids.")
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
            with st.spinner("Interrogation du modèle…"):
                try:
                    ans = ask(make_provider(SETTINGS.llm), cached_tool_context(export_key, ex), question,
                              [{"role": t["role"], "content": t["content"]} for t in history])
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
