"""`python -m p9.checks` : out/traceability.csv, out/coverage.html, out/findings.json,
et une matrice par fichier CATIA (out/catia/<UC>.traceability.csv).

Code retour 1 si un contrôle bloquant échoue (comptes, clés étrangères, triggers invalides,
élément non traduit d'un use case généré).
"""

from __future__ import annotations

import json
import sys

from p9.config import load_settings
from p9.generate import generate, generate_catia
from p9.ingest import ExportUnreadable, load_path

from . import ADDED_KINDS, build_matrix, coverage, coverage_html, matrix_csv, run_all, status_counts


def main(argv: list[str]) -> int:
    s = load_settings()
    try:
        ex = load_path(argv[0] if argv else s.data_dir)
    except ExportUnreadable as e:
        print(f"Erreur : {e}")
        return 2
    from p9.llm.enrich import ProposalFileError, load_store, store_path
    from p9.review import ReviewFileError, load_review, review_csv
    try:
        review = load_review(s.review_file)
        accepted = load_store(store_path(s.proposals_file, s.out_dir)).accepted()
    except (ProposalFileError, ReviewFileError) as e:
        print(f"Erreur : {e}")
        return 2
    gen = generate(ex, cfg=s.generate, accepted_proposals=accepted, review_decisions=review.decisions)
    findings = run_all(ex)
    matrix = build_matrix(ex, gen, findings["constraints_in_text_only"], review.decisions)
    cov = coverage(ex, gen, matrix)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    (s.out_dir / "traceability.csv").write_bytes(matrix_csv(matrix).encode("utf-8"))
    (s.out_dir / "coverage.html").write_bytes(coverage_html(ex, gen, matrix, cov, findings).encode("utf-8"))
    (s.out_dir / "findings.json").write_bytes(json.dumps(
        {k: [f.model_dump() for f in v] for k, v in findings.items()}, ensure_ascii=False, indent=2).encode("utf-8"))

    (s.out_dir / "review.csv").write_bytes(review_csv(review).encode("utf-8"))

    print(f"{len(matrix)} lignes dans out/traceability.csv, {len(review.decisions)} decision(s) dans out/review.csv")
    for kind, c in status_counts(matrix).items():
        print(f"  {kind:<24} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for r in cov:
        print(f"  {r.element:<28} {r.translated:>3}/{r.in_export:<3} {r.percent_label:>6}  {r.note}")
    for name, items in findings.items():
        print(f"  [{name}] {len(items)}")

    # Fichiers CATIA : ce qui n'y est pas (hors du use case, réductions) est not_mapped / partial
    # avec sa raison. Information, pas blocage : la référence reste le modèle complet ci-dessus.
    catia_dir = s.out_dir / "catia"
    catia_dir.mkdir(parents=True, exist_ok=True)
    for f in generate_catia(ex, cfg=s.generate, catia=s.catia):
        cmatrix = build_matrix(ex, f.gen, findings["constraints_in_text_only"])
        name = f.name.removesuffix(".sysml") + ".traceability.csv"
        (catia_dir / name).write_bytes(matrix_csv(cmatrix).encode("utf-8"))
        rows = [r for r in coverage(ex, f.gen, cmatrix) if r.in_export]
        print(f"  out/catia/{name} : " + ", ".join(f"{r.element} {r.translated}+{r.partial}p/{r.in_export}"
                                                   for r in rows))

    blocking = (not ex.report.ok
                or bool(findings["invalid_triggers"]) or bool(findings["invalid_grid_states"])
                or any(e.status == "not_mapped" and e.kind not in ADDED_KINDS for e in matrix))
    print("Tracabilite OK" if not blocking else "Tracabilite : a verifier")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
