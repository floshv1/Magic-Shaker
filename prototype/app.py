"""Interface Projet 9 : import UserShaker (.zip) → choix des use cases → export SysML v2.

Lancer :  streamlit run app.py
"""
import io
import zipfile

import pandas as pd
import streamlit as st

import core

st.set_page_config(page_title="UserShaker → SysML v2", layout="wide")

st.markdown("""
<style>
.uc-badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:rgba(127,127,127,.15);margin-right:4px}
.persona{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;margin:0 4px 4px 0;border:1px solid}
.muted{color:rgba(127,127,127,.95);font-size:13px}
</style>
""", unsafe_allow_html=True)

st.title("UserShaker → SysML v2")
st.caption("Importer l'export → vérifier les use cases → choisir → exporter les fichiers .sysml")

# ------------------------------------------------------------------ 1. import
st.header("1. Importer l'export UserShaker")
up = st.file_uploader("Déposez le .zip contenant les CSV", type=["zip"])
if not up:
    st.info("En attente d'un fichier .zip. Les CSV peuvent être à la racine ou dans un sous-dossier.")
    st.stop()

try:
    ex = core.load_zip(up.getvalue())
except Exception as e:  # zip corrompu, CSV illisible…
    st.error(f"Impossible de lire ce fichier : {e}")
    st.stop()

missing_req = [s for s in core.REQUIRED if s in ex.missing]
if missing_req:
    st.error("Feuilles obligatoires absentes du zip : " + ", ".join(missing_req))
    st.stop()

ok_counts = bool(ex.checks["ok"].all())
c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
c1.metric("Projet", ex.meta.get("project_name", "—"))
c2.metric("Use cases", len(ex.df("use_cases")))
c3.metric("Steps", len(ex.df("steps")))
c4.metric("Contrôles", "OK" if ok_counts and not ex.fk_errors else "À vérifier")
st.caption(f"Exporté le {ex.meta.get('exported_at', '?')} · schéma {ex.meta.get('schema_version', '?')} · {ex.meta.get('company_name', '')}")

with st.expander("Détail des contrôles d'import", expanded=not (ok_counts and not ex.fk_errors)):
    st.dataframe(ex.checks, hide_index=True, width="stretch")
    if ex.missing:
        st.warning("Feuilles absentes : " + ", ".join(ex.missing))
    for err in ex.fk_errors:
        st.warning(err)
    if ok_counts and not ex.fk_errors:
        st.success("Les comptes correspondent à meta.csv et toutes les clés étrangères pointent vers un objet existant.")

# ------------------------------------------------------------------ 2 + 3. use cases
st.header("2. Use cases importés")
ucs = core.use_case_summary(ex)
for u in ucs:
    st.session_state.setdefault(f"sel_{u['id']}", True)

colA, colB, _ = st.columns([1, 1, 4])
if colA.button("Tout sélectionner"):
    for u in ucs:
        st.session_state[f"sel_{u['id']}"] = True
if colB.button("Tout désélectionner"):
    for u in ucs:
        st.session_state[f"sel_{u['id']}"] = False

for u in ucs:
    with st.container(border=True):
        top = st.columns([0.06, 0.94])
        top[0].checkbox("Traduire", key=f"sel_{u['id']}", label_visibility="collapsed")
        with top[1]:
            st.subheader(f"{u['position']}. {u['name']}")
            st.markdown(f"<span class='uc-badge'>{u['status']}</span>"
                        f"<span class='uc-badge'>{len(u['steps'])} steps</span>"
                        f"<span class='uc-badge'>{len(u['personas'])} personas</span>"
                        f"<span class='uc-badge'>{len(u['needs'])} user needs</span>", unsafe_allow_html=True)
            st.write(u["description"])
            st.markdown("".join(
                f"<span class='persona' style='border-color:{p['color'] or '#888'}'>{p['name']} · {p['age_category']}</span>"
                for p in u["personas"]), unsafe_allow_html=True)
            st.markdown("<span class='muted'>Phases : " + " → ".join(u["phases"]) + "</span>", unsafe_allow_html=True)

            with st.expander("Parcours, besoins et émotions"):
                if u["story"]:
                    st.markdown(f"*{u['story']}*")
                st.dataframe(u["steps"].rename(columns={"position": "#", "phase": "phase", "name": "step",
                                                        "trigger_text": "déclencheur"}),
                             hide_index=True, width="stretch")
                left, right = st.columns(2)
                with left:
                    st.markdown("**User needs servis**")
                    for n in u["needs"]:
                        st.markdown(f"- {n}")
                with right:
                    if not u["emotions"].empty:
                        st.markdown("**Émotion par persona (-5 à +5)**")
                        st.line_chart(u["emotions"].reset_index(drop=True))

selected = [u["id"] for u in ucs if st.session_state.get(f"sel_{u['id']}", True)]

# ------------------------------------------------------------------ 4. export
st.header("3. Export SysML v2")
if not selected:
    st.info("Cochez au moins un use case à traduire.")
    st.stop()

st.write(f"**{len(selected)}** use case(s) sélectionné(s). Chaque use case donne un fichier ; "
         f"les éléments partagés (composants, personas, requirements) vont dans `{core.COMMON_PKG}.sysml`, "
         "importé par chaque fichier, pour éviter les doublons dans CATIA.")

common_src, trace = core.generate_common(ex)
files = {f"{core.COMMON_PKG}.sysml": common_src}
for uid in selected:
    fname, src, t = core.generate_use_case(ex, uid)
    files[fname] = src
    trace += t
trace_df = pd.DataFrame(trace)

st.subheader("Couverture")
st.dataframe(core.coverage(ex, selected, trace_df), hide_index=True, width="stretch")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for name, src in files.items():
        z.writestr(name, src)
    z.writestr("traceability.csv", trace_df.to_csv(index=False))
st.download_button("Télécharger tout (.zip : fichiers .sysml + traceability.csv)", buf.getvalue(),
                   file_name="sysml_export.zip", mime="application/zip", type="primary")

tabs = st.tabs(list(files))
for tab, (name, src) in zip(tabs, files.items()):
    with tab:
        st.download_button(f"Télécharger {name}", src.encode("utf-8"), file_name=name, mime="text/plain",
                           key=f"dl_{name}")
        st.code(src, language=None, line_numbers=True)

with st.expander("Matrice de traçabilité"):
    st.dataframe(trace_df, hide_index=True, width="stretch")
