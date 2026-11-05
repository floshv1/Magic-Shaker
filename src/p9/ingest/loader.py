"""Chargement d'un export UserShaker (zip ou dossier) et contrôles d'intégrité.

Fonctions pures : on passe des octets ou un chemin, on récupère un `Export`
(données validées + rapport). Aucune exception ne remonte pour un fichier mal
formé : tout problème est consigné dans `Export.report`.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import models as m
from .schema import (
    ALL_SHEETS,
    FOREIGN_KEYS,
    META_SHEET,
    ORDERINGS,
    REQUIRED_SHEETS,
    SCHEMA_VERSION,
    SHEETS,
    expected_columns,
    required_columns,
)


class ExportUnreadable(Exception):
    """Le conteneur lui-même est illisible (zip corrompu, dossier absent)."""


# --------------------------------------------------------------------- rapport

class CountCheck(BaseModel):
    sheet: str
    expected: int | None
    loaded: int | None

    @property
    def ok(self) -> bool:
        return self.expected is not None and self.loaded is not None and self.expected == self.loaded


class FkError(BaseModel):
    sheet: str
    column: str
    target: str
    missing_ids: list[str]


class Issue(BaseModel):
    sheet: str
    message: str
    line: int | None = None   # ligne du CSV (en-tête = 1)


class IngestReport(BaseModel):
    counts: list[CountCheck] = Field(default_factory=list)
    missing_sheets: list[str] = Field(default_factory=list)
    fk_errors: list[FkError] = Field(default_factory=list)
    row_errors: list[Issue] = Field(default_factory=list)
    order_errors: list[Issue] = Field(default_factory=list)
    duplicate_keys: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)       # non bloquant mais signalé
    ignored_files: list[str] = Field(default_factory=list)    # CSV non reconnus

    @property
    def missing_required(self) -> list[str]:
        return [s for s in REQUIRED_SHEETS if s in self.missing_sheets]

    @property
    def ok(self) -> bool:
        return (
            not self.missing_sheets
            and all(c.ok for c in self.counts)
            and not self.fk_errors
            and not self.row_errors
            and not self.order_errors
            and not self.duplicate_keys
        )

    def summary_lines(self) -> list[str]:
        out = []
        for c in self.counts:
            flag = "OK " if c.ok else "KO "
            out.append(f"{flag} {c.sheet:<24} attendu={c.expected} charge={c.loaded}")
        for s in self.missing_sheets:
            out.append(f"KO  feuille manquante : {s}")
        for e in self.fk_errors:
            out.append(f"KO  {e.sheet}.{e.column} -> {e.target} : {len(e.missing_ids)} id introuvable(s) {e.missing_ids[:3]}")
        for kind, issues in (("ligne", self.row_errors), ("ordre", self.order_errors),
                             ("doublon", self.duplicate_keys), ("info", self.warnings)):
            for i in issues:
                where = f"{i.sheet}" + (f":{i.line}" if i.line else "")
                out.append(f"{'..' if kind == 'info' else 'KO'}  [{kind}] {where} {i.message}")
        for f in self.ignored_files:
            out.append(f"..  fichier ignore (feuille inconnue) : {f}")
        return out


# --------------------------------------------------------------------- export

class Export(BaseModel):
    model_config = ConfigDict(frozen=True)

    meta: m.ExportMeta = Field(default_factory=m.ExportMeta)
    use_cases: list[m.UseCase] = Field(default_factory=list)
    step_groups: list[m.StepGroup] = Field(default_factory=list)
    steps: list[m.Step] = Field(default_factory=list)
    step_triggers: list[m.StepTrigger] = Field(default_factory=list)
    personas: list[m.Persona] = Field(default_factory=list)
    use_case_personas: list[m.UseCasePersona] = Field(default_factory=list)
    persona_step_feedback: list[m.PersonaStepFeedback] = Field(default_factory=list)
    components: list[m.Component] = Field(default_factory=list)
    component_states: list[m.ComponentState] = Field(default_factory=list)
    use_case_components: list[m.UseCaseComponent] = Field(default_factory=list)
    step_component_states: list[m.StepComponentState] = Field(default_factory=list)
    step_unused_components: list[m.StepUnusedComponent] = Field(default_factory=list)
    user_needs: list[m.UserNeed] = Field(default_factory=list)
    step_user_needs: list[m.StepUserNeed] = Field(default_factory=list)
    report: IngestReport = Field(default_factory=IngestReport)

    def rows(self, sheet: str) -> list[m.Row]:
        return getattr(self, SHEETS[sheet].attr)

    def frame(self, sheet: str) -> pd.DataFrame:
        """Vue DataFrame d'une feuille (pour l'affichage)."""
        cols = expected_columns(sheet)
        return pd.DataFrame([r.model_dump() for r in self.rows(sheet)], columns=cols)

    # index utilitaires (données petites : recalcul à la demande)
    def by_id(self, sheet: str) -> dict[str, m.Row]:
        key = SHEETS[sheet].key
        if len(key) != 1:
            raise ValueError(f"{sheet} n'a pas de clé simple")
        return {getattr(r, key[0]): r for r in self.rows(sheet)}

    def use_cases_sorted(self) -> list[m.UseCase]:
        return sorted(self.use_cases, key=lambda u: (u.position, u.use_case_id))

    def steps_of(self, use_case_id: str) -> list[m.Step]:
        return sorted((s for s in self.steps if s.use_case_id == use_case_id),
                      key=lambda s: (s.position, s.step_id))


# --------------------------------------------------------------------- lecture

def sheet_name(path: str) -> str | None:
    """Associe un chemin de fichier à une feuille, ou None.

    Nom exact (`components.csv`), ou préfixe séparé par '-' (`xxxx-components.csv`).
    La correspondance se fait sur le nom complet après le dernier '-', jamais par
    suffixe libre : `use_case_components` ne capture pas `components`.
    """
    parts = path.replace("\\", "/").split("/")
    base = parts[-1]
    if any(p == "__MACOSX" for p in parts) or base.startswith("._") or not base.lower().endswith(".csv"):
        return None
    stem = base[:-4].lower()
    if stem in ALL_SHEETS:
        return stem
    if "-" in stem:
        tail = stem.rsplit("-", 1)[1]
        if tail in ALL_SHEETS:
            return tail
    return None


def _read_csv(raw: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")


def _iter_zip(data: bytes) -> Iterable[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as e:
        raise ExportUnreadable(f"zip illisible : {e}") from e
    except RuntimeError as e:     # chiffré (mot de passe) ou compression non gérée (NotImplementedError)
        raise ExportUnreadable(f"zip illisible : {e} (reexporter en zip standard, sans mot de passe)") from e


def _iter_dir(path: Path) -> Iterable[tuple[str, bytes]]:
    if not path.is_dir():
        raise ExportUnreadable(f"dossier introuvable : {path}")
    return [(p.relative_to(path).as_posix(), p.read_bytes()) for p in sorted(path.rglob("*")) if p.is_file()]


def load_zip(data: bytes) -> Export:
    return load_files(_iter_zip(data))


def load_dir(path: str | Path) -> Export:
    return load_files(_iter_dir(Path(path)))


def load_path(path: str | Path) -> Export:
    p = Path(path)
    if p.is_file():
        return load_zip(p.read_bytes())
    return load_dir(p)


def load_files(files: Iterable[tuple[str, bytes]]) -> Export:
    report = IngestReport()
    frames: dict[str, pd.DataFrame] = {}
    for name, raw in sorted(files, key=lambda f: f[0]):
        sheet = sheet_name(name)
        if sheet is None:
            if name.lower().endswith(".csv") and "__MACOSX" not in name and not name.rsplit("/", 1)[-1].startswith("._"):
                report.ignored_files.append(name)
            continue
        if sheet in frames:
            report.warnings.append(Issue(sheet=sheet, message=f"plusieurs fichiers pour cette feuille, '{name}' ignore"))
            continue
        try:
            frames[sheet] = _read_csv(raw)
        except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
            report.row_errors.append(Issue(sheet=sheet, message=f"CSV illisible : {e}"))

    report.missing_sheets = [s for s in ALL_SHEETS if s not in frames]
    meta = _parse_meta(frames.get(META_SHEET), report)

    data: dict[str, list[m.Row]] = {}
    for sheet, spec in SHEETS.items():
        df = frames.get(sheet)
        data[spec.attr] = [] if df is None else _parse_sheet(sheet, df, report)

    report.counts = [
        CountCheck(sheet=s, expected=meta.counts.get(s), loaded=len(frames[s]) if s in frames else None)
        for s in SHEETS
    ]
    ex = Export(meta=meta, report=report, **data)
    report.fk_errors.extend(_check_foreign_keys(ex, present=set(frames)))
    report.order_errors.extend(_check_orderings(ex))
    report.duplicate_keys.extend(_check_keys(ex))
    return ex


def _parse_meta(df: pd.DataFrame | None, report: IngestReport) -> m.ExportMeta:
    if df is None or not {"key", "value"} <= set(df.columns):
        if df is not None:
            report.row_errors.append(Issue(sheet=META_SHEET, message="colonnes key/value absentes"))
        return m.ExportMeta()
    kv = dict(zip(df["key"], df["value"]))
    counts: dict[str, int] = {}
    extra: dict[str, str] = {}
    for k, v in kv.items():
        if k.startswith("count_"):
            try:
                counts[k[len("count_"):]] = int(v)
            except ValueError:
                report.row_errors.append(Issue(sheet=META_SHEET, message=f"{k} n'est pas un entier : {v!r}"))
        elif k not in {"schema_version", "exported_at", "company_name", "project_id", "project_name"}:
            extra[k] = v
    meta = m.ExportMeta(
        schema_version=kv.get("schema_version") or None,
        exported_at=kv.get("exported_at") or None,
        company_name=kv.get("company_name") or None,
        project_id=kv.get("project_id") or None,
        project_name=kv.get("project_name") or None,
        counts=counts,
        extra=extra,
    )
    if meta.schema_version != SCHEMA_VERSION:
        report.warnings.append(Issue(
            sheet=META_SHEET, message=f"schema_version={meta.schema_version!r}, attendu {SCHEMA_VERSION!r}"))
    for k in extra:
        report.warnings.append(Issue(sheet=META_SHEET, message=f"cle meta inconnue conservee : {k}"))
    return meta


def _parse_sheet(sheet: str, df: pd.DataFrame, report: IngestReport) -> list[m.Row]:
    model = SHEETS[sheet].model
    cols = set(df.columns)
    missing = [c for c in required_columns(sheet) if c not in cols]
    if missing:
        report.row_errors.append(Issue(sheet=sheet, message=f"colonnes obligatoires absentes : {missing}"))
        return []
    unknown = [c for c in df.columns if c not in expected_columns(sheet)]
    if unknown:
        report.warnings.append(Issue(sheet=sheet, message=f"colonnes inconnues conservees : {unknown}"))
    rows: list[m.Row] = []
    for i, rec in enumerate(df.to_dict("records")):
        try:
            rows.append(model.model_validate(rec))
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            report.row_errors.append(Issue(sheet=sheet, line=i + 2, message=msgs))
    return rows


def _check_foreign_keys(ex: Export, present: set[str]) -> list[FkError]:
    errs = []
    for sheet, col, target, tcol in FOREIGN_KEYS:
        if sheet not in present or target not in present:
            continue  # feuille absente : déjà signalée dans missing_sheets
        known = {getattr(r, tcol) for r in ex.rows(target)}
        bad = sorted({v for r in ex.rows(sheet) if (v := getattr(r, col, None)) and v not in known})
        if bad:
            errs.append(FkError(sheet=sheet, column=col, target=target, missing_ids=bad))
    return errs


def _check_orderings(ex: Export) -> list[Issue]:
    issues = []
    for sheet, col, parents in ORDERINGS:
        groups: dict[tuple, list[int]] = defaultdict(list)
        for r in ex.rows(sheet):
            groups[tuple(getattr(r, p) for p in parents)].append(getattr(r, col))
        for parent, values in sorted(groups.items()):
            if sorted(values) != list(range(1, len(values) + 1)):
                where = "/".join(parent) or "(global)"
                issues.append(Issue(sheet=sheet, message=f"{col} non contigu dans {where} : {sorted(values)}"))
    return issues


def _check_keys(ex: Export) -> list[Issue]:
    issues = []
    for sheet, spec in SHEETS.items():
        counter = Counter(tuple(getattr(r, k) for k in spec.key) for r in ex.rows(sheet))
        for key, n in sorted(counter.items()):
            if n > 1:
                issues.append(Issue(sheet=sheet, message=f"cle {'/'.join(map(str, key))} presente {n} fois"))
    return issues
