"""Matrice de traçabilité complète et tableau de couverture.

Principe : on part de l'inventaire de TOUTES les lignes de l'export (une ligne par id, ou par
clé composite pour les feuilles de liens) et on y rattache ce que le générateur a produit.
Ce qui n'a pas été produit apparaît `not_mapped` avec une raison : rien ne disparaît en silence.
"""

from __future__ import annotations

import csv
import io
from collections import Counter

from pydantic import BaseModel

from p9.generate import Generated, TraceEntry, link_id
from p9.ingest import SHEETS, Export

from .consistency import Finding, constraints_in_text_only, finding_id

KIND_OF_SHEET = {
    "use_cases": "use_case", "step_groups": "step_group", "steps": "step", "step_triggers": "step_trigger",
    "personas": "persona", "use_case_personas": "use_case_persona", "persona_step_feedback": "persona_step_feedback",
    "components": "component", "component_states": "component_state", "use_case_components": "use_case_component",
    "step_component_states": "step_component_state", "step_unused_components": "step_unused_component",
    "user_needs": "user_need", "step_user_needs": "step_user_need",
}
KIND_ORDER = ["project", *KIND_OF_SHEET.values(), "text_constraint"]
COLUMNS = ["usershaker_id", "kind", "sysml_qualified_name", "status", "reason", "file"]


def _row_key(sheet: str, row) -> str:
    return link_id(*(getattr(row, k) for k in SHEETS[sheet].key))


def _owner_use_case(ex: Export, sheet: str, row) -> str | None:
    """Use case auquel une ligne appartient (None si elle est partagée)."""
    step_uc = {s.step_id: s.use_case_id for s in ex.steps}
    if hasattr(row, "use_case_id") and sheet != "use_cases":
        return row.use_case_id
    if sheet == "use_cases":
        return row.use_case_id
    if hasattr(row, "step_id"):
        return step_uc.get(row.step_id)
    return None


def build_matrix(ex: Export, gen: Generated, findings: list[Finding] | None = None,
                 decisions: list | None = None) -> list[TraceEntry]:
    """`decisions` : décisions humaines (p9.review) ; une contrainte en texte libre traitée par une garde
    ou écartée change de statut, avec l'auteur, la date et la raison."""
    produced: dict[tuple[str, str], TraceEntry] = {}
    for e in gen.trace:
        produced.setdefault((e.kind, e.usershaker_id), e)
    selected = set(gen.selected)
    out: list[TraceEntry] = []
    if ex.meta.project_id:
        out.append(produced.get(("project", ex.meta.project_id)) or TraceEntry(
            usershaker_id=ex.meta.project_id, kind="project", status="not_mapped", reason="non genere"))
    for sheet, kind in KIND_OF_SHEET.items():
        for row in ex.rows(sheet):
            key = _row_key(sheet, row)
            e = produced.get((kind, key))
            if e is None:
                owner = _owner_use_case(ex, sheet, row)
                reason = "use case non selectionne" if owner and owner not in selected else "non traduit par le generateur"
                e = TraceEntry(usershaker_id=key, kind=kind, status="not_mapped", reason=reason)
            out.append(e)
    # Contraintes vues uniquement dans le texte libre : perte d'information explicite, sauf décision humaine
    # (garde ajoutée, ou point écarté avec sa raison).
    by_finding = {d.finding_id: d for d in decisions or []}
    for f in findings if findings is not None else constraints_in_text_only(ex):
        if f.check != "constraints_in_text_only" or (f.use_case_id and f.use_case_id not in selected):
            continue
        step_entry = produced.get(("step", f.step_id or ""))
        sentence = f.detail.get("sentence", "")
        entry = TraceEntry(
            usershaker_id=f"{f.step_id}#trigger_text[{f.detail.get('index', '?')}]", kind="text_constraint",
            sysml_qualified_name=f"{step_entry.sysml_qualified_name}::triggerText" if step_entry else "",
            status="partial",
            reason=f"contrainte seulement en texte libre (commentaire), sans garde structuree : {sentence}",
            file=step_entry.file if step_entry else "",
        )
        d = by_finding.get(finding_id(f))
        guard = produced.get(("review_decision", d.finding_id)) if d is not None else None
        if d is not None and d.action == "guard" and guard is not None and guard.status != "not_mapped":
            entry = entry.model_copy(update={
                "sysml_qualified_name": guard.sysml_qualified_name, "status": guard.status, "file": guard.file,
                "reason": f"garde ajoutee par {d.author} le {d.decided_at} (origin=human) : {sentence}"})
        elif d is not None and d.action == "dismiss":
            entry = entry.model_copy(update={
                "status": "mapped",
                "reason": f"ecarte par {d.author} le {d.decided_at} : {d.reason.strip()} ({sentence})"})
        out.append(entry)
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    return sorted(out, key=lambda e: (order.get(e.kind, 99), e.usershaker_id))


