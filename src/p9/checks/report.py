"""Rapport HTML de couverture (out/coverage.html) : autonome, sans dépendance."""

from __future__ import annotations

from html import escape

from p9.generate import Generated, TraceEntry
from p9.ingest import Export

from .consistency import Finding
from .traceability import CoverageRow, status_counts

CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:1100px;color:#1d1d1f;padding:0 1rem}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0}th,td{border-bottom:1px solid #eee;padding:.35rem .5rem;
text-align:left;vertical-align:top}th{background:#f6f6f8}td.n{text-align:right;font-variant-numeric:tabular-nums}
.mapped{color:#1a7f37}.partial{color:#9a6700}.not_mapped{color:#cf222e}.muted{color:#6e6e73}
.badge{display:inline-block;padding:0 .5rem;border-radius:9px;background:#f0f0f3;font-size:12px}
code{font-size:12px}
"""


def _table(headers: list[str], rows: list[list[str]], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td class='n'>{c}</td>" if i in numeric else f"<td>{c}</td>" for i, c in enumerate(r)) + "</tr>"
        for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def coverage_html(ex: Export, gen: Generated, matrix: list[TraceEntry], cov: list[CoverageRow],
                  findings: dict[str, list[Finding]]) -> str:
    m = ex.meta
    parts = [f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><title>Couverture - {escape(m.project_name or '')}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>Couverture UserShaker &rarr; SysML v2 : {escape(m.project_name or '')}</h1>",
             f"<p class='muted'>Export du {escape(m.exported_at or '?')}, schema {escape(m.schema_version or '?')}. "
             f"Use cases generes : {len(gen.selected)} / {len(ex.use_cases)}. Empreinte des fichiers : <code>{gen.digest()[:16]}</code></p>"]

    parts.append("<h2>Tableau de couverture</h2>")
    parts.append(_table(["Element", "Dans l'export", "Traduit", "Partiel", "Couverture", "Note"],
                        [[escape(r.element), str(r.in_export), str(r.translated), str(r.partial),
                          escape(r.percent_label), escape(r.note)] for r in cov], numeric={1, 2, 3, 4}))

    counts = status_counts(matrix)
    parts.append("<h2>Matrice de tracabilite : statuts par type</h2>")
    parts.append(_table(["Type", "mapped", "partial", "not_mapped"],
                        [[escape(k), str(v.get("mapped", 0)), str(v.get("partial", 0)), str(v.get("not_mapped", 0))]
                         for k, v in counts.items()], numeric={1, 2, 3}))

    issues = [e for e in matrix if e.status != "mapped"]
    parts.append(f"<h2>Elements partiels ou non traduits ({len(issues)})</h2>")
    parts.append(_table(["Id UserShaker", "Type", "Statut", "Raison", "Element SysML"],
                        [[f"<code>{escape(e.usershaker_id)}</code>", escape(e.kind),
                          f"<span class='{e.status}'>{e.status}</span>", escape(e.reason),
                          f"<code>{escape(e.sysml_qualified_name)}</code>"] for e in issues]))

    parts.append("<h2>Controles de coherence</h2>")
    for name, items in findings.items():
        parts.append(f"<h3>{escape(name)} <span class='badge'>{len(items)}</span></h3>")
        if items:
            parts.append("<ul>" + "".join(
                f"<li><b class='{'not_mapped' if f.severity == 'error' else 'partial' if f.severity == 'warning' else 'muted'}'>"
                f"{f.severity}</b> {escape(f.message)} <span class='muted'>{escape(', '.join(f.ids))}</span></li>"
                for f in items) + "</ul>")
        else:
            parts.append("<p class='mapped'>Aucun probleme.</p>")
    parts.append("</body></html>\n")
    return "".join(parts)
