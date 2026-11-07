"""Durées mesurées des opérations longues (parseur OMG, appels LLM), pour estimer les suivantes.

Les mesures sont gardées dans un petit fichier JSON (par défaut out/timings.json, ignoré par git) :
`{"<clé>": [durée1, durée2, …]}` (les 5 dernières, en secondes). L'estimation est la médiane.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

KEEP = 5


def _load(path: Path) -> dict[str, list[float]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [float(x) for x in v if isinstance(x, int | float) and x >= 0]
            for k, v in data.items() if isinstance(v, list)}


def estimate(path: Path, key: str) -> float | None:
    """Durée probable (médiane des dernières mesures), ou None si jamais mesurée."""
    values = _load(path).get(key) or []
    return statistics.median(values) if values else None


def record(path: Path, key: str, seconds: float) -> None:
    data = _load(path)
    data[key] = [*data.get(key, []), round(float(seconds), 2)][-KEEP:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass          # une mesure perdue n'empêche rien
