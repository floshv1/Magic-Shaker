"""Construit, à partir d'un Export, les données passées aux templates Jinja2.

Toute la logique de mapping UserShaker → SysML v2 est ici ; les templates ne font que
la mise en page. Fonctions pures : même Export + même sélection → mêmes données.
Chaque élément émis produit une ligne de traçabilité (TraceEntry).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from p9.config import GenerateSettings
from p9.ingest import Export
from p9.ingest import models as m

from .naming import RESERVED, Namer, camel, lower_first, pascal
from .trace import TraceEntry, link_id

# Noms fixes déclarés dans le package commun (réservés pour le Namer).
FIXED_COMMON = {"UserShakerRef", "Persona", "PersonaFeedback", "Component"}


# =========================================================================== noms

@dataclass
class CommonNames:
    """Noms stables du package commun, calculés sur tout l'export (indépendants de la sélection)."""

    persona: dict[str, str] = field(default_factory=dict)          # persona_id → part
    component_def: dict[str, str] = field(default_factory=dict)    # component_id → part def
    component_part: dict[str, str] = field(default_factory=dict)   # component_id → part (dans le parent)
    state_def: dict[str, str] = field(default_factory=dict)        # component_id → state def
    state: dict[str, str] = field(default_factory=dict)            # state_id → state (dans le state def)
    requirement: dict[str, str] = field(default_factory=dict)      # user_need_id → requirement def
    event: dict[str, str] = field(default_factory=dict)            # state_id → item def (événement)
    use_case: dict[str, str] = field(default_factory=dict)         # use_case_id → use case def


def by_pos(rows, *keys: str):
    """Tri stable : position (ou sequence) puis id(s)."""
    return sorted(rows, key=lambda r: tuple(getattr(r, k) for k in keys))


def compute_common_names(ex: Export, cfg: GenerateSettings) -> CommonNames:
    n = CommonNames()
    namer = Namer(FIXED_COMMON | {cfg.system_name})
    for p in by_pos(ex.personas, "persona_id"):
        n.persona[p.persona_id] = namer.name(p.name)
    comps = by_pos(ex.components, "component_id")
    for c in comps:
        n.component_def[c.component_id] = namer.name(c.name, upper=True)
    states_by_comp = _states_by_component(ex)
    for c in comps:
        if states_by_comp.get(c.component_id):
            n.state_def[c.component_id] = namer.take(n.component_def[c.component_id] + "States")
    for u in by_pos(ex.user_needs, "user_need_id"):
        n.requirement[u.user_need_id] = namer.name(u.name, upper=True)
    # événements : un item def par état visé par une ligne kind=trigger
    state_by_id = ex.by_id("component_states")
    for t in by_pos(ex.step_triggers, "step_id", "position", "trigger_id"):
        if t.kind == "trigger" and t.state_id not in n.event and t.state_id in state_by_id:
            st = state_by_id[t.state_id]
            comp = n.component_def.get(st.component_id, "Component")
            n.event[t.state_id] = namer.take(f"{comp}Is{pascal(st.name)}")
    # use case def : nommés en dernier (les noms ci-dessus ne bougent pas), uniques face aux définitions
    # communes, car le fichier CATIA d'un use case met tout dans un seul package
    for uc in by_pos(ex.use_cases, "position", "use_case_id"):
        n.use_case[uc.use_case_id] = namer.name(uc.name, upper=True)
    # parts des composants, une portée par parent (ESpace ou composant objet)
    scopes: dict[str | None, Namer] = defaultdict(Namer)
    known = {c.component_id for c in comps}
    for c in comps:
        parent = c.parent_component_id if c.parent_component_id in known else None
        n.component_part[c.component_id] = scopes[parent].take(lower_first(n.component_def[c.component_id]))
    # états : une portée par state def (les gardes y vivent aussi, voir build_states)
    for cid, states in states_by_comp.items():
        sn = Namer({"entry"})
        for s in states:
            n.state[s.state_id] = sn.name(s.name)
    return n


