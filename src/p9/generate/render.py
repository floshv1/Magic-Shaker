"""Rendu Jinja2 → fichiers .sysml (déterministe, octet par octet)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from p9.config import GenerateSettings, load_settings
from p9.ingest import Export

from .build import build_common, build_use_case, compute_common_names, use_case_package
from .proposals import build_proposals
from .review import build_review
from .trace import TraceEntry

TEMPLATES = Path(__file__).parent / "templates"


# --------------------------------------------------------------------- filtres

def sysml_string(value: object) -> str:
    """Littéral chaîne SysML : guillemets doubles, échappements KerML."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f'"{s}"'


def _clean_comment(text: str) -> list[str]:
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("*/", "* /").replace("/*", "/ *")
    return [line.rstrip() for line in text.strip().split("\n")]


def _block(head: str, text: str, ind: str) -> str:
    lines = _clean_comment(text)
    if len(lines) == 1:
        return f"{ind}{head} /* {lines[0]} */"
    body = "\n".join(f"{ind} * {line}".rstrip() for line in lines)
    return f"{ind}{head}\n{ind}/*\n{body}\n{ind} */"


def doc_block(text: str, ind: str) -> str:
    return _block("doc", text or "-", ind)


def comment_block(text: str, name: str, ind: str) -> str:
    return _block(f"comment {name}", text or "-", ind)


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    env.filters["q"] = sysml_string
    env.filters["doc_block"] = doc_block
    env.filters["comment_block"] = comment_block
    return env


# --------------------------------------------------------------------- génération

@dataclass
class Generated:
    files: dict[str, str] = field(default_factory=dict)   # nom de fichier → contenu (commun d'abord)
    trace: list[TraceEntry] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)      # use_case_id générés, dans l'ordre
    single_file: str = ""                                  # tous les packages dans un seul package projet

    def digest(self) -> str:
        h = hashlib.sha256()
        for name, text in self.files.items():
            h.update(name.encode() + b"\0" + text.encode() + b"\0")
        return h.hexdigest()


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:                       # pas plus d'une ligne vide d'affilée
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def generate(ex: Export, selected: list[str] | None = None, cfg: GenerateSettings | None = None,
             accepted_proposals: list | None = None, review_decisions: list | None = None) -> Generated:
    """Génère le fichier commun et un fichier par use case sélectionné (tous si None).

    `accepted_proposals` : propositions LLM acceptées par un humain, rendues dans un package
    séparé (hors du fichier unique model.sysml, pour ne pas mélanger avec le contenu d'origine).
    `review_decisions` : décisions humaines sur les points à valider (p9.review), rendues elles aussi
    dans un package séparé (origin = "human") quand elles ajoutent un élément de modèle.
    """
    cfg = cfg or load_settings().generate
    ucs = ex.use_cases_sorted()
    wanted = set(selected) if selected is not None else {u.use_case_id for u in ucs}
    ucs = [u for u in ucs if u.use_case_id in wanted]
    order = [u.use_case_id for u in ucs]

    env = make_env()
    names = compute_common_names(ex, cfg)
    out = Generated(selected=order)

    ctx, trace = build_common(ex, cfg, names, order)
    out.files[f"{cfg.common_package}.sysml"] = _normalize(env.get_template("common.sysml.j2").render(**ctx))
    out.trace.extend(trace)
    for uc in ucs:
        uctx, utrace = build_use_case(ex, cfg, names, uc)
        out.files[f"{use_case_package(uc)}.sysml"] = _normalize(env.get_template("use_case.sysml.j2").render(**uctx))
        out.trace.extend(utrace)

    body = "\n".join(out.files.values())
    out.single_file = _normalize(
        f"package {cfg.project_package} {{\n"
        f"    doc /* Modele complet en un seul fichier : memes packages que les fichiers separes. */\n\n"
        f"{body}}}\n"
    )
    if accepted_proposals:
        pkg, pctx, ptrace = build_proposals(cfg, accepted_proposals, out.trace, names.requirement)
        out.files[f"{pkg}.sysml"] = _normalize(env.get_template("llm_proposals.sysml.j2").render(**pctx))
        out.trace.extend(ptrace)
    if review_decisions:
        rpkg, rctx, rtrace = build_review(cfg, review_decisions, out.trace, names,
                                          {c.component_id: c.name for c in ex.components},
                                          {s.state_id: s.name for s in ex.component_states},
                                          {f.feedback_id: f.step_id for f in ex.persona_step_feedback},
                                          {s.step_id for s in ex.steps})
        if rctx["items"]:
            out.files[f"{rpkg}.sysml"] = _normalize(env.get_template("review.sysml.j2").render(**rctx))
        out.trace.extend(rtrace)
    return out


def make_zip(files: dict[str, str | bytes]) -> bytes:
    """Zip déterministe (ordre fixe, date fixe) : même contenu → mêmes octets."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            data = files[name]
            z.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def write(gen: Generated, out_dir: Path) -> list[Path]:
    """Écrit les fichiers séparés dans out_dir/sysml/ et le fichier unique out_dir/model.sysml."""
    sysml_dir = out_dir / "sysml"
    sysml_dir.mkdir(parents=True, exist_ok=True)
    for old in sysml_dir.glob("*.sysml"):
        if old.name not in gen.files:
            old.unlink()
    paths = []
    for name, text in gen.files.items():
        p = sysml_dir / name
        p.write_bytes(text.encode("utf-8"))
        paths.append(p)
    model = out_dir / "model.sysml"
    model.write_bytes(gen.single_file.encode("utf-8"))
    paths.append(model)
    return paths
