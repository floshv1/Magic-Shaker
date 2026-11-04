"""`python -m p9.ingest [chemin]` : charge l'export et affiche le rapport. Code 1 si à vérifier."""

from __future__ import annotations

import sys

from p9.config import load_settings

from . import ExportUnreadable, load_path


def main(argv: list[str]) -> int:
    path = argv[0] if argv else load_settings().data_dir
    try:
        ex = load_path(path)
    except ExportUnreadable as e:
        print(f"Erreur : {e}")
        return 2
    m = ex.meta
    print(f"Projet : {m.project_name} ({m.company_name}) - export {m.exported_at} - schema {m.schema_version}")
    print(f"{len(ex.use_cases)} use cases, {len(ex.steps)} steps")
    for line in ex.report.summary_lines():
        print(line)
    print("Controles OK" if ex.report.ok else "A verifier")
    return 0 if ex.report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
