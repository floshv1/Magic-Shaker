"""Sortie « CATIA Community » : un fichier .sysml autonome et compact par use case.

CATIA Magic SysML v2 Community Edition 2026x impose des limites constatées en test :
  - l'éditeur textuel refuse plus de 20 000 caractères (par espace de noms racine) ;
  - le modèle est limité à 500 éléments majeurs ;
  - le menu Fichier n'a pas « Import From » : on colle le texte dans l'éditeur textuel, puis Alt+S.
ESpaceCommon.sysml (≈ 64 Ko) n'y entre donc pas, et un use case qui l'importe ne se résout pas.

Ici chaque use case devient un package autonome qui ne contient que ce qu'il utilise : ses
personas et composants, leurs state def (états sous leur nom d'origine), les transitions
déduites de ce use case, les besoins servis et des vues CATIA prêtes à afficher. Le mapping
est celui du modèle complet (build.py), restreint et allégé. Le modèle complet (out/sysml/,
out/model.sysml) reste la référence validée par le parseur OMG ; ce que ce fichier laisse de
côté figure dans sa matrice de traçabilité (rien n'est perdu en silence).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

from p9.config import CatiaSettings, GenerateSettings, load_settings
from p9.ingest import Export
from p9.ingest import models as m

from .build import (
    CommonNames, _states_by_component, build_states, build_use_case, by_pos, compute_common_names, ignored,
    join_doc, library_text, status_for, use_case_package,
)
from .naming import Namer, lower_first
from .render import Generated, _normalize, make_env
from .trace import TraceEntry

CATIA_DIR = "catia_community"   # dossier des fichiers CATIA Community, dans out/ et dans le zip

# Vues symboliques de la bibliothèque DS_Views de CATIA (types relevés dans CATIA 2026x).
VIEW_GENERAL = "DS_Views::SymbolicViews::gv"
VIEW_STATES = "DS_Views::SymbolicViews::stv"
VIEW_FLOW = "DS_Views::SymbolicViews::afv"

OUT_OF_SCOPE = "hors du use case : absent du fichier CATIA (present dans le modele complet)"
UNUSED_STATE = "etat non atteint dans ce use case : absent du fichier CATIA (present dans le modele complet)"
# États, transitions, gardes, événements et acteurs n'ont pas de UserShakerRef dans le fichier CATIA :
# un compartiment de métadonnée par symbole encombre les diagrammes et coûte des caractères.
BY_NAME = "sans UserShakerRef dans le fichier CATIA : trace par nom qualifie"

# Commentaires de step du modèle complet que le fichier CATIA n'émet pas.
DROPPED_COMMENTS = {
    "blueprint": "grille du blueprint non emise en commentaire (representee par les etats et transitions)",
    "imageUrl": "lien image non emis (presentation, ouvrable seulement dans UserShaker)",
}

# Réductions, appliquées dans cet ordre (de la moins coûteuse en information à la plus coûteuse)
# tant que le fichier dépasse le budget de caractères. Chacune est notée dans la matrice.
TRIMS = {
    "dependency_refs": "UserShakerRef des dependency non emis (id = paire step:need, deux extremites tracees)",
    "kind": "attribut kind de UserShakerRef non emis (le kind reste dans la matrice)",
    "story": "story du use case non emise",
    "step_descriptions": "description du step non emise",
    "persona_bios": "biographie du persona non emise",
    "structured_triggers": "commentaire structuredTrigger non emis (accept / if des transitions et item def evenements)",
    "component_descriptions": "description du composant non emise",
    "feedback_non_negative": "feedback a emotion >= 0 non emis (seuls les points de douleur restent)",
    "feedback": "commentaire feedback non emis",
}

# Estimation grossière du nombre d'éléments (définitions et usages), pour la limite de 500.
_DECL = re.compile(
    r"^\s*(?:then\s+)?(?:abstract\s+)?(?:part|item|state|transition|attribute|action|actor|subject|requirement"
    r"|use case|dependency|view|metadata def|entry|:>>)\b", re.M)


@dataclass
class CatiaFile:
    name: str               # UC1_WelcomeOnBoard.sysml
    text: str
    gen: Generated          # fichier + traçabilité + use case, pour build_matrix
    trims: list[str]        # réductions appliquées pour tenir dans le budget

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def declarations(self) -> int:
        return len(_DECL.findall(self.text))

    @property
    def refs(self) -> int:
        return self.text.count("@UserShakerRef")


def quoted(name: str) -> str:
    """Nom SysML non restreint : garde le nom d'origine ('Welcome pulse'), lisible dans les vues."""
    return "'" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _feedback_comment(ex: Export, feedback_ids: list[str]) -> str:
    rows = {f.feedback_id: f for f in ex.persona_step_feedback}
    personas = ex.by_id("personas")
    lines = []
    for fid in feedback_ids:
        f = rows[fid]
        pname = personas[f.persona_id].name if f.persona_id in personas else f.persona_id
        score = "" if f.emotion_score is None else f" ({f.emotion_score:+d})" if f.emotion_score else " (0)"
        absent = "" if f.is_present else " [absent]"
        lines.append(join_doc(f"{pname}{score}{absent} :", f.action, f"/ {f.insight}" if f.insight else ""))
    return " | ".join(lines)          # une ligne : CATIA compte l'indentation de chaque ligne de commentaire


