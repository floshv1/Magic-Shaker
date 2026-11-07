"""Rendu des propositions LLM ACCEPTÉES dans un package séparé (déterministe, sans LLM).

Une proposition est un objet avec : proposal_id, kind (guard|requirement|rephrase), title, text,
rationale, target_ids, origin. Le générateur ne dépend pas de p9.llm (duck typing).
"""

from __future__ import annotations

from typing import Any

from p9.config import GenerateSettings

from .naming import Namer, pascal
from .trace import TraceEntry


def build_proposals(cfg: GenerateSettings, accepted: list[Any], trace: list[TraceEntry],
                    requirement_names: dict[str, str]) -> tuple[str, dict, list[TraceEntry]]:
    package = f"{cfg.project_package}LlmProposals"
    qn_of = {}
    for e in trace:
        if e.sysml_qualified_name and e.kind in {"step", "persona_step_feedback", "use_case", "user_need"}:
            qn_of.setdefault(e.usershaker_id, e.sysml_qualified_name)
    namer = Namer({"LlmOrigin"} | set(requirement_names.values()))
    items, out_trace = [], []
    for p in sorted(accepted, key=lambda p: (p.kind, p.proposal_id)):
        name = namer.name(p.title, upper=True)
        refines = None
        if p.kind == "rephrase":
            need = next((t for t in p.target_ids if t in requirement_names), None)
            refines = f"{cfg.common_package}::{requirement_names[need]}" if need else None
        deps, missing = [], []
        for t in p.target_ids:
            qn = qn_of.get(t)
            if qn and not (refines and t in requirement_names):
                deps.append(qn)
            elif not qn:
                missing.append(t)
        items.append({
            "id": p.proposal_id, "kind": p.kind, "name": name, "origin": p.origin, "refines": refines,
            "based_on": " ".join(p.target_ids), "dependencies": sorted(set(deps)),
            "doc": f"{p.text} Justification : {p.rationale}",
        })
        out_trace.append(TraceEntry(
            usershaker_id=p.proposal_id, kind="llm_proposal", sysml_qualified_name=f"{package}::{name}",
            status="mapped" if not missing else "partial",
            reason=("origin=llm, accepte" + (f" ; cibles hors des use cases generes : {missing}" if missing else "")),
            file=f"{package}.sysml",
        ))
    imports = sorted({d.split("::", 1)[0] for it in items for d in it["dependencies"]} - {cfg.common_package})
    ctx = {"package": package, "common": cfg.common_package, "imports": imports, "items": items}
    return package, ctx, out_trace
