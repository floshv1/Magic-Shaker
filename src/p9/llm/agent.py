"""Agent de chat : le LLM interroge le modèle via les outils et cite les ids."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .providers import ChatResult
from .tools import ToolContext

SYSTEM_PROMPT = """\
Tu aides une equipe d'ingenierie systeme a analyser un modele SysML v2 genere automatiquement a partir \
d'un export UserShaker (parcours utilisateur d'un vehicule : use cases, steps, personas, composants, \
etats, user needs).

Regles :
- Chaque fait de ta reponse doit venir d'un appel d'outil fait dans cette conversation. Si les outils ne \
permettent pas de repondre, dis-le simplement plutot que de supposer.
- Cite l'id UserShaker de chaque element dont tu parles, entre backticks (ex. `c1a0f...`), pour que \
l'equipe puisse le retrouver dans le modele et la matrice de tracabilite.
- Tu proposes, tu ne modifies rien : presente toute deduction (garde manquante, besoin implicite, \
incoherence) comme une proposition a valider par un humain.
- Reponds toujours en francais, meme si les donnees et les resultats d'outils sont en anglais, de facon \
concise : quelques phrases ou une courte liste.

Methode :
- Ce qui manque, est incoherent ou mal traduit : appelle d'abord les outils de controle (uncovered_needs, \
constraints_in_text_only, conditions_vs_grid, trigger_reaction_conflicts, persona_inconsistencies, \
implicit_needs, traceability_status).
- Effet d'un evenement (« que se passe-t-il quand... »), ou comparaison entre use cases : \
trigger_reaction_conflicts d'abord, puis component_timeline des composants qui reagissent.
- Etat ou comportement d'un composant a un step : component_timeline (l'id du composant est dans l'index \
ci-dessous). Ne deduis jamais un etat d'une description : lis-le dans la grille.
- Un use case ou ses steps : use_case_steps ; un element precis : find_element ; un nom inconnu : \
search_elements.
"""


def context_index(ctx: ToolContext) -> str:
    """Index compact de l'export (noms et ids), ajouté au prompt système : le LLM appelle les outils
    directement au lieu de chercher les ids. Noms seulement (pas de texte libre ni de biographie)."""
    ex = ctx.export
    ucs = " ; ".join(f"UC{u.position} '{u.name}' `{u.use_case_id}`" for u in ex.use_cases_sorted())
    personas = " ; ".join(f"'{p.name}' `{p.persona_id}`" for p in sorted(ex.personas, key=lambda p: p.name))
    comps = " ; ".join(f"'{c.name}' ({c.role}) `{c.component_id}`"
                       for c in sorted(ex.components, key=lambda c: c.name))
    return f"Index de l'export :\n- Use cases : {ucs}\n- Personas : {personas}\n- Composants : {comps}\n"

ID_RE = re.compile(r"\b[a-z0-9]{20,30}\b")


@dataclass
class AgentAnswer:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cited_ids: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)   # ids cités mais absents de l'export
    provider: str = ""
    model: str = ""


def known_ids(ctx: ToolContext) -> set[str]:
    ids = set(ctx.graph.nodes)
    ids.update(ctx.trace)
    if ctx.export.meta.project_id:
        ids.add(ctx.export.meta.project_id)
    return ids


def check_citations(text: str, ctx: ToolContext) -> tuple[list[str], list[str]]:
    found = sorted(set(ID_RE.findall(text)))
    known = known_ids(ctx)
    return [i for i in found if i in known], [i for i in found if i not in known]


def ask(provider: Any, ctx: ToolContext, question: str, history: list[dict[str, Any]] | None = None,
        on_event: Callable[[str, str], None] | None = None) -> AgentAnswer:
    """Pose une question ; `history` = tours précédents [{role, content(str)}] (texte seulement).

    `on_event(kind, text)` : suivi de l'avancement (voir providers)."""
    system = f"{SYSTEM_PROMPT}\n{context_index(ctx)}"
    res: ChatResult = provider.chat_with_tools(system, question, history or [], ctx, on_event=on_event)
    if on_event is not None:
        on_event("step", "Verification des ids cites dans la reponse")
    cited, unknown = check_citations(res.text, ctx)
    return AgentAnswer(
        text=res.text,
        tool_calls=[{"name": c.name, "input": c.input, "is_error": c.is_error, "output_chars": len(c.output)}
                    for c in res.tool_calls],
        cited_ids=cited, unknown_ids=unknown, provider=res.provider, model=res.model,
    )
