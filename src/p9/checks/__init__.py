"""p9.checks : traçabilité, couverture et contrôles de cohérence."""

from .consistency import ALL_CHECKS, Finding, run_all
from .report import coverage_html
from .traceability import COLUMNS, CoverageRow, build_matrix, coverage, matrix_csv, status_counts

__all__ = [
    "ALL_CHECKS", "COLUMNS", "CoverageRow", "Finding", "build_matrix", "coverage", "coverage_html",
    "matrix_csv", "run_all", "status_counts",
]
