"""Vues de l'export pour l'interface (tableaux prêts à afficher, aucune logique dans l'UI)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .loader import Export


@dataclass
class PersonaPill:
    persona_id: str
    name: str
    age_category: str
    color: str


@dataclass
class UseCaseSummary:
    use_case_id: str
    position: int
    name: str
    status: str
    description: str
    story: str
    phases: list[str]
    personas: list[PersonaPill]
    needs: list[tuple[str, str]]                 # (user_need_id, nom)
    steps: pd.DataFrame                          # #, phase, nom, déclencheur
    emotions: pd.DataFrame = field(default_factory=pd.DataFrame)   # index = step, colonnes = personas

    @property
    def title(self) -> str:
        return f"{self.position}. {self.name}"


def use_case_summaries(ex: Export) -> list[UseCaseSummary]:
    groups = ex.by_id("step_groups")
    personas = ex.by_id("personas")
    needs = ex.by_id("user_needs")
    out = []
    for uc in ex.use_cases_sorted():
        steps = ex.steps_of(uc.use_case_id)
        step_ids = {s.step_id for s in steps}
        ucg = sorted((g for g in ex.step_groups if g.use_case_id == uc.use_case_id), key=lambda g: (g.position, g.group_id))
        ucp = sorted((r for r in ex.use_case_personas if r.use_case_id == uc.use_case_id),
                     key=lambda r: (r.position, r.persona_id))
        pills = [PersonaPill(r.persona_id, personas[r.persona_id].name, personas[r.persona_id].age_category or "",
                             personas[r.persona_id].color or "#888888") for r in ucp if r.persona_id in personas]
        need_ids = sorted({r.user_need_id for r in ex.step_user_needs if r.step_id in step_ids and r.user_need_id in needs},
                          key=lambda i: needs[i].name)
        table = pd.DataFrame([{
            "#": s.position,
            "phase": groups[s.group_id].name if s.group_id in groups else "",
            "nom": s.name,
            "déclencheur": s.trigger_text or "",
        } for s in steps])
        label = {s.step_id: f"{s.position}. {s.name}" for s in steps}
        rows = [{"step": label[f.step_id], "persona": personas[f.persona_id].name if f.persona_id in personas else f.persona_id,
                 "emotion": f.emotion_score}
                for f in ex.persona_step_feedback
                if f.step_id in step_ids and f.is_present and f.emotion_score is not None]
        emotions = pd.DataFrame()
        if rows:
            emotions = (pd.DataFrame(rows).pivot_table(index="step", columns="persona", values="emotion", aggfunc="mean")
                        .reindex([label[s.step_id] for s in steps]).dropna(how="all"))
        out.append(UseCaseSummary(
            use_case_id=uc.use_case_id, position=uc.position, name=uc.name, status=uc.status,
            description=uc.description or "", story=uc.story or "", phases=[g.name for g in ucg], personas=pills,
            needs=[(i, needs[i].name) for i in need_ids], steps=table, emotions=emotions,
        ))
    return out