def _states_by_component(ex: Export) -> dict[str, list[m.ComponentState]]:
    out: dict[str, list[m.ComponentState]] = defaultdict(list)
    for s in by_pos(ex.component_states, "component_id", "position", "state_id"):
        out[s.component_id].append(s)
    return dict(out)


# =========================================================================== texte

def join_doc(*parts: str | None, sep: str = " ") -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def ignored(row: m.Row, fields: tuple[str, ...]) -> str:
    """Raison listant les champs renseignés mais volontairement non traduits."""
    present = [f for f in fields if getattr(row, f, None)]
    return f"ignore (presentation) : {', '.join(present)}" if present else ""


def extra_columns(row: m.Row) -> str:
    extra = row.model_extra or {}
    present = sorted(k for k, v in extra.items() if v not in (None, ""))
    return f"colonnes inconnues non traduites : {', '.join(present)}" if present else ""


def status_for(row: m.Row) -> tuple[str, str]:
    """Une colonne inconnue renseignée rend la ligne 'partial' (rien n'est perdu en silence)."""
    extra = extra_columns(row)
    return ("partial", extra) if extra else ("mapped", "")


def library_text(row: m.Row) -> str:
    lib = getattr(row, "library_name", None) or getattr(row, "library_id", None)
    if not lib:
        return ""
    mode = getattr(row, "library_link_mode", None)
    return f"Bibliotheque : {lib}" + (f" ({mode})" if mode else "") + "."


# =========================================================================== états

@dataclass
class Guard:
    name: str
    state_id: str
    doc: str


@dataclass
class Transition:
    name: str
    ref_id: str          # step_id:component_id:sequence de l'état d'arrivée
    source: str
    target: str
    accept: str | None   # nom qualifié de l'item def événement
    guards: list[str]
    doc: str


@dataclass
class StateDefView:
    component_id: str
    name: str
    states: list[dict]
    initial: str
    guards: list[Guard] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)


