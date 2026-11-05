"""Décisions humaines sur les points détectés automatiquement.

Chaque point à valider (un `Finding` des contrôles) reçoit au plus une décision, prise et signée par
une personne :
  - `guard`         : ajouter la garde qui manque (condition structurée composant/état, ou texte) ;
  - `requirement`   : créer le requirement correspondant à un besoin implicite ;
  - `link_need`     : rattacher l'insight à un user need existant ;
  - `fix_at_source` : la donnée est fausse dans UserShaker, à corriger là-bas (rien n'est ajouté au modèle) ;
  - `dismiss`       : écarter le point (faux positif, choix voulu), avec une raison obligatoire.

Les décisions sont stockées dans `review/decisions.json` (versionné) et rendues par template, de façon
déterministe, dans un package séparé marqué `origin = "human"`. Le contenu UserShaker n'est jamais modifié.
Un point est reconnu d'un export à l'autre par un id stable (`finding_id`), calculé sur ses ids UserShaker.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from p9.checks import Finding, finding_id
from p9.ingest import Export

Action = Literal["guard", "requirement", "link_need", "fix_at_source", "dismiss"]

# Contrôles présentés à la revue (les autres restent informatifs) et actions possibles pour chacun.
REVIEW_CHECKS = ["constraints_in_text_only", "persona_inconsistencies", "implicit_needs"]
ACTIONS_BY_CHECK: dict[str, list[str]] = {
    "constraints_in_text_only": ["guard", "dismiss"],
    "persona_inconsistencies": ["fix_at_source", "dismiss"],
    "implicit_needs": ["requirement", "link_need", "dismiss"],
}
ACTION_LABELS = {
    "guard": "Ajouter la garde",
    "requirement": "Créer un requirement",
    "link_need": "Rattacher à un user need existant",
    "fix_at_source": "À corriger dans UserShaker",
    "dismiss": "Écarter ce point",
}


class Decision(BaseModel):
    finding_id: str
    check: str
    action: Action
    author: str = Field(min_length=1, max_length=80)
    decided_at: str = Field(min_length=10, max_length=10)      # AAAA-MM-JJ
    title: str = ""             # nom de la garde ou du requirement
    text: str = ""              # condition en texte, texte du requirement
    reason: str = ""            # justification (obligatoire pour écarter ou renvoyer à la source)
    component_id: str = ""      # garde structurée : composant…
    state_id: str = ""          # … et état attendu
    need_id: str = ""           # user need existant (link_need)
    target_ids: list[str] = Field(default_factory=list)        # ids UserShaker du point traité
    finding_message: str = ""   # message du point au moment de la décision (si le point disparaît)
    llm_proposal_id: str = ""   # proposition LLM reprise (puis validée ou modifiée par l'humain)
    origin: Literal["human"] = "human"


def decision_errors(d: Decision, ex: Export) -> list[str]:
    """Contrôle d'une décision avant enregistrement ou rendu (champs requis, ids existants)."""
    err = []
    if d.action not in ACTIONS_BY_CHECK.get(d.check, []):
        err.append(f"action « {d.action} » impossible pour ce type de point")
    if d.action == "guard":
        if len(d.title.strip()) < 3:
            err.append("donnez un nom à la garde")
        structured = bool(d.component_id or d.state_id)
        if structured:
            states = ex.by_id("component_states")
            st = states.get(d.state_id)
            if d.component_id not in ex.by_id("components"):
                err.append("composant inconnu")
            elif st is None or st.component_id != d.component_id:
                err.append("l'état choisi n'appartient pas au composant")
        elif len(d.text.strip()) < 3:
            err.append("décrivez la condition, ou choisissez un composant et un état")
    if d.action == "requirement":
        if len(d.title.strip()) < 3:
            err.append("donnez un titre au requirement")
        if len(d.text.strip()) < 10:
            err.append("rédigez le texte du requirement (une phrase vérifiable)")
    if d.action == "link_need" and d.need_id not in ex.by_id("user_needs"):
        err.append("choisissez un user need existant")
    if d.action in {"dismiss", "fix_at_source"} and len(d.reason.strip()) < 5:
        err.append("indiquez la raison (au moins quelques mots)")
    return err


class ReviewStore(BaseModel):
    version: int = 1
    decisions: list[Decision] = Field(default_factory=list)

    def get(self, fid: str) -> Decision | None:
        return next((d for d in self.decisions if d.finding_id == fid), None)

    def upsert(self, d: Decision) -> None:
        self.decisions = sorted([x for x in self.decisions if x.finding_id != d.finding_id] + [d],
                                key=lambda x: (x.check, x.finding_id))

    def remove(self, fid: str) -> None:
        self.decisions = [x for x in self.decisions if x.finding_id != fid]


class ReviewFileError(ValueError):
    """Fichier de décisions présent mais illisible (conflit git, JSON invalide) : on refuse de l'écraser."""


def load_review(path: Path | None) -> ReviewStore:
    """Store vide si le fichier n'existe pas encore. Un fichier illisible lève ReviewFileError : renvoyer un
    store vide ferait disparaître les décisions de l'équipe au prochain enregistrement."""
    if path is None or not path.is_file():
        return ReviewStore()
    try:
        return ReviewStore.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(map(str, err["loc"]))
        detail = f"{where} : {err['msg']}" if where else err["msg"]
    except (OSError, ValueError) as e:
        detail = str(e)
    raise ReviewFileError(f"{path} illisible ({detail}). Corrigez le fichier (conflit git ?) : aucune decision "
                          "n'est appliquee ni enregistree tant qu'il est illisible.")


def save_review(store: ReviewStore, path: Path) -> None:
    """Sortie stable (triée, indentée) pour des diffs git lisibles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = store.model_dump(mode="json", exclude_defaults=False)
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


# --------------------------------------------------------------------- état de la revue

@dataclass
class ReviewItem:
    finding: Finding
    finding_id: str
    decision: Decision | None = None


@dataclass
class ReviewState:
    items: dict[str, list[ReviewItem]] = field(default_factory=dict)   # contrôle → points
    orphans: list[Decision] = field(default_factory=list)              # décisions dont le point a disparu

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.items.values())

    @property
    def done(self) -> int:
        return sum(1 for v in self.items.values() for i in v if i.decision is not None)


def review_state(findings: dict[str, list[Finding]], store: ReviewStore) -> ReviewState:
    state = ReviewState()
    seen: set[str] = set()
    for check in REVIEW_CHECKS:
        rows = []
        for f in findings.get(check, []):
            fid = finding_id(f)
            seen.add(fid)
            rows.append(ReviewItem(finding=f, finding_id=fid, decision=store.get(fid)))
        state.items[check] = rows
    state.orphans = [d for d in store.decisions if d.finding_id not in seen]
    return state


def review_csv(store: ReviewStore) -> str:
    """Journal des décisions (livré avec le modèle)."""
    cols = ["finding_id", "check", "action", "author", "decided_at", "title", "text", "reason", "component_id",
            "state_id", "need_id", "target_ids", "llm_proposal_id", "finding_message"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for d in store.decisions:
        row = d.model_dump(include=set(cols))
        row["target_ids"] = " ".join(d.target_ids)
        w.writerow(row)
    return buf.getvalue()
