"""Modèles pydantic d'une ligne de chaque feuille de l'export UserShaker (schéma 1.0).

Une cellule vide veut dire « non renseigné » : elle devient `None` pour les champs
optionnels, et une erreur de validation pour les champs « always filled ».
Les colonnes inconnues sont conservées (extra="allow") et signalées dans le rapport.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Id = str
Position = int


class Row(BaseModel):
    """Base de toutes les lignes : '' → None, colonnes en plus conservées."""

    model_config = ConfigDict(extra="allow", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if v == "" else v


class UseCase(Row):
    use_case_id: Id
    position: Position = Field(ge=1)
    name: str
    description: str | None = None
    story: str | None = None
    status: Literal["new", "in_progress", "ready", "frozen"]
    folder: str | None = None


class StepGroup(Row):
    group_id: Id
    use_case_id: Id
    position: Position = Field(ge=1)
    name: str
    color: str | None = None


class Step(Row):
    step_id: Id
    use_case_id: Id
    position: Position = Field(ge=1)
    name: str
    description: str | None = None
    trigger_text: str | None = None
    group_id: Id | None = None
    note: str | None = None
    image_url: str | None = None


class StepTrigger(Row):
    trigger_id: Id
    step_id: Id
    kind: Literal["trigger", "condition"]
    position: Position = Field(ge=1)
    component_id: Id
    state_id: Id
    operator: Literal["is"]


class Persona(Row):
    persona_id: Id
    name: str
    age_category: Literal["child", "young_adult", "active_adult", "senior"] | None = None
    gender: Literal["female", "male", "other"] | None = None
    biography: str | None = None
    color: str | None = None
    library_id: Id | None = None
    library_name: str | None = None
    library_link_mode: Literal["linked", "copied"] | None = None


class UseCasePersona(Row):
    use_case_id: Id
    persona_id: Id
    position: Position = Field(ge=1)


class PersonaStepFeedback(Row):
    feedback_id: Id
    step_id: Id
    persona_id: Id
    is_present: bool
    action: str | None = None
    insight: str | None = None
    emotion_score: int | None = Field(default=None, ge=-5, le=5)


class Component(Row):
    component_id: Id
    name: str
    role: Literal["trigger", "reactive", "object"]
    category: str | None = None
    parent_component_id: Id | None = None
    description: str | None = None
    library_id: Id | None = None
    library_name: str | None = None
    library_link_mode: Literal["linked", "copied"] | None = None


class ComponentState(Row):
    state_id: Id
    component_id: Id
    position: Position = Field(ge=1)
    name: str
    description: str | None = None


class UseCaseComponent(Row):
    use_case_id: Id
    component_id: Id
    position: Position = Field(ge=1)


class StepComponentState(Row):
    step_id: Id
    component_id: Id
    state_id: Id
    sequence: Position = Field(ge=1)


class StepUnusedComponent(Row):
    step_id: Id
    component_id: Id


class UserNeed(Row):
    user_need_id: Id
    name: str
    description: str | None = None
    reference_url: str | None = None
    library_id: Id | None = None
    library_name: str | None = None


class StepUserNeed(Row):
    step_id: Id
    user_need_id: Id


class ExportMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str | None = None
    exported_at: str | None = None
    company_name: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    extra: dict[str, str] = Field(default_factory=dict)