def build_states(
    ex: Export, cfg: GenerateSettings, names: CommonNames, selected: list[str], trace: list[TraceEntry],
) -> dict[str, StateDefView]:
    """state def par composant + transitions déduites de la grille du blueprint.

    Règle (signalée dans la doc générée, à valider par l'expert UserShaker) :
      - pour chaque use case sélectionné et chaque composant, on suit ses états step après step ;
      - un changement d'état au début d'un step k donne une transition déclenchée par l'événement
        du step k (`accept`, ligne kind=trigger) et gardée par ses conditions (`if`, kind=condition) ;
        si le composant est lui-même celui du trigger, la transition est l'entrée externe (sans accept) ;
      - un changement à l'intérieur d'un step (sequence > 1) donne une transition de complétion.
    """
    pkg = cfg.common_package
    comps = ex.by_id("components")
    state_by_id: dict[str, m.ComponentState] = ex.by_id("component_states")  # type: ignore[assignment]
    views: dict[str, StateDefView] = {}
    for cid, states in _states_by_component(ex).items():
        if cid not in names.state_def:
            continue
        sd = names.state_def[cid]
        views[cid] = StateDefView(
            component_id=cid, name=sd, initial=names.state[states[0].state_id],
            states=[{"name": names.state[s.state_id], "id": s.state_id,
                     "doc": join_doc(s.name + ".", s.description)} for s in states],
        )
        for s in states:
            st, why = status_for(s)
            trace.append(TraceEntry(usershaker_id=s.state_id, kind="component_state",
                                    sysml_qualified_name=f"{pkg}::{sd}::{names.state[s.state_id]}",
                                    status=st, reason=why))

    triggers_by_step: dict[str, list[m.StepTrigger]] = defaultdict(list)
    for t in by_pos(ex.step_triggers, "step_id", "position", "trigger_id"):
        triggers_by_step[t.step_id].append(t)
    grid: dict[tuple[str, str], list[m.StepComponentState]] = defaultdict(list)
    for r in by_pos(ex.step_component_states, "step_id", "component_id", "sequence"):
        grid[(r.step_id, r.component_id)].append(r)

    guard_names: dict[tuple[str, str], str] = {}               # (component, state_id) → garde
    guard_namers: dict[str, Namer] = {}
    trans_namers: dict[str, Namer] = {}
    used_events: dict[str, list[str]] = defaultdict(list)       # trigger_id → transitions
    used_conditions: dict[str, list[str]] = defaultdict(list)
    grid_trace: dict[str, TraceEntry] = {}
    uc_by_id = ex.by_id("use_cases")

    def guard_for(cid: str, state_id: str) -> str:
        key = (cid, state_id)
        if key not in guard_names:
            st = state_by_id[state_id]
            comp_name = comps[st.component_id].name if st.component_id in comps else st.component_id
            namer = guard_namers.setdefault(cid, Namer({s["name"] for s in views[cid].states} | {"entry"}))
            g = namer.take(camel(comp_name) + "Is" + pascal(st.name))
            guard_names[key] = g
            views[cid].guards.append(Guard(
                name=g, state_id=state_id,
                doc=f"Vrai quand '{comp_name}' est dans l'etat '{st.name}' (condition structuree UserShaker).",
            ))
        return guard_names[key]

    for uc_id in selected:
        uc = uc_by_id[uc_id]
        steps = ex.steps_of(uc_id)
        for cid, view in sorted(views.items()):
            prev: tuple[str, str] | None = None  # (state_id, libellé du step)
            for step in steps:
                rows = grid.get((step.step_id, cid), [])
                for r in rows:
                    key = link_id(r.step_id, r.component_id, r.sequence)
                    target = names.state.get(r.state_id)
                    if target is None or state_by_id[r.state_id].component_id != cid:
                        grid_trace[key] = TraceEntry(
                            usershaker_id=key, kind="step_component_state", status="not_mapped",
                            reason="etat inconnu ou d'un autre composant")
                        continue
                    where = f"UC{uc.position} '{uc.name}', step {step.position} '{step.name}'"
                    state_qn = f"{pkg}::{view.name}::{target}"
                    if prev is None or prev[0] == r.state_id:
                        why = "etat de depart du use case" if prev is None else "etat maintenu"
                        grid_trace[key] = TraceEntry(usershaker_id=key, kind="step_component_state",
                                                     sysml_qualified_name=state_qn, reason=why)
                        prev = (r.state_id, where)
                        continue
                    accept, guards, notes = None, [], []
                    trig = triggers_by_step.get(step.step_id, [])
                    event_rows = [t for t in trig if t.kind == "trigger"]
                    cond_rows = [t for t in trig if t.kind == "condition" and t.state_id in state_by_id]
                    external = False
                    if r.sequence > 1:
                        notes.append("changement pendant le step (transition de completion)")
                    elif not event_rows:
                        notes.append("step sans trigger structure")
                    elif event_rows[0].component_id == cid:
                        external = True
                        notes.append("ce composant porte l'evenement du step (entree externe, sans accept)")
                    else:
                        if event_rows[0].state_id in names.event:
                            accept = f"{pkg}::{names.event[event_rows[0].state_id]}"
                        guards = [guard_for(cid, c.state_id) for c in cond_rows]
                    src = names.state[prev[0]]
                    tn = trans_namers.setdefault(view.name, Namer({s["name"] for s in view.states} | {"entry"}))
                    tname = tn.take(f"{src}To{target[:1].upper()}{target[1:]}")
                    t_qn = f"{pkg}::{view.name}::{tname}"
                    view.transitions.append(Transition(
                        name=tname, ref_id=key, source=src, target=target, accept=accept, guards=guards,
                        doc=join_doc(f"{where} : {state_by_id[prev[0]].name} -> {state_by_id[r.state_id].name}.",
                                     *(n + "." for n in notes)),
                    ))
                    if accept or external:
                        used_events[event_rows[0].trigger_id].append(t_qn)
                    if guards:
                        for c in cond_rows:
                            used_conditions[c.trigger_id].append(t_qn)
                    grid_trace[key] = TraceEntry(usershaker_id=key, kind="step_component_state",
                                                 sysml_qualified_name=t_qn, reason="transition deduite")
                    prev = (r.state_id, where)

    trace.extend(grid_trace[k] for k in sorted(grid_trace))

    step_pos = {s.step_id: s.position for uc in selected for s in ex.steps_of(uc)}
    for t in by_pos(ex.step_triggers, "step_id", "position", "trigger_id"):
        if t.step_id not in step_pos:
            continue
        no_transition = ("premier step du use case : pas d'etat precedent dans la grille, aucune transition deduite"
                         if step_pos[t.step_id] == 1 else "aucune transition deduite")
        if t.kind == "trigger":
            used = used_events.get(t.trigger_id, [])
            qn = f"{pkg}::{names.event[t.state_id]}" if t.state_id in names.event else ""
            status, why = ("mapped", f"accept de {len(used)} transition(s)") if used else (
                "partial", f"evenement declare ; {no_transition} ; texte conserve dans le step")
        else:
            used = used_conditions.get(t.trigger_id, [])
            qn = used[0] if used else ""
            status, why = ("mapped", f"garde if de {len(used)} transition(s)") if used else (
                "partial", f"{no_transition} ; condition conservee en commentaire du step")
        trace.append(TraceEntry(usershaker_id=t.trigger_id, kind="step_trigger",
                                sysml_qualified_name=qn, status=status, reason=why))
    return views


