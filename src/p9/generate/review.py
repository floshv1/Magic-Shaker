"""Rendu des décisions HUMAINES sur les points à valider, dans un package séparé (déterministe).

Une décision est un objet avec : finding_id, check, action, author, decided_at, title, text, reason,
component_id, state_id, need_id, target_ids, llm_proposal_id, origin. Le générateur ne dépend pas de
p9.review (duck typing), comme pour les propositions LLM.

Ce qui devient un élément de modèle :
  - action `guard`       → `constraint def` (+ dependency du step vers la garde, et de la garde vers
                           l'état attendu si la condition est structurée) ;
  - action `requirement` → `requirement def` (+ dependency de l'insight vers le requirement) ;
  - action `link_need`   → dependency de l'insight vers le requirement existant du package commun.
`fix_at_source` et `dismiss` n'ajoutent rien au modèle : ils figurent dans la traçabilité et le journal.
"""

from __future__ import annotations

from typing import Any

from p9.config import GenerateSettings

from .build import CommonNames
from .naming import Namer
from .trace import TraceEntry

MODEL_ACTIONS = {"guard", "requirement", "link_need"}


def review_package(cfg: GenerateSettings) -> str:
    return f"{cfg.project_package}Review"


def build_review(cfg: GenerateSettings, decisions: list[Any], trace: list[TraceEntry], names: CommonNames,
                 component_names: dict[str, str], state_names: dict[str, str],
                 feedback_steps: dict[str, str] | None = None,
                 steps_known: set[str] | frozenset[str] = frozenset()) -> tuple[str, dict, list[TraceEntry]]:
    """`feedback_steps` : feedback_id → step_id ; `steps_known` : tous les step_id de l'export (pour savoir
    si un élément visé manque parce que son use case n'est pas sélectionné)."""
    package = review_package(cfg)
    common = cfg.common_package
    qn_of: dict[str, dict[str, str]] = {"step": {}, "persona_step_feedback": {}}
    for e in trace:
        if e.sysml_qualified_name and e.kind in qn_of:
            qn_of[e.kind].setdefault(e.usershaker_id, e.sysml_qualified_name)
    step_of_feedback = dict(feedback_steps or {})
    namer = Namer({"ReviewDecision"} | set(names.requirement.values()))
    items, out_trace = [], []
    for d in sorted(decisions, key=lambda d: (d.check, d.finding_id)):
        if d.action not in MODEL_ACTIONS:
            continue
        meta = {"action": d.action, "author": d.author, "decided_at": d.decided_at, "finding_id": d.finding_id,
                "based_on": " ".join(d.target_ids), "llm_proposal_id": d.llm_proposal_id}
        # élément d'où part la dépendance : le step pour une garde, l'insight (feedback) sinon
        if d.action == "guard":
            sources = [qn_of["step"][t] for t in d.target_ids if t in qn_of["step"]]
            expected = [t for t in d.target_ids if t in steps_known]
        else:
            sources = [qn_of["persona_step_feedback"][t] for t in d.target_ids if t in qn_of["persona_step_feedback"]]
            expected = [t for t in d.target_ids if t in step_of_feedback]
        missing = [t for t in expected if t not in qn_of["step"] and t not in qn_of["persona_step_feedback"]]
        item: dict[str, Any] = {"meta": meta, "dependencies": [], "kind": d.action}
        if d.action == "guard":
            item["name"] = namer.name(d.title, upper=True)
            if d.state_id:
                cname = component_names.get(d.component_id, d.component_id)
                sname = state_names.get(d.state_id, d.state_id)
                cond = f"Vrai quand '{cname}' est dans l'etat '{sname}'."
                sd, st = names.state_def.get(d.component_id), names.state.get(d.state_id)
                if sd and st:
                    item["dependencies"].append((item["name"], f"{common}::{sd}::{st}"))
            else:
                cond = f"Condition : {d.text.strip().rstrip('.')}."
            item["doc"] = f"{cond} Garde ajoutee par {d.author} le {d.decided_at}." + (
                f" Motif : {d.reason.strip()}" if d.reason.strip() else "")
            # le step concerné dépend de la garde (elle conditionne son déclenchement)
            item["dependencies"] += [(src, item["name"]) for src in sources]
        elif d.action == "requirement":
            item["name"] = namer.name(d.title, upper=True)
            item["doc"] = f"{d.text.strip().rstrip('.')}. Requirement cree par {d.author} le {d.decided_at}." + (
                f" Motif : {d.reason.strip()}" if d.reason.strip() else "")
            item["dependencies"] += [(src, item["name"]) for src in sources]
        else:   # link_need : uniquement des dépendances vers le requirement existant
            req = names.requirement.get(d.need_id)
            if req:
                item["dependencies"] += [(src, f"{common}::{req}") for src in sources]
            item["name"] = None
        item["dependencies"] = sorted(set(item["dependencies"]))
        items.append(item)
        rendered = bool(item["name"]) or bool(item["dependencies"])
        qn = f"{package}::{item['name']}" if item["name"] else (
            f"{package}::dependency({item['dependencies'][0][0].rsplit('::', 1)[-1]} -> "
            f"{item['dependencies'][0][1].rsplit('::', 1)[-1]})" if item["dependencies"] else "")
        if not sources:
            missing = missing or list(expected)
        status = "mapped" if rendered and not missing else ("partial" if rendered else "not_mapped")
        reason = f"origin=human, {d.action} par {d.author} le {d.decided_at}"
        if missing:
            reason += f" ; elements hors des use cases generes : {missing}"
        out_trace.append(TraceEntry(usershaker_id=d.finding_id, kind="review_decision", sysml_qualified_name=qn,
                                    status=status, reason=reason, file=f"{package}.sysml"))
    imports = sorted({qn.split("::", 1)[0] for it in items for pair in it["dependencies"] for qn in pair
                      if "::" in qn} - {common, package})
    ctx = {"package": package, "common": common, "imports": imports, "items": items}
    return package, ctx, out_trace
