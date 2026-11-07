"""Configuration du pipeline : lue dans pyproject.toml ([tool.p9]), surchargeable par
variables d'environnement (P9_DATA_DIR, P9_OUT_DIR, P9_REVIEW_FILE, P9_PROPOSALS_FILE, P9_SYSML_HOME, P9_JAVA_HOME,
P9_LLM_*) ou `.env`."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULTS = {"data_dir": "data/usershaker", "out_dir": "out", "review_file": "review/decisions.json",
             "proposals_file": "review/llm_proposals.json", "llm_eval_file": "evals/llm_questions.json"}
_GENERATE_DEFAULTS = {
    "project_package": "Project",
    "common_package": "Common",
    "system_name": "System",
    "subject_name": "system",
}
_LLM_DEFAULTS = {
    "provider": "anthropic",
    "anthropic_model": "claude-opus-5-5",
    "effort": "medium",
    "ollama_host": "http://localhost:11434",
    "ollama_model": "qwen2.5:7b",
}
_CATIA_DEFAULTS = {"char_limit": 20000, "char_budget": 19000}
_OMG_DEFAULTS = {
    "kernel_version": "",
    "kernel_url": "",
    "jdk_url_template": "",
    "sysml_home": "~/.cache/p9-sysml",
}


def find_root(start: Path | None = None) -> Path:
    """Remonte depuis `start` (ou le cwd) jusqu'au dossier contenant pyproject.toml."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(f"pyproject.toml introuvable au-dessus de {here}")


def _load_dotenv(root: Path) -> None:
    """Charge `.env` (KEY=VALUE) sans écraser l'environnement existant."""
    env = root / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class GenerateSettings:
    project_package: str
    common_package: str
    system_name: str
    subject_name: str


@dataclass(frozen=True)
class CatiaSettings:
    char_limit: int     # l'éditeur textuel de CATIA Community refuse au-delà (par espace de noms racine)
    char_budget: int    # cible du générateur : marge pour les vues ajoutées à la main dans CATIA


@dataclass(frozen=True)
class OmgSettings:
    kernel_version: str
    kernel_url: str
    jdk_url_template: str
    sysml_home: Path
    java_home: Path | None


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    anthropic_model: str
    effort: str
    ollama_host: str
    ollama_model: str


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    out_dir: Path
    generate: GenerateSettings = field(default_factory=lambda: GenerateSettings(**_GENERATE_DEFAULTS))
    omg: OmgSettings | None = None
    llm: LlmSettings = field(default_factory=lambda: LlmSettings(**_LLM_DEFAULTS))
    catia: CatiaSettings = field(default_factory=lambda: CatiaSettings(**_CATIA_DEFAULTS))
    review_file: Path | None = None      # décisions humaines sur les points à valider (versionné)
    proposals_file: Path | None = None   # propositions LLM et leur statut accepted / rejected (versionné)
    llm_eval_file: Path | None = None    # questions de mesure de l'agent de chat (versionné)


def load_settings(start: Path | None = None) -> Settings:
    root = find_root(start)
    _load_dotenv(root)
    with open(root / "pyproject.toml", "rb") as f:
        tool = tomllib.load(f).get("tool", {}).get("p9", {})
    cfg = {**_DEFAULTS, **{k: v for k, v in tool.items() if not isinstance(v, dict)}}

    def resolve(key: str) -> Path:
        path = Path(os.environ.get(f"P9_{key.upper()}", cfg[key]))
        return path if path.is_absolute() else root / path

    gen = GenerateSettings(**{**_GENERATE_DEFAULTS, **tool.get("generate", {})})
    omg_cfg = {**_OMG_DEFAULTS, **tool.get("omg", {})}
    java_home = os.environ.get("P9_JAVA_HOME")
    omg = OmgSettings(
        kernel_version=omg_cfg["kernel_version"],
        kernel_url=omg_cfg["kernel_url"],
        jdk_url_template=omg_cfg["jdk_url_template"],
        sysml_home=Path(os.environ.get("P9_SYSML_HOME", omg_cfg["sysml_home"])).expanduser(),
        java_home=Path(java_home) if java_home else None,
    )
    llm_cfg = {**_LLM_DEFAULTS, **tool.get("llm", {})}
    llm = LlmSettings(
        provider=os.environ.get("P9_LLM_PROVIDER", llm_cfg["provider"]),
        anthropic_model=os.environ.get("P9_LLM_MODEL", llm_cfg["anthropic_model"]),
        effort=os.environ.get("P9_LLM_EFFORT", llm_cfg["effort"]),
        ollama_host=os.environ.get("OLLAMA_HOST", llm_cfg["ollama_host"]),
        ollama_model=os.environ.get("P9_OLLAMA_MODEL", llm_cfg["ollama_model"]),
    )
    catia = CatiaSettings(**{**_CATIA_DEFAULTS, **tool.get("catia", {})})
    return Settings(root=root, data_dir=resolve("data_dir"), out_dir=resolve("out_dir"), generate=gen, omg=omg,
                    llm=llm, catia=catia, review_file=resolve("review_file"), proposals_file=resolve("proposals_file"),
                    llm_eval_file=resolve("llm_eval_file"))