# =========================================================================== commun

def build_common(ex: Export, cfg: GenerateSettings, names: CommonNames, selected: list[str]) -> tuple[dict, list[TraceEntry]]:
    pkg = cfg.common_package
    trace: list[TraceEntry] = []
    meta = ex.meta

    personas = []
    for p in by_pos(ex.personas, "persona_id"):
        nm = names.persona[p.persona_id]
        personas.append({
            "name": nm, "id": p.persona_id,
            "doc": join_doc(f"{p.name}.", p.biography, library_text(p)),
            "attrs": [(k, v) for k, v in (("ageCategory", p.age_category), ("gender", p.gender)) if v],
        })
        st, why = status_for(p)
        trace.append(TraceEntry(usershaker_id=p.persona_id, kind="persona", sysml_qualified_name=f"{pkg}::{nm}",
                                status=st, reason=join_doc(why, ignored(p, ("color",)), sep="; ")))

    state_views = build_states(ex, cfg, names, selected, trace)

    comps = by_pos(ex.components, "component_id")
    known = {c.component_id for c in comps}
    children: dict[str | None, list[m.Component]] = defaultdict(list)
    for c in comps:
        children[c.parent_component_id if c.parent_component_id in known else None].append(c)
    components = []
    for c in comps:
        nm = names.component_def[c.component_id]
        components.append({
            "name": nm, "id": c.component_id,
            "doc": join_doc(f"{c.name}.", c.description, library_text(c)),
            "attrs": [(k, v) for k, v in (("role", c.role), ("category", c.category)) if v],
            "parts": [(names.component_part[ch.component_id], names.component_def[ch.component_id])
                      for ch in children.get(c.component_id, [])],
            "state_def": state_views[c.component_id].name if c.component_id in state_views else None,
        })
        qn = f"{pkg}::{nm}"
        st, why = status_for(c)
        trace.append(TraceEntry(usershaker_id=c.component_id, kind="component", sysml_qualified_name=qn,
                                status=st, reason=why))
    system_parts = [(names.component_part[c.component_id], names.component_def[c.component_id])
                    for c in children.get(None, [])]

    events = []
    state_by_id = ex.by_id("component_states")
    comp_by_id = ex.by_id("components")
    for sid, ev in sorted(names.event.items(), key=lambda kv: kv[1]):
        s = state_by_id[sid]
        cname = comp_by_id[s.component_id].name if s.component_id in comp_by_id else s.component_id
        events.append({"name": ev, "id": sid, "doc": f"Evenement : '{cname}' passe dans l'etat '{s.name}'."})

    requirements = []
    for u in by_pos(ex.user_needs, "user_need_id"):
        nm = names.requirement[u.user_need_id]
        ref = f"Reference : {u.reference_url}." if u.reference_url else ""
        requirements.append({"name": nm, "id": u.user_need_id,
                             "doc": join_doc(u.name + ("." if not u.name.endswith(".") else ""),
                                             u.description, ref, library_text(u))})
        st, why = status_for(u)
        trace.append(TraceEntry(usershaker_id=u.user_need_id, kind="user_need", sysml_qualified_name=f"{pkg}::{nm}",
                                status=st, reason=why))

    if meta.project_id:
        trace.append(TraceEntry(usershaker_id=meta.project_id, kind="project", sysml_qualified_name=pkg))

    ctx = {
        "package": pkg,
        "system": cfg.system_name,
        "project": {
            "id": meta.project_id or "",
            "doc": join_doc(
                f"{meta.project_name or 'Projet'}" + (f" ({meta.company_name})" if meta.company_name else "") + ".",
                f"Export UserShaker du {meta.exported_at}, schema {meta.schema_version}." if meta.exported_at else "",
                "Fichier genere automatiquement : ne pas modifier a la main.",
            ),
        },
        "personas": personas,
        "components": components,
        "system_parts": system_parts,
        "state_defs": [state_views[c.component_id] for c in comps if c.component_id in state_views],
        "events": events,
        "requirements": requirements,
    }
    for e in trace:
        e.file = f"{pkg}.sysml"
    return ctx, trace


