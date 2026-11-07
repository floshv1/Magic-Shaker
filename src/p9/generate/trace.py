"""Ligne de la matrice de traçabilité, partagée par generate/ et checks/."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Status = Literal["mapped", "partial", "not_mapped"]


class TraceEntry(BaseModel):
    usershaker_id: str
    kind: str
    sysml_qualified_name: str = ""
    status: Status = "mapped"
    reason: str = ""
    file: str = ""


def link_id(*parts: str | int) -> str:
    """Id d'une ligne de feuille de liens (pas d'id propre) : clés jointes par ':'."""
    return ":".join(str(p) for p in parts)
