"""`python -m p9.generate` : export → out/sysml/*.sysml + out/model.sysml + out/catia/*.sysml."""

from __future__ import annotations

import sys

from p9.config import load_settings
from p9.ingest import ExportUnreadable, load_path

from . import generate, generate_catia, write, write_catia


def main(argv: list[str]) -> int:
    s = load_settings()
    try:
        ex = load_path(argv[0] if argv else s.data_dir)
    except ExportUnreadable as e:
        print(f"Erreur : {e}")
        return 2
    if ex.report.missing_required:
        print(f"Feuilles obligatoires manquantes : {ex.report.missing_required}")
        return 2
    # Propositions LLM acceptées par un humain (out/proposals.json), rendues dans un package séparé.
    from p9.llm.enrich import load_store
    accepted = load_store(s.out_dir / "proposals.json").accepted()
    # Décisions humaines sur les points à valider (review/decisions.json, versionné).
    from p9.review import load_review
    decisions = load_review(s.review_file).decisions
    gen = generate(ex, cfg=s.generate, accepted_proposals=accepted, review_decisions=decisions)
    if accepted:
        print(f"{len(accepted)} proposition(s) LLM acceptee(s) incluse(s) (package separe, origin=llm)")
    if decisions:
        print(f"{len(decisions)} decision(s) humaine(s) prise(s) en compte (package separe, origin=human)")
    for p in write(gen, s.out_dir):
        print(f"ecrit {p.relative_to(s.root)}")
    print(f"{len(gen.trace)} lignes de tracabilite, sha256 {gen.digest()[:16]}")

    # Sortie CATIA Community : un fichier autonome par use case, sous la limite de l'éditeur textuel.
    catia = generate_catia(ex, cfg=s.generate, catia=s.catia)
    write_catia(catia, s.out_dir)
    over = []
    for f in catia:
        flag = "" if f.chars <= s.catia.char_limit else "  DEPASSE LA LIMITE CATIA"
        print(f"ecrit out/catia/{f.name} : {f.chars}/{s.catia.char_limit} caracteres, ~{f.declarations} "
              f"declarations{flag}" + (f" (reductions : {', '.join(f.trims)})" if f.trims else ""))
        if flag:
            over.append(f.name)
    if over:
        print(f"Attention : {len(over)} fichier(s) CATIA trop long(s) meme apres reductions : {over}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
