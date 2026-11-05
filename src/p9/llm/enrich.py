"""Enrichissement : le LLM lit le texte libre et PROPOSE gardes, requirements et reformulations.

- Entrée minimale (data protection) : seulement les points à valider détectés par les contrôles
  (phrases de trigger_text, insights négatifs, incohérences) et le nom des éléments voisins.
  Pas de biographie, pas d'URL, pas d'export complet.
- Sortie : JSON structuré validé par pydantic ; chaque proposition est `origin="llm"`,
  `status="to_validate"`, et ne cite que des ids qui existent dans l'export.
- Les décisions humaines (accepted / rejected) sont stockées à part (`proposals_file`, versionné :
  review/llm_proposals.json par défaut).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from p9.ingest import Export

from .tools import ToolContext

Kind = Literal["guard", "requirement", "rephrase"]
Decision = Literal["to_validate", "accepted", "rejected"]


TITLE_MAX, TEXT_MAX, TARGETS_MAX = 120, 1200, 8


class RawProposal(BaseModel):
    """Ce que le LLM renvoie (schéma imposé)."""

    kind: Kind
    title: str = Field(min_length=3, max_length=TITLE_MAX)
    text: str = Field(min_length=3, max_length=TEXT_MAX)
    rationale: str = Field(min_length=3, max_length=TEXT_MAX)
    target_ids: list[str] = Field(min_length=1, max_length=TARGETS_MAX)


class Proposal(RawProposal):
    proposal_id: str
    source_item: str = ""
    origin: Literal["llm"] = "llm"
    status: Decision = "to_validate"


class EnrichResult(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)
    invalid: list[dict[str, Any]] = Field(default_factory=list)   # rejetées automatiquement (ids inconnus…)
    payload_items: int = 0
    provider: str = ""


SYSTEM_PROMPT = """\
Tu assistes une equipe qui traduit des parcours utilisateur (UserShaker) en modele SysML v2. \
Pour chaque point a valider fourni, propose au plus deux elements de modele :
- "guard" : une condition structuree qui manque (ex. une contrainte ecrite seulement en texte libre) ;
- "requirement" : un besoin implicite a ajouter (ex. a partir d'un insight negatif) ;
- "rephrase" : une reformulation plus verifiable d'un user need existant.
Chaque proposition cite dans target_ids uniquement des ids presents dans les donnees fournies \
(le step, le feedback ou le user need concerne). Le texte d'un requirement est une phrase verifiable \
("Le vehicule doit ..."). La justification explique en une ou deux phrases le lien avec la source. \
Ne propose rien si le point ne le justifie pas. Reponds en francais.
"""


def output_schema() -> dict[str, Any]:
    """Limites de longueur dans les descriptions : les sorties structurées de l'API Claude refusent minLength /
    maxLength. RawProposal les vérifie ensuite, proposition par proposition."""
    return {
        "type": "object",
        "properties": {"proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["guard", "requirement", "rephrase"]},
                "title": {"type": "string", "description": f"nom court, {TITLE_MAX} caracteres au plus"},
                "text": {"type": "string", "description": f"{TEXT_MAX} caracteres au plus"},
                "rationale": {"type": "string", "description": f"une ou deux phrases, {TEXT_MAX} caracteres au plus"},
                "target_ids": {"type": "array", "items": {"type": "string"},
                               "description": f"1 a {TARGETS_MAX} ids presents dans les donnees fournies"},
            },
            "required": ["kind", "title", "text", "rationale", "target_ids"],
            "additionalProperties": False,
        }}},
        "required": ["proposals"],
        "additionalProperties": False,
    }


def build_payload(ctx: ToolContext) -> list[dict[str, Any]]:
    """Points à valider envoyés au LLM : le strict nécessaire, avec les ids."""
    ex = ctx.export
    steps, needs = ex.by_id("steps"), ex.by_id("user_needs")
    comps, states = ex.by_id("components"), ex.by_id("component_states")

    def step_ctx(step_id: str | None) -> dict[str, Any]:
        s = steps.get(step_id or "")
        if not s:
            return {}
        trig = [{"kind": t.kind, "component": comps[t.component_id].name if t.component_id in comps else "",
                 "state": states[t.state_id].name if t.state_id in states else ""}
                for t in sorted(ex.step_triggers, key=lambda t: t.position) if t.step_id == s.step_id]
        served = [{"user_need_id": r.user_need_id, "name": needs[r.user_need_id].name}
                  for r in ex.step_user_needs if r.step_id == s.step_id and r.user_need_id in needs]
        return {"step_id": s.step_id, "step_name": s.name, "trigger_text": s.trigger_text,
                "structured_triggers": trig, "user_needs_served": served}

    items: list[dict[str, Any]] = []
    for f in ctx.findings["constraints_in_text_only"]:
        items.append({"item_id": f"text_constraint:{f.step_id}:{f.detail.get('index')}", "type": "text_constraint",
                      "sentence": f.detail.get("sentence", ""), **step_ctx(f.step_id)})
    for f in ctx.findings["implicit_needs"]:
        fb_id = f.ids[0]
        items.append({"item_id": f"implicit_need:{fb_id}", "type": "negative_insight", "feedback_id": fb_id,
                      "insight": f.detail.get("insight", ""), "emotion_score": f.detail.get("emotion_score"),
                      **step_ctx(f.step_id)})
    for f in ctx.findings["persona_inconsistencies"]:
        items.append({"item_id": f"persona:{'-'.join(f.ids[:3])}", "type": "persona_inconsistency",
                      "finding": f.message, "ids": f.ids})
    return items


def proposal_id(p: RawProposal) -> str:
    key = json.dumps([p.kind, sorted(p.target_ids), p.title.strip().lower()], ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def validate_proposals(raw_json: str, ex: Export, valid_ids: set[str]) -> EnrichResult:
    """Chaque proposition est validée seule : une proposition non conforme est écartée avec sa raison, sans
    faire perdre les autres."""
    res = EnrichResult()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        res.invalid.append({"error": "sortie LLM non conforme au schema", "detail": f"JSON invalide : {e}"[:500]})
        return res
    items = data.get("proposals") if isinstance(data, dict) else None
    if not isinstance(items, list):
        res.invalid.append({"error": "sortie LLM non conforme au schema", "detail": "liste 'proposals' absente"})
        return res
    seen: set[str] = set()
    for item in items:
        try:
            p = RawProposal.model_validate(item)
        except ValidationError as e:
            title = item.get("title") if isinstance(item, dict) else None
            res.invalid.append({
                "title": str(title or "?")[:TITLE_MAX], "error": "proposition non conforme au schema",
                "detail": "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())[:500]})
            continue
        bad = [i for i in p.target_ids if i not in valid_ids]
        if bad:
            res.invalid.append({"title": p.title, "error": f"ids inconnus : {bad}"})
            continue
        pid = proposal_id(p)
        if pid in seen:
            continue
        seen.add(pid)
        res.proposals.append(Proposal(**p.model_dump(), proposal_id=pid))
    res.proposals.sort(key=lambda p: (p.kind, p.target_ids[0], p.proposal_id))
    return res


def enrich(provider: Any, ctx: ToolContext, on_event: Callable[[str, str], None] | None = None) -> EnrichResult:
    """`on_event(kind, text)` : suivi de l'avancement (voir providers)."""
    def emit(text: str) -> None:
        if on_event is not None:
            on_event("step", text)

    items = build_payload(ctx)
    if not items:
        return EnrichResult(provider=getattr(provider, "name", ""))
    prompt = "Points a valider (JSON) :\n" + json.dumps(items, ensure_ascii=False, indent=1)
    emit(f"Preparation de {len(items)} points a analyser (donnees minimales, {len(prompt)} caracteres envoyes)")
    raw = provider.structured(SYSTEM_PROMPT, prompt, output_schema(), on_event=on_event)
    emit("Verification des propositions recues (format, ids existants)")
    valid = set(ctx.graph.nodes) | {i for it in items for i in it.get("ids", [])}
    res = validate_proposals(raw, ctx.export, valid)
    res.payload_items = len(items)
    res.provider = getattr(provider, "name", "")
    return res


# --------------------------------------------------------------------- décisions humaines

class ProposalStore(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)

    def merge(self, new: list[Proposal]) -> None:
        """Ajoute les nouvelles propositions sans écraser une décision déjà prise."""
        known = {p.proposal_id for p in self.proposals}
        self.proposals.extend(p for p in new if p.proposal_id not in known)
        self.proposals.sort(key=lambda p: (p.kind, p.target_ids[0], p.proposal_id))

    def decide(self, pid: str, status: Decision) -> None:
        self.proposals = [p.model_copy(update={"status": status}) if p.proposal_id == pid else p
                          for p in self.proposals]

    def accepted(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "accepted"]


LEGACY_STORE = "proposals.json"     # ancien emplacement, dans out/ (ignoré par git, effacé par `make clean`)


def store_path(proposals_file: Path | None, out_dir: Path) -> Path:
    """Fichier de propositions à lire : `proposals_file`, ou l'ancien out/proposals.json tant qu'il n'existe pas
    (les décisions déjà prises restent appliquées ; le prochain enregistrement écrit `proposals_file`)."""
    legacy = out_dir / LEGACY_STORE
    if proposals_file is None:
        return legacy
    return legacy if not proposals_file.is_file() and legacy.is_file() else proposals_file


class ProposalFileError(ValueError):
    """Fichier de propositions présent mais illisible : on refuse de l'écraser (décisions accepted / rejected)."""


def load_store(path: Path) -> ProposalStore:
    """Store vide si le fichier n'existe pas encore ; un fichier illisible lève ProposalFileError."""
    if not path.is_file():
        return ProposalStore()
    try:
        return ProposalStore.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(map(str, err["loc"]))
        detail = f"{where} : {err['msg']}" if where else err["msg"]
    except (OSError, ValueError) as e:
        detail = str(e)
    raise ProposalFileError(f"{path} illisible ({detail}). Corrigez ou supprimez le fichier : aucune proposition "
                            "n'est appliquee ni enregistree tant qu'il est illisible.")


def save_store(store: ProposalStore, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(store.model_dump_json(indent=2).encode("utf-8"))