def build_catia_use_case(
    ex: Export, cfg: GenerateSettings, names: CommonNames, uc: m.UseCase, catia: CatiaSettings,
    trims: frozenset[str] = frozenset(),
) -> tuple[dict, list[TraceEntry]]:
    pkg = use_case_package(uc)
    pcfg = dataclasses.replace(cfg, common_package=pkg)       # tout vit dans le package du use case
    trace: list[TraceEntry] = []
    steps = ex.steps_of(uc.use_case_id)
    step_by_id = {s.step_id: s for s in steps}
    grid_comps = {r.component_id for r in ex.step_component_states if r.step_id in step_by_id}

    # ------------------------------------------------------------------ périmètre du use case
    persona_ids = {r.persona_id for r in ex.use_case_personas if r.use_case_id == uc.use_case_id}
    comp_ids = ({r.component_id for r in ex.use_case_components if r.use_case_id == uc.use_case_id}
                | grid_comps | {t.component_id for t in ex.step_triggers if t.step_id in step_by_id})
    need_ids = {r.user_need_id for r in ex.step_user_needs if r.step_id in step_by_id}
    event_states = {t.state_id for t in ex.step_triggers if t.step_id in step_by_id and t.kind == "trigger"}

    if ex.meta.project_id:
        trace.append(TraceEntry(usershaker_id=ex.meta.project_id, kind="project", sysml_qualified_name=pkg))

    personas = []
    for p in by_pos(ex.personas, "persona_id"):
        nm = names.persona[p.persona_id]
        if p.persona_id not in persona_ids:
            trace.append(TraceEntry(usershaker_id=p.persona_id, kind="persona", status="not_mapped", reason=OUT_OF_SCOPE))
            continue
        bio = "" if "persona_bios" in trims else p.biography
        personas.append({"name": nm, "id": p.persona_id, "doc": join_doc(f"{p.name}.", bio, library_text(p)),
                         "attrs": [(k, v) for k, v in (("ageCategory", p.age_category), ("gender", p.gender)) if v]})
        st, why = status_for(p)
        why = join_doc(why, ignored(p, ("color",)), sep="; ")
        if "persona_bios" in trims and p.biography:
            st, why = "partial", join_doc(why, TRIMS["persona_bios"], sep="; ")
        trace.append(TraceEntry(usershaker_id=p.persona_id, kind="persona", sysml_qualified_name=f"{pkg}::{nm}",
                                status=st, reason=why))

    # ------------------------------------------------------------------ états et transitions (ce use case seul)
    # Chaque state def ne garde que les états atteints dans ce use case ; `entry` mène à l'état du
    # composant au premier step où il apparaît (et non au premier état de sa liste).
    state_trace: list[TraceEntry] = []
    views = build_states(ex, pcfg, names, [uc.use_case_id], state_trace)
    states_by_comp = _states_by_component(ex)
    state_by_id: dict[str, m.ComponentState] = ex.by_id("component_states")  # type: ignore[assignment]
    step_rank = {s.step_id: s.position for s in steps}
    uc_grid = sorted((r for r in ex.step_component_states if r.step_id in step_by_id),
                     key=lambda r: (step_rank[r.step_id], r.sequence, r.component_id))
    reached: dict[str, list[str]] = {}    # component_id → state_ids atteints, dans l'ordre du parcours
    for r in uc_grid:
        if r.state_id in state_by_id and state_by_id[r.state_id].component_id == r.component_id:
            seen = reached.setdefault(r.component_id, [])
            if r.state_id not in seen:
                seen.append(r.state_id)
    used_states = {sid for sids in reached.values() for sid in sids}
    renamed: dict[str, str] = {}          # nom qualifié camelCase → nom qualifié du fichier CATIA
    for cid in list(views):
        if cid not in comp_ids or cid not in reached:
            del views[cid]                # composant absent ou jamais placé dans la grille de ce use case
            continue
        v = views[cid]
        v.states = [st for st in v.states if st["id"] in used_states]
        v.initial = names.state[reached[cid][0]]
        for t in v.transitions:
            if t.accept:
                t.accept = t.accept.rsplit("::", 1)[-1]
        for st in v.states:
            st["doc"] = state_by_id[st["id"]].description or ""
        labels = [s.name.strip() for s in states_by_comp[cid]]
        if not all(labels) or len(set(labels)) != len(labels):
            continue                      # noms d'origine vides ou en double : identifiants camelCase
        q = {names.state[s.state_id]: quoted(s.name.strip()) for s in states_by_comp[cid]}
        v.initial = q[v.initial]
        for st in v.states:
            renamed[f"{pkg}::{v.name}::{st['name']}"] = f"{pkg}::{v.name}::{q[st['name']]}"
            st["name"] = q[st["name"]]
        for t in v.transitions:
            t.source, t.target = q[t.source], q[t.target]
    for e in state_trace:
        if e.kind == "component_state":
            st_row = state_by_id[e.usershaker_id]
            if st_row.component_id not in comp_ids:
                e.sysml_qualified_name, e.status, e.reason = "", "not_mapped", OUT_OF_SCOPE
            elif e.usershaker_id not in used_states:
                e.sysml_qualified_name, e.status, e.reason = "", "not_mapped", UNUSED_STATE
            else:
                e.reason = join_doc(e.reason, BY_NAME, sep="; ")
        elif e.kind == "step_component_state" and e.status != "not_mapped":
            e.reason = join_doc(e.reason, BY_NAME, sep="; ")
        elif e.kind == "step_trigger" and e.status == "partial" and "structured_triggers" in trims:
            if "condition conservee" in e.reason:       # la condition n'était que dans le commentaire
                e.status = "not_mapped"
            e.reason = join_doc(e.reason, TRIMS["structured_triggers"], sep="; ")
        e.sysml_qualified_name = renamed.get(e.sysml_qualified_name, e.sysml_qualified_name)
    trace.extend(state_trace)

    # ------------------------------------------------------------------ composants
    comp_rows = by_pos(ex.components, "component_id")
    comp_by_id = {c.component_id: c for c in comp_rows}
    part_namer = Namer()
    part_name: dict[str, str] = {}
    components = []
    for c in comp_rows:
        nm = names.component_def[c.component_id]
        if c.component_id not in comp_ids:
            trace.append(TraceEntry(usershaker_id=c.component_id, kind="component", status="not_mapped",
                                    reason=OUT_OF_SCOPE))
            continue
        part_name[c.component_id] = part_namer.take(names.component_part[c.component_id])
        parent = comp_by_id.get(c.parent_component_id or "")
        short = "component_descriptions" in trims
        components.append({
            "name": nm, "id": c.component_id,
            "doc": join_doc(f"{c.name} ({c.role}).", "" if short else c.description,
                            f"Categorie : {c.category}." if c.category else "",
                            f"Fait partie de : {parent.name}." if parent else "", "" if short else library_text(c)),
            "state_def": views[c.component_id].name if c.component_id in views else None,
        })
        st, why = status_for(c)
        if short and (c.description or library_text(c)):
            st, why = "partial", join_doc(why, TRIMS["component_descriptions"], sep="; ")
        trace.append(TraceEntry(usershaker_id=c.component_id, kind="component", sysml_qualified_name=f"{pkg}::{nm}",
                                status=st, reason=why))
    system_parts = [(part_name[c["id"]], c["name"]) for c in components]
    state_defs = [views[c["id"]] for c in components if c["id"] in views]
    events = [{"name": names.event[sid], "id": sid}
              for sid in sorted(event_states & names.event.keys(), key=lambda s: names.event[s])]

    # ------------------------------------------------------------------ besoins servis
    requirements = []
    for u in by_pos(ex.user_needs, "user_need_id"):
        nm = names.requirement[u.user_need_id]
        if u.user_need_id not in need_ids:
            trace.append(TraceEntry(usershaker_id=u.user_need_id, kind="user_need", status="not_mapped",
                                    reason=OUT_OF_SCOPE))
            continue
        ref = f"Reference : {u.reference_url}." if u.reference_url else ""
        requirements.append({"name": nm, "id": u.user_need_id,
                             "doc": join_doc(u.name + ("." if not u.name.endswith(".") else ""),
                                             u.description, ref, library_text(u))})
        st, why = status_for(u)
        trace.append(TraceEntry(usershaker_id=u.user_need_id, kind="user_need", sysml_qualified_name=f"{pkg}::{nm}",
                                status=st, reason=why))

    # ------------------------------------------------------------------ use case (mapping du modèle complet, allégé)
    uctx, utrace = build_use_case(ex, pcfg, names, uc)
    groups = ex.by_id("step_groups")
    grid_steps = {r.step_id for r in ex.step_component_states}
    fb_by_id = {f.feedback_id: f for f in ex.persona_step_feedback}

    def feedback_dropped(fid: str) -> str:
        """Raison si ce feedback n'est pas émis, sinon ''."""
        if "feedback" in trims:
            return TRIMS["feedback"]
        score = fb_by_id[fid].emotion_score
        if "feedback_non_negative" in trims and (score is None or score >= 0):
            return TRIMS["feedback_non_negative"]
        return ""

    def adapt(item: dict) -> None:
        step = step_by_id[item["id"]]
        phase = groups[step.group_id].name if step.group_id in groups else None
        desc = "" if "step_descriptions" in trims else step.description
        item["doc"] = join_doc(f"[{phase}]" if phase else "", f"Step {step.position} : {step.name}.", desc)
        dropped = set(DROPPED_COMMENTS) | ({"structuredTrigger"} if "structured_triggers" in trims else set())
        comments = [(n, t) for n, t in item["comments"] if n not in dropped]
        kept = [f["id"] for f in item["feedback"] if not feedback_dropped(f["id"])]
        if kept:
            comments.append(("feedback", _feedback_comment(ex, kept)))
        item["comments"], item["feedback"] = comments, []

    for item in uctx["flow"]:
        for s in item["steps"] if item["kind"] == "phase" else [item]:
            adapt(s)
    for d in uctx["dependencies"]:
        d["target"] = d["target"].rsplit("::", 1)[-1]
    story = "" if "story" in trims else uctx["story"]

    for e in utrace:
        if e.kind == "use_case_component":
            cid = e.usershaker_id.split(":", 1)[1]
            e.sysml_qualified_name = f"{pkg}::{cfg.system_name}::{part_name[cid]}" if cid in part_name else ""
            e.reason = "composant du use case : part de ESpace dans ce fichier"
        elif e.kind == "use_case_persona":
            e.reason = join_doc(e.reason, BY_NAME, sep="; ")
        elif e.kind == "persona_step_feedback" and feedback_dropped(e.usershaker_id):
            e.sysml_qualified_name, e.status = "", "not_mapped"
            e.reason = join_doc(e.reason, feedback_dropped(e.usershaker_id), sep="; ")
        elif e.kind == "persona_step_feedback":
            e.status = "partial"
            e.sysml_qualified_name = e.sysml_qualified_name.rsplit("::", 1)[0] + "::feedback"
            e.reason = join_doc(e.reason, "CATIA Community : action, insight et score dans le commentaire feedback "
                                          "du step", sep="; ")
        elif e.kind == "step":
            step = step_by_id[e.usershaker_id]
            notes = [DROPPED_COMMENTS["imageUrl"]] if step.image_url else []
            if step.step_id in grid_steps:
                notes.append(DROPPED_COMMENTS["blueprint"])
            if "step_descriptions" in trims and step.description:
                e.status = "partial"
                notes.append(TRIMS["step_descriptions"])
            e.reason = join_doc(e.reason, *notes, sep="; ")
        elif e.kind == "use_case" and "story" in trims and uc.story:
            e.status, e.reason = "partial", join_doc(e.reason, TRIMS["story"], sep="; ")
        elif e.kind == "step_user_need" and "dependency_refs" in trims:
            e.reason = join_doc(e.reason, TRIMS["dependency_refs"], sep="; ")
    trace.extend(utrace)

    # ------------------------------------------------------------------ vues CATIA
    taken = ({"UserShakerRef", "Persona", cfg.system_name, uctx["name"]} | {p["name"] for p in personas}
             | {c["name"] for c in components} | {v.name for v in state_defs} | {e["name"] for e in events}
             | {r["name"] for r in requirements})
    vnamer = Namer(taken)
    view_list = [
        {"name": vnamer.take("structureView"), "type": VIEW_GENERAL, "expose": [cfg.system_name]},
        {"name": vnamer.take("flowView"), "type": VIEW_FLOW, "expose": [uctx["name"]]},
    ]
    changing = [v.name for v in state_defs if v.transitions]
    if changing:
        view_list.append({"name": vnamer.take("statesView"), "type": VIEW_STATES, "expose": changing})
    if requirements:
        view_list.append({"name": vnamer.take("needsView"), "type": VIEW_GENERAL,
                          "expose": [r["name"] for r in requirements]})

    applied = [t for t in TRIMS if t in trims]
    ctx = {
        "package": pkg, "project_id": ex.meta.project_id or "",
        "doc": join_doc(
            f"Sortie CATIA Community du use case {uc.position} '{uc.name}' : fichier autonome (l'editeur textuel "
            f"de CATIA Community refuse plus de {catia.char_limit} caracteres).",
            "Modele complet : out/model.sysml.",
            ("Reductions appliquees : " + ", ".join(applied) + ".") if applied else ""),
        "with_kind": "kind" not in trims, "dependency_refs": "dependency_refs" not in trims,
        "personas": personas, "components": components, "system": cfg.system_name, "system_parts": system_parts,
        "events": events, "state_defs": state_defs, "requirements": requirements,
        "name": uctx["name"], "id": uctx["id"], "uc_doc": uctx["doc"], "story": story,
        "subject": uctx["subject"], "actors": uctx["actors"], "flow": uctx["flow"],
        "dependencies": uctx["dependencies"], "views": view_list,
    }
    return ctx, trace


