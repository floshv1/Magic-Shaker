"""Diagrammes d'aperçu (Graphviz DOT) dans le style des vues CATIA Magic SysML v2.

Les diagrammes sont construits à partir des MÊMES contextes que les templates .sysml (build.py) :
les noms affichés sont les noms SysML générés, et le survol d'un élément montre son id UserShaker.
Rien n'est déduit ici ; c'est une vue du modèle généré, déterministe comme lui.

Vues :
  - structure      : `part def ESpace` et la composition des composants (bloc « BDD »)
  - use_cases      : sujet, use cases et acteurs (personas)
  - activities     : une vue par use case : phases (actions composites) et enchaînement des steps
  - states         : une vue par `state def` : états, état initial et transitions (accept / if)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from p9.config import GenerateSettings, load_settings
from p9.ingest import Export

from .build import build_common, build_use_case, compute_common_names, use_case_package

FONT = "Helvetica"
COLORS = {
    "part": ("#E8F0FA", "#4A6FA5"),
    "system": ("#F7F9FC", "#2E4A7A"),
    "use_case": ("#FFF7D6", "#B08900"),
    "actor": ("#FFFFFF", "#555555"),
    "action": ("#FFFFFF", "#3C6E47"),
    "phase": ("#F3F8F4", "#3C6E47"),
    "requirement": ("#FCEDEA", "#A8432F"),
    "state": ("#EAF5EA", "#2E7D32"),
}


@dataclass
class Diagram:
    title: str
    dot: str
    nodes: int
    edges: int
    transitions: int = 0      # machines à états seulement


@dataclass
class Diagrams:
    structure: Diagram
    use_cases: Diagram
    activities: dict[str, Diagram] = field(default_factory=dict)   # package du use case → vue
    states: dict[str, Diagram] = field(default_factory=dict)       # nom du state def → vue


# --------------------------------------------------------------------- DOT

def _tip(text: str | None, limit: int = 400) -> str:
    t = " ".join(str(text or "").split())
    t = t if len(t) <= limit else t[: limit - 1] + "…"
    return t.replace("\\", "\\\\").replace('"', '\\"')


def _label(keyword: str, name: str, extra: list[str] | None = None) -> str:
    rows = [f"<FONT POINT-SIZE='9'>«{escape(keyword)}»</FONT>", f"<B>{escape(name)}</B>"]
    rows += [f"<FONT POINT-SIZE='9'>{escape(e)}</FONT>" for e in extra or []]
    return "<" + "<BR/>".join(rows) + ">"


class _Dot:
    def __init__(self, name: str, rankdir: str = "TB", directed: bool = True) -> None:
        self.directed = directed
        self.lines = [
            f'{"digraph" if directed else "graph"} "{name}" {{',
            f'  graph [rankdir={rankdir}, bgcolor="white", fontname="{FONT}", fontsize=11, '
            'nodesep=0.4, ranksep=0.5, splines=ortho, forcelabels=true, pad=0.2];',
            f'  node [fontname="{FONT}", fontsize=11, shape=box, style="filled", margin="0.18,0.07", penwidth=1.2];',
            f'  edge [fontname="{FONT}", fontsize=9, color="#555555", fontcolor="#333333"];',
        ]
        self.nodes = 0
        self.edges = 0
        self.indent = "  "

    def node(self, nid: str, label: str, kind: str, tooltip: str = "", **attrs: str) -> None:
        fill, line = COLORS[kind]
        a = {"label": label, "fillcolor": f'"{fill}"', "color": f'"{line}"', "tooltip": f'"{_tip(tooltip)}"', **attrs}
        self.lines.append(f'{self.indent}"{nid}" [' + ", ".join(f"{k}={v}" for k, v in a.items()) + "];")
        self.nodes += 1

    def raw_node(self, nid: str, attrs: str) -> None:
        self.lines.append(f'{self.indent}"{nid}" [{attrs}];')

    def edge(self, a: str, b: str, **attrs: str) -> None:
        op = "->" if self.directed else "--"
        if "label" in attrs:               # traits orthogonaux : étiquettes externes (xlabel)
            attrs["xlabel"] = attrs.pop("label")
        body = ", ".join(f"{k}={v}" for k, v in attrs.items())
        self.lines.append(f'{self.indent}"{a}" {op} "{b}"' + (f" [{body}]" if body else "") + ";")
        self.edges += 1

    def open_cluster(self, cid: str, label: str, kind: str, tooltip: str = "") -> None:
        fill, line = COLORS[kind]
        self.lines.append(f'{self.indent}subgraph "cluster_{cid}" {{')
        self.indent += "  "
        self.lines.append(f'{self.indent}label={label}; labeljust=l; style="filled"; fillcolor="{fill}"; '
                          f'color="{line}"; tooltip="{_tip(tooltip)}";')

    def close_cluster(self) -> None:
        self.indent = self.indent[:-2]
        self.lines.append(f"{self.indent}}}")

    def done(self, title: str) -> Diagram:
        return Diagram(title=title, dot="\n".join(self.lines + ["}"]) + "\n", nodes=self.nodes, edges=self.edges)


def _short(qn: str | None) -> str:
    return (qn or "").rsplit("::", 1)[-1]


def _ref(doc: str, uid: str) -> str:
    return f"{doc} [UserShakerRef id = {uid}]" if uid else doc


# --------------------------------------------------------------------- vues

GRID_COLS = 4


def _structure(common: dict) -> Diagram:
    """Vue de type « blocs imbriqués » (comme une vue générale CATIA) : le bloc `part def ESpace` contient ses
    parts, rangées en grille ; une part qui a elle-même des parts devient un bloc imbriqué."""
    d = _Dot("structure", rankdir="TB")
    system = common["system"]
    comps = {c["name"]: c for c in common["components"]}

    def place(parts: list[tuple[str, str]], path: str, seen: frozenset[str]) -> list[str]:
        anchors: list[str] = []          # un nœud par case de la grille (1er nœud d'un bloc imbriqué)
        for part, typ in parts:
            c = comps.get(typ)
            extra = [f": {typ}"]
            if c:
                extra += [f"{k} = {v}" for k, v in c["attrs"]]
                if c["state_def"]:
                    extra.append(f"exhibit state : {c['state_def']}")
            nid = f"p{path}_{part}"
            tip = _ref(c["doc"], c["id"]) if c else typ
            if c and c["parts"] and typ not in seen:            # bloc imbriqué (garde-fou contre les cycles)
                d.open_cluster(nid, _label("part", part, extra), "part", tip)
                inner = place(c["parts"], f"{path}_{part}", seen | {typ})
                d.close_cluster()
                if inner:
                    anchors.append(inner[0])
            else:
                d.node(nid, _label("part", part, extra), "part", tip, width="3.4")
                anchors.append(nid)
        # grille : GRID_COLS blocs par ligne, lignes empilées (liens invisibles)
        rows = [anchors[i:i + GRID_COLS] for i in range(0, len(anchors), GRID_COLS)]
        for row in rows:
            if len(row) > 1:
                d.lines.append(f"{d.indent}{{ rank=same; " + " ".join(f'"{a}"' for a in row) + " }")
                for a, b in zip(row, row[1:]):
                    d.lines.append(f'{d.indent}"{a}" -> "{b}" [style=invis];')
        for upper, lower in zip(rows, rows[1:]):
            for a, b in zip(upper, lower):
                d.lines.append(f'{d.indent}"{a}" -> "{b}" [style=invis];')
        return anchors

    d.open_cluster("system", _label("part def", system, [f"{len(common['system_parts'])} parts"]), "system",
                   f"Systeme {system} (sujet des use cases) et ses parts")
    place(common["system_parts"], "", frozenset({system}))
    d.close_cluster()
    return d.done(f"Structure : part def {system}")


def _use_cases(common: dict, ucs: list[dict], subject: str) -> Diagram:
    d = _Dot("use_cases", rankdir="LR", directed=False)
    personas = {p["name"]: p for p in common["personas"]}
    used: list[str] = []
    for u in ucs:
        for a in u["actors"]:
            p = _short(a["persona"])
            if p not in used:
                used.append(p)
    for p in used:
        info = personas.get(p, {"doc": p, "id": ""})
        d.node(f"act_{p}", _label("actor", p, [": Persona"]), "actor", _ref(info["doc"], info["id"]),
               shape="box", style='"filled"')
    d.open_cluster("subject", _label("part def", common["system"], [f"subject {subject}"]), "part",
                   f"Sujet des use cases : {subject} : {common['system']}")
    for u in ucs:
        d.node(f"uc_{u['package']}", _label("use case def", u["name"], [u["package"]]), "use_case",
               _ref(u["doc"], u["id"]), shape="ellipse")
    d.close_cluster()
    for u in ucs:
        for a in u["actors"]:
            d.edge(f"act_{_short(a['persona'])}", f"uc_{u['package']}", tooltip=f'"actor {a["name"]}"')
    return d.done("Use cases et acteurs")


def _activity(u: dict, with_requirements: bool) -> Diagram:
    d = _Dot(u["package"], rankdir="TB")
    d.raw_node("start", 'shape=circle, style=filled, fillcolor="black", label="", width=0.22, tooltip="start"')
    d.raw_node("done", 'shape=doublecircle, style=filled, fillcolor="black", label="", width=0.16, tooltip="done"')
    order: list[str] = []
    for item in u["flow"]:
        steps = item["steps"] if item["kind"] == "phase" else [item]
        if item["kind"] == "phase":
            d.open_cluster(f"{item['name']}_{len(order)}", _label("action", item["name"]), "phase",
                           _ref(item["doc"], item["id"]))
        for s in steps:
            nid = f"a_{s['name']}"
            d.node(nid, _label("action", s["name"]), "action", _ref(s["doc"], s["id"]),
                   shape="box", style='"filled"')
            order.append(nid)
        if item["kind"] == "phase":
            d.close_cluster()
    chain = ["start", *order, "done"]
    for a, b in zip(chain, chain[1:]):
        d.edge(a, b, tooltip='"then"')
    if with_requirements:
        reqs: list[str] = []
        for dep in u["dependencies"]:
            r = _short(dep["target"])
            if r not in reqs:
                reqs.append(r)
        if reqs:
            # colonne à droite : requirements empilés par des liens invisibles
            d.open_cluster("requirements", _label("package", "requirements servis"), "requirement",
                           "Requirements (user needs) servis par les steps de ce use case")
            for r in reqs:
                d.node(f"req_{r}", _label("requirement def", r), "requirement", r, shape="box")
            for a, b in zip(reqs, reqs[1:]):
                d.lines.append(f'{d.indent}"req_{a}" -> "req_{b}" [style=invis];')
            d.close_cluster()
        for dep in u["dependencies"]:
            d.edge(f"a_{_short(dep['source'])}", f"req_{_short(dep['target'])}", style="dashed",
                   arrowhead="vee", color='"#C98476"', constraint="false",
                   tooltip=f'"«dependency» {_tip(dep["id"])}"')
    return d.done(f"{u['package']} : use case def {u['name']}")


def _state_machine(sd, ids: dict[str, str]) -> Diagram:
    d = _Dot(sd.name, rankdir="LR")
    d.raw_node("init", 'shape=circle, style=filled, fillcolor="black", label="", width=0.2, tooltip="entry"')
    for s in sd.states:
        d.node(f"s_{s['name']}", _label("state", s["name"]), "state", _ref(s["doc"], s["id"]),
               shape="box", style='"filled"')
    d.edge("init", f"s_{sd.initial}", tooltip='"entry"')
    for t in sd.transitions:
        # étiquette = petit bloc posé sur le trait : reste lisible avec des traits orthogonaux
        rows = [f"<B>{escape(t.name)}</B>"]
        if t.accept:
            rows.append(f"accept {escape(_short(t.accept))}")
        if t.guards:
            rows.append(f"[{escape(' and '.join(t.guards))}]")
        tip = _tip(_ref(t.doc, t.ref_id))
        d.raw_node(f"t_{t.name}", 'shape=box, style="filled", fillcolor="white", color="#BBBBBB", fontsize=9, '
                                  f'margin="0.06,0.03", label=<{"<BR/>".join(rows)}>, tooltip="{tip}"')
        d.edge(f"s_{t.source}", f"t_{t.name}", arrowhead="none", tooltip=f'"{tip}"')
        d.edge(f"t_{t.name}", f"s_{t.target}", tooltip=f'"{tip}"')
    out = d.done(f"state def {sd.name} ({ids.get(sd.component_id, sd.component_id)})")
    out.transitions = len(sd.transitions)
    return out


def build_diagrams(ex: Export, selected: list[str] | None = None, cfg: GenerateSettings | None = None,
                   with_requirements: bool = True) -> Diagrams:
    """Vues du modèle généré pour les use cases sélectionnés (tous si None), même ordre que generate()."""
    cfg = cfg or load_settings().generate
    ucs = ex.use_cases_sorted()
    wanted = set(selected) if selected is not None else {u.use_case_id for u in ucs}
    ucs = [u for u in ucs if u.use_case_id in wanted]
    names = compute_common_names(ex, cfg)
    common, _ = build_common(ex, cfg, names, [u.use_case_id for u in ucs])
    uc_ctx = [build_use_case(ex, cfg, names, u)[0] for u in ucs]
    comp_names = {c.component_id: c.name for c in ex.components}

    out = Diagrams(structure=_structure(common), use_cases=_use_cases(common, uc_ctx, cfg.subject_name))
    for u, ctx in zip(ucs, uc_ctx):
        out.activities[use_case_package(u)] = _activity(ctx, with_requirements)
    for sd in common["state_defs"]:
        out.states[sd.name] = _state_machine(sd, comp_names)
    return out


__all__ = ["Diagram", "Diagrams", "build_diagrams"]
