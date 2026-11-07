"""`python -m p9.llm.evaluate` : mesure l'agent de chat sur des questions dont on connaît la réponse.

Les questions sont dans un fichier JSON versionné (`llm_eval_file` dans pyproject.toml), propre au
dataset (ids attendus) ; ce module est générique. Le score est automatique, sans LLM juge :
  - must_cite    : ids UserShaker que la réponse doit citer ;
  - must_mention : pour chaque entrée, au moins une des variantes doit apparaître (accents et casse ignorés) ;
  - tools_any    : au moins un de ces outils doit avoir été appelé ;
  - language     : langue attendue de la réponse ("fr" par défaut, "" pour ne pas vérifier) ;
  - et aucun id inexistant cité (réponse ancrée dans l'export).
Une question passe si tous ses critères sont remplis. Résultat : out/llm_eval.json + résumé console.
Chaque question appelle le LLM configuré (coût si l'API est payante).

Comparer deux modèles : relancer avec P9_LLM_PROVIDER / P9_OLLAMA_MODEL / P9_LLM_MODEL / P9_LLM_EFFORT.
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .agent import AgentAnswer, ask
from .providers import LlmUnavailable
from .tools import ToolContext


class EvalQuestion(BaseModel):
    id: str
    question: str
    must_cite: list[str] = Field(default_factory=list)
    must_mention: list[list[str]] = Field(default_factory=list)
    tools_any: list[str] = Field(default_factory=list)
    language: str = "fr"
    note: str = ""


# Mots outils fréquents, comptés hors des segments cités (noms d'éléments en anglais entre guillemets,
# en gras ou entre backticks) : une réponse française en contient plus que de mots anglais.
_FR_WORDS = frozenset("le la les des de du au aux une et en est sont dans pas aucun aucune avec pour sur par "
                      "cette ce ces qui que il ou".split())
_EN_WORDS = frozenset("the is are of and not no with for this that which there was were".split())
_QUOTED = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"]*\"|`[^`]*`|\*[^*\n]*\*")


def looks_like(text: str, language: str) -> bool:
    words = normalize(_QUOTED.sub(" ", text)).replace("'", " ").split()
    fr = sum(w in _FR_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    return (fr >= 3 or fr >= en) if language == "fr" else en > fr


class EvalResult(BaseModel):
    question_id: str
    passed: bool
    score: float
    criteria: dict[str, float] = Field(default_factory=dict)   # critère → part remplie (0 à 1)
    missing_ids: list[str] = Field(default_factory=list)
    missing_mentions: list[list[str]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    seconds: float = 0.0
    answer: str = ""
    error: str = ""


def normalize(text: str) -> str:
    """Minuscules sans accents, espaces insécables remplacés : 'Ratio normal' == 'ratio normal'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.replace("’", "'").lower().split())


def load_questions(path: Path) -> list[EvalQuestion]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalQuestion.model_validate(q) for q in data["questions"]]


def score_answer(q: EvalQuestion, answer: AgentAnswer, seconds: float = 0.0) -> EvalResult:
    text = normalize(answer.text)
    tools = [c["name"] for c in answer.tool_calls]
    criteria: dict[str, float] = {}
    missing_ids = [i for i in q.must_cite if i not in answer.text]
    if q.must_cite:
        criteria["cite"] = 1 - len(missing_ids) / len(q.must_cite)
    missing_mentions = [alts for alts in q.must_mention if not any(normalize(a) in text for a in alts)]
    if q.must_mention:
        criteria["mention"] = 1 - len(missing_mentions) / len(q.must_mention)
    if q.tools_any:
        criteria["tools"] = 1.0 if set(q.tools_any) & set(tools) else 0.0
    if q.language:
        criteria["language"] = 1.0 if looks_like(answer.text, q.language) else 0.0
    criteria["grounded"] = 0.0 if answer.unknown_ids else 1.0
    return EvalResult(
        question_id=q.id, passed=all(v == 1.0 for v in criteria.values()),
        score=round(sum(criteria.values()) / len(criteria), 3), criteria=criteria,
        missing_ids=missing_ids, missing_mentions=missing_mentions, tools=tools,
        unknown_ids=answer.unknown_ids, seconds=round(seconds, 1), answer=answer.text,
    )


def run(provider: Any, ctx: ToolContext, questions: list[EvalQuestion],
        on_result: Callable[[EvalResult], None] | None = None) -> list[EvalResult]:
    """Chaque question part d'une conversation vide. Une erreur LLM compte 0 sans arrêter la série."""
    results = []
    for q in questions:
        t = time.monotonic()
        try:
            res = score_answer(q, ask(provider, ctx, q.question), time.monotonic() - t)
        except LlmUnavailable as e:
            res = EvalResult(question_id=q.id, passed=False, score=0.0, seconds=round(time.monotonic() - t, 1),
                             error=str(e))
        results.append(res)
        if on_result is not None:
            on_result(res)
    return results


def summary(results: list[EvalResult]) -> dict[str, Any]:
    n = len(results)
    return {"questions": n, "passed": sum(r.passed for r in results),
            "mean_score": round(sum(r.score for r in results) / n, 3) if n else 0.0,
            "seconds": round(sum(r.seconds for r in results), 1)}


def _line(r: EvalResult) -> str:
    if r.error:
        return f"  KO  {r.question_id:<28} erreur : {r.error}"
    miss = []
    if r.missing_ids:
        miss.append(f"ids manquants {len(r.missing_ids)}")
    if r.missing_mentions:
        miss.append("mots manquants " + " / ".join(a[0] for a in r.missing_mentions))
    if r.criteria.get("tools") == 0.0:
        miss.append("outil attendu non appele")
    if r.criteria.get("language") == 0.0:
        miss.append("mauvaise langue")
    if r.unknown_ids:
        miss.append(f"ids inexistants {r.unknown_ids}")
    return (f"  {'OK' if r.passed else 'KO'}  {r.question_id:<28} {r.score:>5.2f}  {r.seconds:>5.1f} s  "
            f"outils {r.tools}" + (f"  -> {'; '.join(miss)}" if miss else ""))


def main(argv: list[str]) -> int:
    from p9.config import load_settings
    from p9.ingest import ExportUnreadable, load_path

    from .providers import make_provider, provider_status

    s = load_settings()
    path = Path(argv[0]) if argv else s.llm_eval_file
    if path is None or not path.is_file():
        print(f"Fichier de questions introuvable : {path}")
        return 2
    ok, label = provider_status(s.llm)
    if not ok:
        print(f"LLM indisponible : {label}")
        return 2
    try:
        ex = load_path(s.data_dir)
    except ExportUnreadable as e:
        print(f"Erreur : {e}")
        return 2
    questions = load_questions(path)
    print(f"{len(questions)} questions, {label}")
    results = run(make_provider(s.llm), ToolContext(ex), questions, on_result=lambda r: print(_line(r), flush=True))
    tot = summary(results)
    model = s.llm.anthropic_model if s.llm.provider == "anthropic" else s.llm.ollama_model
    report = {"provider": s.llm.provider, "model": model, "effort": s.llm.effort, **tot,
              "results": [r.model_dump() for r in results]}
    s.out_dir.mkdir(parents=True, exist_ok=True)
    (s.out_dir / "llm_eval.json").write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"{tot['passed']}/{tot['questions']} questions reussies, score moyen {tot['mean_score']:.2f}, "
          f"{tot['seconds']:.0f} s ; detail dans out/llm_eval.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