def generate_catia(ex: Export, selected: list[str] | None = None, cfg: GenerateSettings | None = None,
                   catia: CatiaSettings | None = None) -> list[CatiaFile]:
    """Un fichier CATIA autonome par use case sélectionné (tous si None), réduit jusqu'au budget."""
    if cfg is None or catia is None:
        s = load_settings()
        cfg, catia = cfg or s.generate, catia or s.catia
    wanted = set(selected) if selected is not None else None
    env = make_env()
    template = env.get_template("catia_use_case.sysml.j2")
    names = compute_common_names(ex, cfg)
    out: list[CatiaFile] = []
    for uc in ex.use_cases_sorted():
        if wanted is not None and uc.use_case_id not in wanted:
            continue
        trims: list[str] = []
        while True:
            ctx, trace = build_catia_use_case(ex, cfg, names, uc, catia, frozenset(trims))
            text = _normalize(template.render(**ctx))
            if len(text) <= catia.char_budget or len(trims) == len(TRIMS):
                break
            trims.append(next(t for t in TRIMS if t not in trims))
        name = f"{use_case_package(uc)}.sysml"
        for e in trace:
            e.file = name
        out.append(CatiaFile(name=name, text=text, trims=trims,
                             gen=Generated(files={name: text}, trace=trace, selected=[uc.use_case_id])))
    return out


def write_catia(files: list[CatiaFile], out_dir: Path) -> list[Path]:
    """Écrit out_dir/catia_community/<UC>.sysml (et retire les anciens fichiers de ce dossier)."""
    catia_dir = out_dir / CATIA_DIR
    catia_dir.mkdir(parents=True, exist_ok=True)
    keep = {f.name for f in files}
    for old in catia_dir.glob("*.sysml"):
        if old.name not in keep:
            old.unlink()
    paths = []
    for f in files:
        p = catia_dir / f.name
        p.write_bytes(f.text.encode("utf-8"))
        paths.append(p)
    return paths
