"""Chargement d'un export UserShaker (.zip) et génération SysML v2 par use case.

Version de départ pour l'interface. Le générateur ci-dessous est volontairement simple :
il sera remplacé par src/p9/generate (templates Jinja2) sans changer l'interface,
tant que les fonctions gardent la même signature.
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field

import pandas as pd

SHEETS = [
    "meta", "use_cases", "step_groups", "steps", "step_triggers", "personas",
    "use_case_personas", "persona_step_feedback", "components", "component_states",
    "use_case_components", "step_component_states", "step_unused_components",
    "user_needs", "step_user_needs",
]
REQUIRED = ["use_cases", "steps", "personas", "components", "user_needs"]
COMMON_PKG = "ESpaceCommon"


# ---------------------------------------------------------------- chargement

@dataclass
class Export:
    sheets: dict[str, pd.DataFrame]
    missing: list[str]
    meta: dict[str, str]
    checks: pd.DataFrame          # feuille, attendu, chargé, ok
    fk_errors: list[str] = field(default_factory=list)

    def df(self, name: str) -> pd.DataFrame:
        return self.sheets.get(name, pd.DataFrame())


def _sheet_name(path: str) -> str | None:
    """Retrouve la feuille d'un fichier, même dans un sous-dossier ou avec un préfixe 'xxxx-'."""
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not base.endswith(".csv") or base.startswith("._"):
        return None
    stem = base[:-4]
    # nom exact d'abord, puis préfixe séparé par '-' (évite components ≠ use_case_components)
    if stem in SHEETS:
        return stem
    for s in sorted(SHEETS, key=len, reverse=True):
        if stem.endswith("-" + s):
            return s
    return None


def load_zip(data: bytes) -> Export:
    sheets: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = _sheet_name(info.filename)
            if not name or info.is_dir():
                continue
            raw = z.read(info)
            # utf-8-sig : component_states.csv commence par un BOM
            sheets[name] = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")

    missing = [s for s in SHEETS if s not in sheets]
    meta = {}
    if "meta" in sheets:
        meta = dict(zip(sheets["meta"]["key"], sheets["meta"]["value"]))

    rows = []
    for s in SHEETS:
        if s == "meta":
            continue
        exp = meta.get(f"count_{s}")
        got = len(sheets[s]) if s in sheets else None
        rows.append({"feuille": s, "attendu": exp, "chargé": got,
                     "ok": exp is not None and got is not None and int(exp) == got})
    ex = Export(sheets, missing, meta, pd.DataFrame(rows))
    ex.fk_errors = _check_fk(ex)
    return ex


FK = [  # (feuille, colonne, feuille cible, colonne cible)
    ("steps", "use_case_id", "use_cases", "use_case_id"),
    ("steps", "group_id", "step_groups", "group_id"),
    ("step_groups", "use_case_id", "use_cases", "use_case_id"),
    ("step_triggers", "step_id", "steps", "step_id"),
    ("step_triggers", "component_id", "components", "component_id"),
    ("step_triggers", "state_id", "component_states", "state_id"),
    ("use_case_personas", "persona_id", "personas", "persona_id"),
    ("persona_step_feedback", "step_id", "steps", "step_id"),
    ("persona_step_feedback", "persona_id", "personas", "persona_id"),
    ("component_states", "component_id", "components", "component_id"),
    ("step_component_states", "state_id", "component_states", "state_id"),
    ("step_user_needs", "user_need_id", "user_needs", "user_need_id"),
]


def _check_fk(ex: Export) -> list[str]:
    errs = []
    for src, col, dst, dcol in FK:
        a, b = ex.df(src), ex.df(dst)
        if a.empty or b.empty or col not in a or dcol not in b:
            continue
        vals = set(a[col]) - {""}
        bad = vals - set(b[dcol])
        if bad:
            errs.append(f"{src}.{col} → {dst} : {len(bad)} id introuvable(s)")
    return errs


# ---------------------------------------------------------------- vues pour l'UI

def use_case_summary(ex: Export) -> list[dict]:
    uc = ex.df("use_cases").copy()
    uc["position"] = uc["position"].astype(int)
    steps, groups = ex.df("steps"), ex.df("step_groups")
    ucp, pers = ex.df("use_case_personas"), ex.df("personas")
    sun, needs = ex.df("step_user_needs"), ex.df("user_needs")
    out = []
    for _, u in uc.sort_values("position").iterrows():
        st = steps[steps.use_case_id == u.use_case_id].copy()
        st["position"] = st["position"].astype(int)
        st = st.sort_values("position")
        g = groups[groups.use_case_id == u.use_case_id]
        gname = dict(zip(g.group_id, g.name))
        st["phase"] = st["group_id"].map(gname).fillna("")
        pid = ucp[ucp.use_case_id == u.use_case_id].persona_id
        p = pers[pers.persona_id.isin(pid)]
        nid = sun[sun.step_id.isin(st.step_id)].user_need_id.unique()
        n = needs[needs.user_need_id.isin(nid)]
        out.append({
            "id": u.use_case_id, "position": u.position, "name": u["name"],
            "description": u.description, "story": u.story, "status": u.status,
            "phases": [gname[k] for k in g.sort_values("position").group_id],
            "steps": st[["position", "phase", "name", "trigger_text"]],
            "personas": p[["persona_id", "name", "age_category", "color"]].to_dict("records"),
            "needs": n["name"].tolist(),
            "emotions": _emotions(ex, st, p),
        })
    return out