# =========================================================================== use case

def use_case_package(uc: m.UseCase) -> str:
    return f"UC{uc.position}_{pascal(uc.name)}"


def build_use_case(ex: Export, cfg: GenerateSettings, names: CommonNames, uc: m.UseCase) -> tuple[dict, list[TraceEntry]]:
    common = cfg.common_package
    pkg = use_case_package(uc)
    ucdef = names.use_case[uc.use_case_id]
    base_qn = f"{pkg}::{ucdef}"
    trace: list[TraceEntry] = []
    namer = Namer({cfg.subject_name, "start", "done"} | RESERVED)

    st, why = status_for(uc)
    trace.append(TraceEntry(usershaker_id=uc.use_case_id, kind="use_case", sysml_qualified_name=base_qn,
                            status=st, reason=why))

    persona_by_id = ex.by_id("personas")
    uc_personas = by_pos([r for r in ex.use_case_personas if r.use_case_id == uc.use_case_id], "position", "persona_id")
    actors = []
    persona_order: dict[str, int] = {}
    for r in uc_personas:
        if r.persona_id not in names.persona:
            continue
        nm = namer.take(names.persona[r.persona_id])
        lid = link_id(r.use_case_id, r.persona_id)
        actors.append({"name": nm, "persona": f"{common}::{names.persona[r.persona_id]}", "id": lid})
        persona_order[r.persona_id] = r.position
        trace.append(TraceEntry(usershaker_id=lid, kind="use_case_persona", sysml_qualified_name=f"{base_qn}::{nm}"))

    comp_by_id = ex.by_id("components")
    uc_comps = by_pos([r for r in ex.use_case_components if r.use_case_id == uc.use_case_id], "position", "component_id")
    comp_refs = []
    comp_order: dict[str, int] = {}
    for r in uc_comps:
        if r.component_id not in names.component_def:
            continue
        nm = namer.take(lower_first(names.component_def[r.component_id]))
        lid = link_id(r.use_case_id, r.component_id)
        comp_refs.append({"name": nm, "type": names.component_def[r.component_id], "id": lid})
        comp_order[r.component_id] = r.position
        trace.append(TraceEntry(usershaker_id=lid, kind="use_case_component", sysml_qualified_name=f"{base_qn}::{nm}"))

    groups = ex.by_id("step_groups")
    state_by_id = ex.by_id("component_states")
    triggers_by_step: dict[str, list[m.StepTrigger]] = defaultdict(list)
    for t in by_pos(ex.step_triggers, "step_id", "position", "trigger_id"):
        triggers_by_step[t.step_id].append(t)
    grid: dict[str, list[m.StepComponentState]] = defaultdict(list)
    for r in ex.step_component_states:
        grid[r.step_id].append(r)
    unused: dict[str, list[str]] = defaultdict(list)
    for r in ex.step_unused_components:
        unused[r.step_id].append(r.component_id)
    feedback: dict[str, list[m.PersonaStepFeedback]] = defaultdict(list)
    for f in ex.persona_step_feedback:
        feedback[f.step_id].append(f)

    def comp_label(cid: str) -> str:
        return comp_by_id[cid].name if cid in comp_by_id else cid

    def state_label(sid: str) -> str:
        return state_by_id[sid].name if sid in state_by_id else sid

    flow: list[dict] = []      # éléments de premier niveau : phases ou steps sans phase
    step_qn: dict[str, str] = {}
    group_trace: dict[str, TraceEntry] = {}
    group_actions: dict[str, list[str]] = defaultdict(list)   # phase → ses actions (plusieurs si non contiguë)
    for step in ex.steps_of(uc.use_case_id):
        gid = step.group_id if step.group_id in groups else None
        phase_name = groups[gid].name if gid else None
        if gid and (not flow or flow[-1].get("group_id") != gid):
            g = groups[gid]
            pn = namer.name(g.name, suffix="Phase")
            group_actions[gid].append(pn)
            if gid not in group_trace:
                gst, gwhy = status_for(g)
                group_trace[gid] = TraceEntry(usershaker_id=gid, kind="step_group",
                                              sysml_qualified_name=f"{base_qn}::{pn}", status=gst,
                                              reason=join_doc(gwhy, ignored(g, ("color",)), sep="; "))
                trace.append(group_trace[gid])
            flow.append({"kind": "phase", "name": pn, "id": gid, "group_id": gid,
                         "doc": f"Phase {g.position} : {g.name}.", "steps": []})
        sn = namer.name(step.name)
        qn = f"{base_qn}::{flow[-1]['name']}::{sn}" if gid else f"{base_qn}::{sn}"
        step_qn[step.step_id] = qn

        comments = []
        if step.trigger_text:
            comments.append(("triggerText", step.trigger_text))
        trig = triggers_by_step.get(step.step_id, [])
        if trig:
            parts = []
            for t in trig:
                word = "quand" if t.kind == "trigger" else "et si"
                parts.append(f"{word} '{comp_label(t.component_id)}' {t.operator} '{state_label(t.state_id)}'")
            comments.append(("structuredTrigger", " ; ".join(parts)))
        rows = sorted(grid.get(step.step_id, []),
                      key=lambda r: (comp_order.get(r.component_id, 10**6), r.component_id, r.sequence))
        if rows:
            per_comp: dict[str, list[str]] = {}
            for r in rows:
                per_comp.setdefault(r.component_id, []).append(state_label(r.state_id))
            comments.append(("blueprint", " ; ".join(f"{comp_label(c)} : {' -> '.join(s)}" for c, s in per_comp.items())))
        if unused.get(step.step_id):
            comments.append(("unusedComponents", ", ".join(comp_label(c) for c in sorted(unused[step.step_id]))))
            for c in sorted(unused[step.step_id]):
                trace.append(TraceEntry(usershaker_id=link_id(step.step_id, c), kind="step_unused_component",
                                        sysml_qualified_name=f"{qn}::unusedComponents"))
        if step.note:
            comments.append(("note", step.note))
        if step.image_url:
            comments.append(("imageUrl", step.image_url))

        fb_namer = Namer()
        fbs = []
        for f in sorted(feedback.get(step.step_id, []),
                        key=lambda f: (persona_order.get(f.persona_id, 10**6), f.persona_id, f.feedback_id)):
            pname = persona_by_id[f.persona_id].name if f.persona_id in persona_by_id else f.persona_id
            an = fb_namer.take("feedback" + pascal(pname))
            values = [("personaId", f'"{f.persona_id}"'), ("isPresent", "true" if f.is_present else "false")]
            if f.emotion_score is not None:
                values.append(("emotionScore", str(f.emotion_score)))
            presence = "" if f.is_present else " (absent de ce step, textes conserves)"
            fbs.append({"name": an, "id": f.feedback_id, "fields": values,
                        "doc": join_doc(f"{pname}{presence}.",
                                        f"Action : {f.action}" if f.action else "",
                                        f"Insight : {f.insight}" if f.insight else "", sep=" | ")})
            fst, fwhy = status_for(f)
            trace.append(TraceEntry(usershaker_id=f.feedback_id, kind="persona_step_feedback",
                                    sysml_qualified_name=f"{qn}::{an}", status=fst, reason=fwhy))

        item = {
            "kind": "step", "name": sn, "id": step.step_id,
            "doc": join_doc(f"[{phase_name}]" if phase_name else "", f"Step {step.position} : {step.name}.", step.description),
            "comments": comments, "feedback": fbs,
        }
        sst, swhy = status_for(step)
        trace.append(TraceEntry(usershaker_id=step.step_id, kind="step", sysml_qualified_name=qn, status=sst, reason=swhy))
        if gid:
            flow[-1]["steps"].append(item)
        else:
            flow.append(item)
    for gid, actions in group_actions.items():
        if len(actions) > 1:       # steps d'une même phase séparés par une autre : une action par morceau
            e = group_trace[gid]
            e.reason = join_doc(e.reason, f"groupe non contigu : scinde en {len(actions)} actions "
                                          f"({', '.join(actions)})", sep="; ")

    dependencies = []
    step_pos = {s.step_id: s.position for s in ex.steps_of(uc.use_case_id)}
    links = sorted((r for r in ex.step_user_needs if r.step_id in step_qn),
                   key=lambda r: (step_pos[r.step_id], r.user_need_id))
    for r in links:
        lid = link_id(r.step_id, r.user_need_id)
        if r.user_need_id not in names.requirement:
            trace.append(TraceEntry(usershaker_id=lid, kind="step_user_need", status="not_mapped",
                                    reason="user need introuvable"))
            continue
        src = step_qn[r.step_id][len(pkg) + 2:]   # relatif au package du use case
        dependencies.append({"id": lid, "source": src, "target": f"{common}::{names.requirement[r.user_need_id]}"})
        trace.append(TraceEntry(usershaker_id=lid, kind="step_user_need",
                                sysml_qualified_name=f"{pkg}::dependency({src} -> {names.requirement[r.user_need_id]})"))

    ctx = {
        "package": pkg, "common": common, "name": ucdef, "id": uc.use_case_id,
        "doc": join_doc(f"Use case {uc.position} : {uc.name} (statut : {uc.status}"
                        + (f", dossier : {uc.folder}" if uc.folder else "") + ").", uc.description),
        "story": uc.story or "",
        "subject": cfg.subject_name, "system": cfg.system_name,
        "actors": actors, "components": comp_refs, "flow": flow, "dependencies": dependencies,
    }
    for e in trace:
        e.file = f"{pkg}.sysml"
    return ctx, trace
