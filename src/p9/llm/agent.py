"""Agent de chat : le LLM interroge le modèle via les outils et cite les ids."""

from __future__ import annotations

import re
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
- Reponds en francais, de facon concise : quelques phrases ou une courte liste.
"""

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


def ask(provider: Any, ctx: ToolContext, question: str, history: list[dict[str, Any]] | None = None) -> AgentAnswer:
    """Pose une question ; `history` = tours précédents [{role, content(str)}] (texte seulement)."""
    res: ChatResult = provider.chat_with_tools(SYSTEM_PROMPT, question, history or [], ctx)
    cited, unknown = check_citations(res.text, ctx)
    return AgentAnswer(
        text=res.text,
        tool_calls=[{"name": c.name, "input": c.input, "is_error": c.is_error, "output_chars": len(c.output)}
                    for c in res.tool_calls],
        cited_ids=cited, unknown_ids=unknown, provider=res.provider, model=res.model,
    )