def _emotions(ex: Export, steps: pd.DataFrame, pers: pd.DataFrame) -> pd.DataFrame:
    fb = ex.df("persona_step_feedback")
    if fb.empty:
        return pd.DataFrame()
    fb = fb[fb.step_id.isin(steps.step_id) & (fb.is_present.str.lower() == "true")].copy()
    fb["emotion_score"] = pd.to_numeric(fb.emotion_score, errors="coerce")
    fb["step"] = fb.step_id.map(dict(zip(steps.step_id, steps.position.astype(str) + ". " + steps.name)))
    fb["persona"] = fb.persona_id.map(dict(zip(pers.persona_id, pers.name)))
    t = fb.pivot_table(index="step", columns="persona", values="emotion_score", aggfunc="mean")
    order = (steps.position.astype(str) + ". " + steps.name).tolist()
    return t.reindex([s for s in order if s in t.index])


# ---------------------------------------------------------------- génération SysML v2

def ident(text: str, upper: bool = False) -> str:
    """'Léa starts a game' → 'leaStartsAGame' (ou 'LeaStartsAGame')."""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", t) or ["x"]
    s = words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
    if upper:
        s = s[:1].upper() + s[1:]
    if s[0].isdigit():
        s = "_" + s
    return s


def _str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _doc(s: str, ind: str) -> str:
    s = str(s or "").strip().replace("*/", "* /")
    return f"{ind}doc /* {s} */\n" if s else ""


def _ref(i: str, kind: str, ind: str) -> str:
    return f"{ind}@UserShakerRef {{ id = {_str(i)}; kind = {_str(kind)}; }}\n"


class Names:
    """Identifiants uniques et stables par portée."""
    def __init__(self):
        self.used: dict[str, set] = {}

    def get(self, scope: str, text: str, upper: bool = False) -> str:
        base = ident(text, upper)
        used = self.used.setdefault(scope, set())
        name, k = base, 2
        while name in used:
            name, k = f"{base}{k}", k + 1
        used.add(name)
        return name


def _common_names(ex: Export):
    n = Names()
    pers = {r.persona_id: n.get("pkg", r["name"]) for _, r in ex.df("personas").iterrows()}
    comps = {r.component_id: n.get("pkg", r["name"], upper=True) for _, r in ex.df("components").iterrows()}
    needs = {r.user_need_id: n.get("pkg", r["name"], upper=True) for _, r in ex.df("user_needs").iterrows()}
    return pers, comps, needs


def generate_common(ex: Export) -> tuple[str, list[dict]]:
    """Éléments partagés par tous les use cases : un seul fichier, importé par chacun."""
    pers, comps, needs = _common_names(ex)
    trace = []
    L = [f"package {COMMON_PKG} {{\n",
         "    private import ScalarValues::*;\n\n",
         "    metadata def UserShakerRef {\n        attribute id : String;\n        attribute kind : String;\n    }\n\n",
         "    part def Persona {\n        attribute ageCategory : String;\n    }\n\n"]
    for _, p in ex.df("personas").iterrows():
        nm = pers[p.persona_id]
        L.append(f"    part {nm} : Persona {{\n")
        L.append(_ref(p.persona_id, "persona", " " * 8))
        L.append(_doc(f"{p['name']} ({p.age_category}). {p.biography}", " " * 8))
        L.append(f"        attribute redefines ageCategory = {_str(p.age_category)};\n    }}\n")
        trace.append({"usershaker_id": p.persona_id, "kind": "persona", "sysml": f"{COMMON_PKG}::{nm}"})
    L.append("\n")
    for _, c in ex.df("components").iterrows():
        nm = comps[c.component_id]
        L.append(f"    part def {nm} {{\n")
        L.append(_ref(c.component_id, f"component/{c.role}", " " * 8))
        L.append(_doc(c.description, " " * 8))
        L.append("    }\n")
        trace.append({"usershaker_id": c.component_id, "kind": "component", "sysml": f"{COMMON_PKG}::{nm}"})
    L.append("\n    part def ESpace {\n")
    for _, c in ex.df("components").iterrows():
        nm = comps[c.component_id]
        L.append(f"        part {nm[:1].lower() + nm[1:]} : {nm};\n")
    L.append("    }\n\n")
    for _, u in ex.df("user_needs").iterrows():
        nm = needs[u.user_need_id]
        L.append(f"    requirement def {nm} {{\n")
        L.append(_ref(u.user_need_id, "user_need", " " * 8))
        L.append(_doc(u["name"] + (". " + u.description if u.description else ""), " " * 8))
        L.append("    }\n")
        trace.append({"usershaker_id": u.user_need_id, "kind": "user_need", "sysml": f"{COMMON_PKG}::{nm}"})
    L.append("}\n")
    return "".join(L), trace


