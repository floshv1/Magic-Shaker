"""p9.llm : enrichissement (propositions à valider) et agent de chat en tool calling.

Le LLM ne génère jamais de SysML : il propose, l'humain valide, et seules les propositions
acceptées sont rendues (de façon déterministe) dans un package séparé, marquées origin = "llm".
"""

from .agent import SYSTEM_PROMPT, AgentAnswer, ask, check_citations
from .enrich import (
    EnrichResult,
    Proposal,
    ProposalStore,
    build_payload,
    enrich,
    load_store,
    save_store,
    validate_proposals,
)
from .providers import LlmUnavailable, make_provider, provider_status
from .tools import TOOLS, ToolContext, run_tool

__all__ = [
    "SYSTEM_PROMPT", "TOOLS", "AgentAnswer", "EnrichResult", "LlmUnavailable", "Proposal", "ProposalStore",
    "ToolContext", "ask", "build_payload", "check_citations", "enrich", "load_store", "make_provider",
    "provider_status", "run_tool", "save_store", "validate_proposals",
]
