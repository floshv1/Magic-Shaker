"""Description du schéma 1.0 : feuilles, modèle de ligne, clé, clés étrangères, ordres."""

from __future__ import annotations

from dataclasses import dataclass

from . import models as m

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class SheetSpec:
    name: str
    model: type[m.Row]
    key: tuple[str, ...]          # clé primaire (ou composite pour les feuilles de liens)
    attr: str                     # nom de l'attribut dans Export


SHEETS: dict[str, SheetSpec] = {
    s.name: s
    for s in [
        SheetSpec("use_cases", m.UseCase, ("use_case_id",), "use_cases"),
        SheetSpec("step_groups", m.StepGroup, ("group_id",), "step_groups"),
        SheetSpec("steps", m.Step, ("step_id",), "steps"),
        SheetSpec("step_triggers", m.StepTrigger, ("trigger_id",), "step_triggers"),
        SheetSpec("personas", m.Persona, ("persona_id",), "personas"),
        SheetSpec("use_case_personas", m.UseCasePersona, ("use_case_id", "persona_id"), "use_case_personas"),
        SheetSpec("persona_step_feedback", m.PersonaStepFeedback, ("feedback_id",), "persona_step_feedback"),
        SheetSpec("components", m.Component, ("component_id",), "components"),
        SheetSpec("component_states", m.ComponentState, ("state_id",), "component_states"),
        SheetSpec("use_case_components", m.UseCaseComponent, ("use_case_id", "component_id"), "use_case_components"),
        SheetSpec(
            "step_component_states", m.StepComponentState,
            ("step_id", "component_id", "sequence"), "step_component_states",
        ),
        SheetSpec("step_unused_components", m.StepUnusedComponent, ("step_id", "component_id"), "step_unused_components"),
        SheetSpec("user_needs", m.UserNeed, ("user_need_id",), "user_needs"),
        SheetSpec("step_user_needs", m.StepUserNeed, ("step_id", "user_need_id"), "step_user_needs"),
    ]
}

META_SHEET = "meta"
ALL_SHEETS: list[str] = [META_SHEET, *SHEETS]
REQUIRED_SHEETS: list[str] = ["use_cases", "steps", "personas", "components", "user_needs"]

# (feuille, colonne, feuille cible, colonne cible). Une valeur vide n'est pas vérifiée.
FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("step_groups", "use_case_id", "use_cases", "use_case_id"),
    ("steps", "use_case_id", "use_cases", "use_case_id"),
    ("steps", "group_id", "step_groups", "group_id"),
    ("step_triggers", "step_id", "steps", "step_id"),
    ("step_triggers", "component_id", "components", "component_id"),
    ("step_triggers", "state_id", "component_states", "state_id"),
    ("use_case_personas", "use_case_id", "use_cases", "use_case_id"),
    ("use_case_personas", "persona_id", "personas", "persona_id"),
    ("persona_step_feedback", "step_id", "steps", "step_id"),
    ("persona_step_feedback", "persona_id", "personas", "persona_id"),
    ("components", "parent_component_id", "components", "component_id"),
    ("component_states", "component_id", "components", "component_id"),
    ("use_case_components", "use_case_id", "use_cases", "use_case_id"),
    ("use_case_components", "component_id", "components", "component_id"),
    ("step_component_states", "step_id", "steps", "step_id"),
    ("step_component_states", "component_id", "components", "component_id"),
    ("step_component_states", "state_id", "component_states", "state_id"),
    ("step_unused_components", "step_id", "steps", "step_id"),
    ("step_unused_components", "component_id", "components", "component_id"),
    ("step_user_needs", "step_id", "steps", "step_id"),
    ("step_user_needs", "user_need_id", "user_needs", "user_need_id"),
]

# (feuille, colonne d'ordre, colonnes du parent) : 1..n sans trou dans chaque parent.
ORDERINGS: list[tuple[str, str, tuple[str, ...]]] = [
    ("use_cases", "position", ()),
    ("step_groups", "position", ("use_case_id",)),
    ("steps", "position", ("use_case_id",)),
    ("step_triggers", "position", ("step_id",)),
    ("use_case_personas", "position", ("use_case_id",)),
    ("component_states", "position", ("component_id",)),
    ("use_case_components", "position", ("use_case_id",)),
    ("step_component_states", "sequence", ("step_id", "component_id")),
]


def expected_columns(sheet: str) -> list[str]:
    return list(SHEETS[sheet].model.model_fields)


def required_columns(sheet: str) -> list[str]:
    return [n for n, f in SHEETS[sheet].model.model_fields.items() if f.is_required()]