def generate_use_case(ex: Export, uc_id: str) -> tuple[str, str, list[dict]]:
    """Renvoie (nom de fichier, contenu .sysml, lignes de traçabilité) pour un use case."""
    pers, _, needs = _common_names(ex)
    u = ex.df("use_cases").set_index("use_case_id").loc[uc_id]
    pkg = f"UC{u.position}_{ident(u['name'], upper=True)}"
    ucdef = ident(u["name"], upper=True)
    steps = ex.df("steps")
    st = steps[steps.use_case_id == uc_id].copy()
    st["p"] = st.position.astype(int)
    st = st.sort_values("p")
    groups = dict(zip(ex.df("step_groups").group_id, ex.df("step_groups")["name"]))
    pids = ex.df("use_case_personas").query("use_case_id == @uc_id").persona_id.tolist()
    fb = ex.df("persona_step_feedback")
    pname = dict(zip(ex.df("personas").persona_id, ex.df("personas")["name"]))
    sun = ex.df("step_user_needs")

    n = Names()
    trace = [{"usershaker_id": uc_id, "kind": "use_case", "sysml": f"{pkg}::{ucdef}"}]
    L = [f"package {pkg} {{\n", f"    private import {COMMON_PKG}::*;\n\n",
         f"    use case def {ucdef} {{\n", _ref(uc_id, "use_case", " " * 8), _doc(u.story or u.description, " " * 8),
         "        subject vehicle : ESpace;\n"]
    for pid in pids:
        if pid in pers:
            L.append(f"        actor {pers[pid]} : Persona;\n")
    L.append("\n")
    act = {}
    for _, s in st.iterrows():
        a = n.get("uc", s["name"])
        act[s.step_id] = a
        L.append(f"        action {a} {{\n")
        L.append(_ref(s.step_id, "step", " " * 12))
        phase = groups.get(s.group_id, "")
        L.append(_doc(f"[{phase}] {s.description}" if phase else s.description, " " * 12))
        if s.trigger_text:
            L.append(f"            comment Trigger /* {str(s.trigger_text).replace('*/', '* /')} */\n")
        for _, f in fb[(fb.step_id == s.step_id) & (fb.is_present.str.lower() == "true")].iterrows():
            txt = f"{pname.get(f.persona_id, '?')} | action: {f.action} | insight: {f.insight} | emotion: {f.emotion_score}"
            L.append(f"            comment /* {txt.replace('*/', '* /')} */\n")
        L.append("        }\n")
        trace.append({"usershaker_id": s.step_id, "kind": "step", "sysml": f"{pkg}::{ucdef}::{a}"})
    L.append("\n")
    order = [act[i] for i in st.step_id]
    for a, b in zip(order, order[1:]):
        L.append(f"        first {a} then {b};\n")
    L.append("    }\n\n")
    for _, r in sun[sun.step_id.isin(st.step_id)].iterrows():
        if r.user_need_id in needs:
            L.append(f"    dependency from {ucdef}::{act[r.step_id]} to {COMMON_PKG}::{needs[r.user_need_id]};\n")
    L.append("}\n")
    return f"{pkg}.sysml", "".join(L), trace


def coverage(ex: Export, uc_ids: list[str], trace: pd.DataFrame) -> pd.DataFrame:
    """Ce qui est traduit dans les fichiers générés vs ce que contient l'export pour ces use cases."""
    st = ex.df("steps")[ex.df("steps").use_case_id.isin(uc_ids)]
    pers = ex.df("use_case_personas")[ex.df("use_case_personas").use_case_id.isin(uc_ids)].persona_id.unique()
    needs = ex.df("step_user_needs")[ex.df("step_user_needs").step_id.isin(st.step_id)].user_need_id.unique()
    trig = ex.df("step_triggers")[ex.df("step_triggers").step_id.isin(st.step_id)]
    ids = set(trace.usershaker_id)
    rows = [
        ("Use cases", len(uc_ids), sum(i in ids for i in uc_ids), ""),
        ("Steps", len(st), sum(i in ids for i in st.step_id), ""),
        ("Personas", len(pers), sum(i in ids for i in pers), ""),
        ("User needs servis", len(needs), sum(i in ids for i in needs), ""),
        ("Triggers / conditions", len(trig), 0, "texte en commentaire ; transitions à générer (T24)"),
    ]
    df = pd.DataFrame(rows, columns=["élément", "dans l'export", "traduit", "note"])
    df["couverture"] = (df["traduit"] / df["dans l'export"].replace(0, pd.NA) * 100).round().astype("Int64").astype(str) + " %"
    return df
