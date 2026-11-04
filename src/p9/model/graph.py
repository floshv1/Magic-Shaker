"""Graphe pivot (networkx) : un nœud par id UserShaker, une arête par relation de l'export.

Sert aux requêtes transverses (agent de chat, recherche d'un élément et de ses voisins).
Les nœuds portent les données d'origine ; rien n'est déduit ici.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from p9.ingest import Export

NODE_SHEETS = {
    "use_cases": ("use_case", "use_case_id"),
    "step_groups": ("step_group", "group_id"),
    "steps": ("step", "step_id"),
    "step_triggers": ("step_trigger", "trigger_id"),
    "personas": ("persona", "persona_id"),
    "persona_step_feedback": ("persona_step_feedback", "feedback_id"),
    "components": ("component", "component_id"),
    "component_states": ("component_state", "state_id"),
    "user_needs": ("user_need", "user_need_id"),
}


def build_graph(ex: Export) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for sheet, (kind, key) in NODE_SHEETS.items():
        for row in ex.rows(sheet):
            data = row.model_dump()
            g.add_node(data[key], kind=kind, name=data.get("name") or data.get("insight") or data.get("kind") or "",
                       data=data)

    def edge(a: str | None, b: str | None, rel: str, **attrs: Any) -> None:
        if a and b and a in g and b in g:
            g.add_edge(a, b, key=rel, rel=rel, **attrs)

    for s in ex.step_groups:
        edge(s.use_case_id, s.group_id, "has_phase")
    for s in ex.steps:
        edge(s.use_case_id, s.step_id, "has_step", position=s.position)
        edge(s.step_id, s.group_id, "in_phase")
    for uc in ex.use_cases:
        steps = ex.steps_of(uc.use_case_id)
        for a, b in zip(steps, steps[1:]):
            edge(a.step_id, b.step_id, "then")
    for t in ex.step_triggers:
        edge(t.step_id, t.trigger_id, "has_trigger")
        edge(t.trigger_id, t.state_id, t.kind)          # trigger / condition
        edge(t.trigger_id, t.component_id, "observes")
    for r in ex.use_case_personas:
        edge(r.persona_id, r.use_case_id, "takes_part_in", position=r.position)
    for f in ex.persona_step_feedback:
        edge(f.persona_id, f.feedback_id, "gives_feedback")
        edge(f.feedback_id, f.step_id, "about_step")
    for c in ex.components:
        edge(c.parent_component_id, c.component_id, "contains")
    for s in ex.component_states:
        edge(s.component_id, s.state_id, "has_state", position=s.position)
    for r in ex.use_case_components:
        edge(r.use_case_id, r.component_id, "uses_component", position=r.position)
    for r in ex.step_component_states:
        g.add_edge(r.step_id, r.state_id, key=f"expects:{r.component_id}:{r.sequence}", rel="expects_state",
                   sequence=r.sequence)
    for r in ex.step_unused_components:
        edge(r.step_id, r.component_id, "unused_component")
    for r in ex.step_user_needs:
        edge(r.step_id, r.user_need_id, "serves")
    return g


def find_element(g: nx.MultiDiGraph, element_id: str, limit: int = 40) -> dict[str, Any] | None:
    """Un élément et ses voisins directs (relations sortantes et entrantes)."""
    if element_id not in g:
        return None
    node = g.nodes[element_id]

    def describe(n: str) -> dict[str, str]:
        d = g.nodes[n]
        return {"id": n, "kind": d.get("kind", ""), "name": str(d.get("name", ""))}

    out = [{"rel": a.get("rel", k), **describe(v)} for _, v, k, a in g.out_edges(element_id, keys=True, data=True)]
    inc = [{"rel": a.get("rel", k), **describe(u)} for u, _, k, a in g.in_edges(element_id, keys=True, data=True)]
    return {"id": element_id, "kind": node.get("kind"), "data": node.get("data", {}),
            "outgoing": out[:limit], "incoming": inc[:limit],
            "truncated": len(out) > limit or len(inc) > limit}


def search(g: nx.MultiDiGraph, text: str, kind: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    """Recherche plein texte simple sur les noms et champs texte des nœuds."""
    q = text.lower().strip()
    hits = []
    for n, d in g.nodes(data=True):
        if kind and d.get("kind") != kind:
            continue
        blob = " ".join(str(v) for v in d.get("data", {}).values() if isinstance(v, str)).lower()
        if q and q in blob:
            hits.append({"id": n, "kind": d.get("kind", ""), "name": str(d.get("name", ""))})
    return sorted(hits, key=lambda h: (h["kind"], h["name"], h["id"]))[:limit]
