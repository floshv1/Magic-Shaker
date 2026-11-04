"""`python -m p9.checks` : out/traceability.csv, out/coverage.html, out/findings.json.

Code retour 1 si un contrôle bloquant échoue (comptes, clés étrangères, triggers invalides,
élément non traduit d'un use case généré).
"""

from __future__ import annotations

import json
import sys

from p9.config import load_settings
from p9.generate import generate
from p9.ingest import ExportUnreadable, load_path

from . import build_matrix, coverage, coverage_html, matrix_csv, run_all, status_counts


def main(argv: list[str]) -> int:
    s = load_settings()
    try:
        ex = load_path(argv[0] if argv else s.data_dir)
    except ExportUnreadable as e:
        print(f"Erreur : {e}")
        return 2
    gen = generate(ex, cfg=s.generate)
    findings = run_all(ex)
    matrix = build_matrix(ex, gen, findings["constraints_in_text_only"])
    cov = coverage(ex, gen, matrix)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    (s.out_dir / "traceability.csv").write_bytes(matrix_csv(matrix).encode("utf-8"))
    (s.out_dir / "coverage.html").write_bytes(coverage_html(ex, gen, matrix, cov, findings).encode("utf-8"))
    (s.out_dir / "findings.json").write_bytes(json.dumps(
        {k: [f.model_dump() for f in v] for k, v in findings.items()}, ensure_ascii=False, indent=2).encode("utf-8"))

    print(f"{len(matrix)} lignes dans out/traceability.csv")
    for kind, c in status_counts(matrix).items():
        print(f"  {kind:<24} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for r in cov:
        print(f"  {r.element:<28} {r.translated:>3}/{r.in_export:<3} {r.percent_label:>6}  {r.note}")
    for name, items in findings.items():
        print(f"  [{name}] {len(items)}")

    blocking = (not ex.report.ok
                or bool(findings["invalid_triggers"]) or bool(findings["invalid_grid_states"])
                or any(e.status == "not_mapped" for e in matrix))
    print("Tracabilite OK" if not blocking else "Tracabilite : a verifier")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
