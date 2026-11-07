"""Outils que le LLM appelle pour répondre : il ne répond jamais de mémoire.

Chaque outil est une fonction Python pure sur l'export / le graphe / la traçabilité,
qui renvoie du JSON citant les ids UserShaker. Les schémas sont neutres (JSON Schema)
et adaptés à chaque fournisseur dans `providers.py`.

Data protection : les outils ne renvoient que les champs utiles à la question (pas de
biographie complète, pas d'URL d'image) ; `find_element` tronque les textes longs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from p9.checks import Finding, build_matrix, run_all
from p9.generate import Generated, TraceEntry, generate
from p9.ingest import Export
from p9.model import build_graph, find_element, search

MAX_TEXT = 400


def _short(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_TEXT:
        return v[:MAX_TEXT] + "…"
    return v


def _findings(items: list[Finding]) -> list[dict[str, Any]]:
    return [{"severity": f.severity, "message": f.message, "ids": f.ids} for f in items]


@dataclass
class ToolContext:
    """État partagé par les outils (calculé une fois par export et par état de la revue)."""

    export: Export
    # décisions humaines et propositions LLM acceptées : l'agent voit la traçabilité du modèle courant
    review_decisions: list[Any] = field(default_factory=list)
    accepted_proposals: list[Any] = field(default_factory=list)
    graph: nx.MultiDiGraph = field(init=False)
    generated: Generated = field(init=False)
    findings: dict[str, list[Finding]] = field(init=False)
    trace: dict[str, list[TraceEntry]] = field(init=False)

    def __post_init__(self) -> None:
        self.graph = build_graph(self.export)
        self.generated = generate(self.export, accepted_proposals=self.accepted_proposals,
                                  review_decisions=self.review_decisions)
        self.findings = run_all(self.export)
        matrix = build_matrix(self.export, self.generated, self.findings["constraints_in_text_only"],
                              self.review_decisions)
        self.trace = {}
        for e in matrix:
            self.trace.setdefault(e.usershaker_id, []).append(e)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


def _schema(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


def _list_use_cases(ctx: ToolContext) -> Any:
    ex = ctx.export
    return [{"use_case_id": u.use_case_id, "position": u.position, "name": u.name, "status": u.status,
             "steps": len(ex.steps_of(u.use_case_id)),
             "personas": sorted(r.persona_id for r in ex.use_case_personas if r.use_case_id == u.use_case_id)}
            for u in ex.use_cases_sorted()]


def _use_case_steps(ctx: ToolContext, use_case_id: str) -> Any:
    ex = ctx.export
    groups = ex.by_id("step_groups")
    needs = ex.by_id("user_needs")
    out = []
    for s in ex.steps_of(use_case_id):
        out.append({
            "step_id": s.step_id, "position": s.position, "name": s.name,
            "phase": groups[s.group_id].name if s.group_id in groups else None,
            "trigger_text": s.trigger_text,
            "user_needs": [{"user_need_id": r.user_need_id, "name": needs[r.user_need_id].name}
                           for r in ex.step_user_needs if r.step_id == s.step_id and r.user_need_id in needs],
            # ressenti des personas présents (pas l'action : le strict nécessaire)
            "feedback": [{"feedback_id": f.feedback_id, "persona_id": f.persona_id,
                          "emotion_score": f.emotion_score, "insight": _short(f.insight)}
                         for f in ex.persona_step_feedback if f.step_id == s.step_id and f.is_present],
        })
    return out or {"error": f"use case introuvable : {use_case_id}"}


def _component_timeline(ctx: ToolContext, component_id: str, use_case_id: str) -> Any:
    """États d'un composant step après step (grille du blueprint), avec l'événement de chaque step."""
    ex = ctx.export
    comps, states = ex.by_id("components"), ex.by_id("component_states")
    if component_id not in comps:
        return {"error": f"composant introuvable : {component_id} (chercher son id avec search_elements)"}
    ucs = [u for u in ex.use_cases_sorted() if not use_case_id or u.use_case_id == use_case_id]
    if not ucs:
        return {"error": f"use case introuvable : {use_case_id}"}
    grid: dict[str, list] = {}
    for r in sorted(ex.step_component_states, key=lambda r: r.sequence):
        if r.component_id == component_id:
            grid.setdefault(r.step_id, []).append(r.state_id)

    def label(t) -> str:
        cname = comps[t.component_id].name if t.component_id in comps else t.component_id
        return f"{cname} is {states[t.state_id].name if t.state_id in states else t.state_id}"

    c = comps[component_id]
    out: dict[str, Any] = {"component_id": component_id, "name": c.name, "role": c.role, "use_cases": []}
    for uc in ucs:
        rows, prev = [], None
        for s in ex.steps_of(uc.use_case_id):
            seq = grid.get(s.step_id)
            if not seq:
                continue
            trig = sorted((t for t in ex.step_triggers if t.step_id == s.step_id), key=lambda t: t.position)
            rows.append({
                "step_id": s.step_id, "position": s.position, "step": s.name,
                "states": [{"state_id": sid, "name": states[sid].name if sid in states else sid} for sid in seq],
                "changed": prev is not None and (seq[0] != prev or len(seq) > 1),
                "event": next((label(t) for t in trig if t.kind == "trigger"), None),
                "conditions": [label(t) for t in trig if t.kind == "condition"],
            })
            prev = seq[-1]
        if rows:
            out["use_cases"].append({"use_case_id": uc.use_case_id, "name": uc.name, "steps": rows})
    return out


def _find(ctx: ToolContext, element_id: str) -> Any:
    el = find_element(ctx.graph, element_id)
    if el is None:
        return {"error": f"id introuvable : {element_id}"}
    el["data"] = {k: _short(v) for k, v in el["data"].items() if k not in {"image_url", "biography"}}
    el["traceability"] = [e.model_dump(include={"kind", "sysml_qualified_name", "status", "reason"})
                          for e in ctx.trace.get(element_id, [])]
    return el


def _search(ctx: ToolContext, text: str, kind: str | None = None) -> Any:
    return search(ctx.graph, text, kind=kind or None)


MAX_TRACE_ROWS = 60


def _trace_status(ctx: ToolContext, status: str) -> Any:
    rows = [e for es in ctx.trace.values() for e in es if e.status == status]
    # total et drapeau de troncature : le LLM ne doit pas croire avoir tout vu
    return {"total": len(rows), "shown": min(len(rows), MAX_TRACE_ROWS), "truncated": len(rows) > MAX_TRACE_ROWS,
            "rows": [e.model_dump(include={"usershaker_id", "kind", "sysml_qualified_name", "reason"})
                     for e in rows[:MAX_TRACE_ROWS]]}


def _negative_feedback(ctx: ToolContext) -> Any:
    return _findings(ctx.findings["implicit_needs"])


TOOLS: list[Tool] = [
    Tool("list_use_cases", "Liste les use cases de l'export (id, position, nom, nombre de steps, personas).",
         _schema(), _list_use_cases),
    Tool("use_case_steps", "Steps d'un use case dans l'ordre, avec phase, trigger_text, user needs servis et "
         "ressenti des personas (emotion_score de -5 a +5, insight).",
         _schema({"use_case_id": {"type": "string", "description": "use_case_id UserShaker"}}, ["use_case_id"]),
         _use_case_steps),
    Tool("component_timeline", "Etat d'un composant a chaque step (grille du blueprint), avec l'evenement et les "
         "conditions du step et un drapeau changed. A utiliser pour toute question sur l'etat ou le comportement "
         "d'un composant (portes, volant, ecran...). Trouver d'abord son component_id avec search_elements.",
         _schema({"component_id": {"type": "string", "description": "component_id UserShaker"},
                  "use_case_id": {"type": "string", "description": "use_case_id pour un seul use case ; vide = tous"}},
                 ["component_id", "use_case_id"]),
         _component_timeline),
    Tool("conditions_vs_grid", "Triggers ou conditions d'un step contredits par la grille du blueprint (le step "
         "exige un etat que la grille ne montre pas).", _schema(),
         lambda ctx: _findings(ctx.findings["conditions_vs_grid"])),
    Tool("trigger_reaction_conflicts", "Meme evenement dans plusieurs steps mais reactions differentes d'un "
         "composant selon le use case.", _schema(),
         lambda ctx: _findings(ctx.findings["trigger_reaction_conflicts"])),
    Tool("uncovered_needs", "User needs servis par aucun step.", _schema(),
         lambda ctx: _findings(ctx.findings["uncovered_needs"])),
    Tool("unreachable_states", "Etats de composants jamais atteints dans les parcours.", _schema(),
         lambda ctx: _findings(ctx.findings["unreachable_states"])),
    Tool("constraints_in_text_only",
         "Contraintes presentes seulement dans le texte libre trigger_text, sans condition structuree.", _schema(),
         lambda ctx: _findings(ctx.findings["constraints_in_text_only"])),
    Tool("persona_inconsistencies", "Incoherences de personas entre use cases (roles, renvois, presence).",
         _schema(), lambda ctx: _findings(ctx.findings["persona_inconsistencies"])),
    Tool("implicit_needs", "Insights negatifs (emotion_score < 0) relies a aucun user need : besoins implicites.",
         _schema(), _negative_feedback),
    Tool("invalid_triggers", "Triggers incoherents (etat d'un autre composant, ordre trigger/condition).",
         _schema(), lambda ctx: _findings(ctx.findings["invalid_triggers"])),
    Tool("find_element", "Un element par son id UserShaker : donnees, voisins dans le graphe, tracabilite SysML.",
         _schema({"element_id": {"type": "string"}}, ["element_id"]), _find),
    Tool("search_elements", "Recherche plein texte dans les elements (noms, descriptions, insights). A utiliser "
         "pour trouver l'id d'un element cite par son nom ; les noms de l'export sont en anglais.",
         _schema({"text": {"type": "string"},
                  "kind": {"type": "string", "description": "filtre optionnel : step, persona, component, "
                           "component_state, user_need, persona_step_feedback, use_case ; vide = tous"}},
                 ["text", "kind"]), _search),
    Tool("traceability_status", "Lignes de la matrice de tracabilite ayant un statut donne (total, et au plus "
         f"{MAX_TRACE_ROWS} lignes : truncated indique s'il en reste).",
         _schema({"status": {"type": "string", "enum": ["mapped", "partial", "not_mapped"]}}, ["status"]),
         _trace_status),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(ctx: ToolContext, name: str, arguments: dict[str, Any] | None) -> tuple[str, bool]:
    """Exécute un outil ; renvoie (JSON, is_error). Les erreurs ne lèvent pas : le LLM les voit."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"outil inconnu : {name}"}), True
    args = arguments or {}
    allowed = set(tool.parameters["properties"])
    unknown = set(args) - allowed
    missing = [k for k in tool.parameters["required"] if k not in args]
    if unknown or missing:
        return json.dumps({"error": f"arguments invalides (inconnus {sorted(unknown)}, manquants {missing})"}), True
    try:
        result = tool.fn(ctx, **args)
    except Exception as e:  # un outil défaillant ne doit pas casser la conversation
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), True
    return json.dumps(result, ensure_ascii=False, default=str), False