def matrix_csv(matrix: list[TraceEntry]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for e in matrix:
        w.writerow(e.model_dump(include=set(COLUMNS)))
    return buf.getvalue()


def status_counts(matrix: list[TraceEntry]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for e in matrix:
        out.setdefault(e.kind, Counter())[e.status] += 1
    return {k: dict(v) for k, v in out.items()}


# --------------------------------------------------------------------- couverture

class CoverageRow(BaseModel):
    element: str
    in_export: int
    translated: int
    partial: int
    note: str

    @property
    def percent(self) -> int | None:
        if self.in_export == 0:
            return None
        # arrondi vers le bas : jamais 100 % si un élément manque
        return (100 * self.translated) // self.in_export

    @property
    def percent_label(self) -> str:
        return "-" if self.percent is None else f"{self.percent} %"


def coverage(ex: Export, gen: Generated, matrix: list[TraceEntry]) -> list[CoverageRow]:
    """Pour les use cases sélectionnés : dans l'export / traduit (mapped) / partiel, par famille."""
    sel = set(gen.selected)
    steps = [s for s in ex.steps if s.use_case_id in sel]
    step_ids = {s.step_id for s in steps}
    personas = sorted({r.persona_id for r in ex.use_case_personas if r.use_case_id in sel})
    needs = sorted({r.user_need_id for r in ex.step_user_needs if r.step_id in step_ids})
    triggers = [t for t in ex.step_triggers if t.step_id in step_ids]
    grid = [r for r in ex.step_component_states if r.step_id in step_ids]
    feedback = [f for f in ex.persona_step_feedback if f.step_id in step_ids]
    by = {(e.kind, e.usershaker_id): e for e in matrix}
    text_constraints = [e for e in matrix if e.kind == "text_constraint"]

    def row(element: str, kind: str, ids: list[str], note_ok: str = "") -> CoverageRow:
        entries = [by.get((kind, i)) for i in ids]
        mapped = sum(1 for e in entries if e and e.status == "mapped")
        partial = sum(1 for e in entries if e and e.status == "partial")
        missing = len(ids) - mapped - partial
        notes = []
        if partial:
            reasons = Counter(e.reason for e in entries if e and e.status == "partial")
            notes.append(f"{partial} partiel(s) : " + " ; ".join(f"{r} ({n})" for r, n in reasons.most_common(3)))
        if missing:
            notes.append(f"{missing} non traduit(s)")
        return CoverageRow(element=element, in_export=len(ids), translated=mapped, partial=partial,
                           note=" | ".join(notes) or note_ok)

    rows = [
        row("Use cases", "use_case", sorted(sel)),
        row("Steps", "step", sorted(step_ids)),
        row("Personas", "persona", personas),
        row("User needs servis", "user_need", needs),
        row("Triggers / conditions", "step_trigger", sorted(t.trigger_id for t in triggers),
            "accept / garde if de transitions"),
        row("Etats du blueprint", "step_component_state",
            sorted(link_id(r.step_id, r.component_id, r.sequence) for r in grid), "etats et transitions deduites"),
        row("Feedback personas", "persona_step_feedback", sorted(f.feedback_id for f in feedback)),
    ]
    if text_constraints:
        rows.append(CoverageRow(
            element="Contraintes en texte libre", in_export=len(text_constraints), translated=0,
            partial=len(text_constraints),
            note="conservees en commentaire, sans condition structuree : a modeliser (voir Points a valider)"))
    return rows
