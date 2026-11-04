"""Contrôles de cohérence sur l'export (fonctions pures, sans LLM).

Chaque contrôle renvoie une liste de `Finding` qui cite les ids UserShaker concernés.
Les heuristiques sur le texte libre sont volontairement simples et explicables : elles
signalent des points à valider, elles ne corrigent rien.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from p9.ingest import Export

Severity = Literal["error", "warning", "info"]

STOPWORDS = frozenset("""
a an the and or of to in on at by for from with without into onto is are be been being was were it its this that
these those as so if only once when while than then there their his her he she they them we you i my our your
all any each every no not one two own same other more less very can could should would will just also up down out
le la les un une des et ou de du en au aux est sont ne pas que qui dans sur par pour avec sans son sa ses leur
""".split())

# Marqueurs d'enchaînement temporel entre deux scènes (« after the charging stop »).
TEMPORAL_MARKERS = frozenset("after before since during earlier later previous following back apres avant depuis pendant".split())

# Rôles d'occupant d'un véhicule (vocabulaire du domaine, pas du dataset).
SEAT_ROLES = ["front passenger", "rear passenger", "second row", "third row", "back seat", "driver's seat",
              "driver", "co-driver"]


class Finding(BaseModel):
    check: str
    severity: Severity = "warning"
    message: str
    ids: list[str] = Field(default_factory=list)
    use_case_id: str | None = None
    step_id: str | None = None
    detail: dict[str, str] = Field(default_factory=dict)


def finding_id(f: Finding) -> str:
    """Id stable d'un point : même contrôle, mêmes ids UserShaker, même phrase → même id (sert aux décisions)."""
    key = [f.check, sorted(f.ids), f.detail.get("sentence", ""), f.detail.get("index", ""), f.detail.get("role", "")]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------- outils texte

def tokens(text: str | None) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9À-ÿ]+", (text or "").lower())
    return [_stem(w) for w in words if w not in STOPWORDS]


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def sentences(text: str | None) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def refers_to(sentence: str, name_words: set[str], window: int = 5) -> bool:
    """Vrai si un marqueur temporel est suivi, à moins de `window` mots, d'au moins deux mots
    du nom d'un autre use case (« after the charging stop » → un use case nommé '... stop ... charging')."""
    words = re.findall(r"[a-zA-Z0-9À-ÿ]+", sentence.lower())
    for i, w in enumerate(words):
        if w in TEMPORAL_MARKERS:
            following = {_stem(x) for x in words[i + 1:i + 1 + window] if x not in STOPWORDS}
            if len(following & name_words) >= 2:
                return True
    return False


def first_name(name: str) -> str:
    return name.split()[0] if name.split() else name


# --------------------------------------------------------------------- contrôles structurels

def uncovered_needs(ex: Export) -> list[Finding]:
    linked = {r.user_need_id for r in ex.step_user_needs}
    return [Finding(check="uncovered_needs", message=f"User need '{u.name}' n'est servi par aucun step.",
                    ids=[u.user_need_id])
            for u in sorted(ex.user_needs, key=lambda u: u.user_need_id) if u.user_need_id not in linked]


def unreachable_states(ex: Export) -> list[Finding]:
    """États jamais atteints : absents de la grille du blueprint et de tout trigger."""
    used = {r.state_id for r in ex.step_component_states} | {t.state_id for t in ex.step_triggers}
    comps = ex.by_id("components")
    out = []
    for s in sorted(ex.component_states, key=lambda s: (s.component_id, s.position)):
        if s.state_id not in used:
            c = comps.get(s.component_id)
            out.append(Finding(check="unreachable_states", severity="info",
                               message=f"Etat '{s.name}' de '{c.name if c else s.component_id}' jamais atteint dans les parcours.",
                               ids=[s.state_id, s.component_id]))
    return out


def invalid_triggers(ex: Export) -> list[Finding]:
    """Un trigger doit viser un état du composant qu'il observe."""
    states = ex.by_id("component_states")
    out = []
    for t in sorted(ex.step_triggers, key=lambda t: (t.step_id, t.position)):
        st = states.get(t.state_id)
        if st is None:
            out.append(Finding(check="invalid_triggers", severity="error", message="Trigger vers un etat inconnu.",
                               ids=[t.trigger_id, t.state_id], step_id=t.step_id))
        elif st.component_id != t.component_id:
            out.append(Finding(check="invalid_triggers", severity="error",
                               message=f"Trigger : l'etat '{st.name}' n'appartient pas au composant observe.",
                               ids=[t.trigger_id, t.component_id, t.state_id], step_id=t.step_id))
    # premier rang = trigger, suivants = condition
    by_step: dict[str, list] = defaultdict(list)
    for t in ex.step_triggers:
        by_step[t.step_id].append(t)
    for sid, rows in sorted(by_step.items()):
        rows.sort(key=lambda t: t.position)
        kinds = [t.kind for t in rows]
        if kinds[0] != "trigger" or "trigger" in kinds[1:]:
            out.append(Finding(check="invalid_triggers", severity="error",
                               message=f"Ordre trigger/condition inattendu : {kinds}.",
                               ids=[t.trigger_id for t in rows], step_id=sid))
    return out


def invalid_grid_states(ex: Export) -> list[Finding]:
    states = ex.by_id("component_states")
    out = []
    for r in sorted(ex.step_component_states, key=lambda r: (r.step_id, r.component_id, r.sequence)):
        st = states.get(r.state_id)
        if st is not None and st.component_id != r.component_id:
            out.append(Finding(check="invalid_grid_states", severity="error",
                               message=f"Grille : l'etat '{st.name}' n'appartient pas au composant.",
                               ids=[r.step_id, r.component_id, r.state_id], step_id=r.step_id))
    return out


# --------------------------------------------------------------------- texte libre

def constraints_in_text_only(ex: Export) -> list[Finding]:
    """Phrases de `trigger_text` sans équivalent dans step_triggers.

    La 1re phrase décrit l'événement : elle est couverte s'il existe une ligne kind=trigger.
    Les suivantes doivent correspondre à une condition (un mot commun avec le nom du
    composant ou de l'état visé). Sinon la contrainte n'existe qu'en texte libre.
    """
    comps, states = ex.by_id("components"), ex.by_id("component_states")
    by_step: dict[str, list] = defaultdict(list)
    for t in ex.step_triggers:
        by_step[t.step_id].append(t)
    ucs = ex.by_id("use_cases")
    out = []
    for uc in ex.use_cases_sorted():
        for step in ex.steps_of(uc.use_case_id):
            rows = sorted(by_step.get(step.step_id, []), key=lambda t: t.position)
            has_event = any(t.kind == "trigger" for t in rows)
            conds = [t for t in rows if t.kind == "condition"]
            cond_words = []
            for t in conds:
                words = set(tokens(states[t.state_id].name if t.state_id in states else ""))
                words |= set(tokens(comps[t.component_id].name if t.component_id in comps else ""))
                cond_words.append(words)
            for i, sent in enumerate(sentences(step.trigger_text)):
                if i == 0 and has_event:
                    continue
                words = set(tokens(sent))
                if any(words & cw for cw in cond_words):
                    continue
                numeric = bool(re.search(r"\d", sent))
                out.append(Finding(
                    check="constraints_in_text_only",
                    message=(f"UC{ucs[uc.use_case_id].position} step {step.position} '{step.name}' : "
                             f"« {sent} » n'a pas de condition structuree equivalente"
                             + (" (seuil numerique)" if numeric else "") + "."),
                    ids=[step.step_id], use_case_id=uc.use_case_id, step_id=step.step_id,
                    detail={"sentence": sent, "index": str(i)},
                ))
    return out


def persona_inconsistencies(ex: Export) -> list[Finding]:
    """Incohérences de personas entre use cases.

    1. Persona nommé dans les textes d'un use case auquel il n'est pas rattaché.
    2. Persona rattaché à un use case mais présent à aucun step (feedback is_present).
    3. Conflit de rôle : une phrase donne au persona X un rôle d'occupant (« front passenger »…)
       que la biographie attribue au persona Y.
    4. Renvoi temporel d'un use case à un autre (« after the charging stop » : au moins deux mots du
       nom de l'autre use case + un marqueur temporel) avec des personas différents.
    """
    personas = sorted(ex.personas, key=lambda p: p.persona_id)
    linked: dict[str, set[str]] = defaultdict(set)
    for r in ex.use_case_personas:
        linked[r.use_case_id].add(r.persona_id)
    present: dict[str, set[str]] = defaultdict(set)
    step_uc = {s.step_id: s.use_case_id for s in ex.steps}
    for f in ex.persona_step_feedback:
        if f.is_present and f.step_id in step_uc:
            present[step_uc[f.step_id]].add(f.persona_id)

    def uc_texts(uc_id: str) -> list[tuple[str | None, str]]:
        uc = ex.by_id("use_cases")[uc_id]
        out = [(None, uc.story or ""), (None, uc.description or "")]
        for s in ex.steps_of(uc_id):
            out += [(s.step_id, s.description or ""), (s.step_id, s.trigger_text or "")]
        return out

    def label(uc_id: str) -> str:
        uc = ex.by_id("use_cases")[uc_id]
        return f"UC{uc.position} '{uc.name}'"

    def pname(pid: str) -> str:
        return first_name(ex.by_id("personas")[pid].name)

    out: list[Finding] = []
    for uc in ex.use_cases_sorted():
        uid = uc.use_case_id
        texts = uc_texts(uid)
        joined = " ".join(t for _, t in texts)
        for p in personas:
            fn = first_name(p.name)
            mentioned = re.search(rf"\b{re.escape(fn)}\b", joined) is not None
            if mentioned and p.persona_id not in linked[uid]:
                out.append(Finding(check="persona_inconsistencies",
                                   message=f"{label(uid)} : {fn} est cite dans le texte mais n'est pas un persona du use case.",
                                   ids=[uid, p.persona_id], use_case_id=uid))
            if p.persona_id in linked[uid] and p.persona_id not in present[uid]:
                out.append(Finding(check="persona_inconsistencies", severity="info",
                                   message=f"{label(uid)} : {fn} est rattache au use case mais present a aucun step.",
                                   ids=[uid, p.persona_id], use_case_id=uid))
        # 3. conflits de rôle
        for step_id, text in texts:
            for sent in sentences(text):
                low = sent.lower()
                for role in SEAT_ROLES:
                    if role not in low:
                        continue
                    owners = [q for q in personas if role in (q.biography or "").lower()]
                    for x in personas:
                        if not re.search(rf"\b{re.escape(first_name(x.name))}\b", sent):
                            continue
                        for y in owners:
                            if y.persona_id == x.persona_id or role in (x.biography or "").lower():
                                continue
                            other_ucs = sorted(u for u, ps in linked.items() if y.persona_id in ps and u != uid)
                            where = ", ".join(label(u) for u in other_ucs) or "aucun use case"
                            absent = "" if y.persona_id in linked[uid] else f" ; {pname(y.persona_id)} est absent de ce use case"
                            out.append(Finding(
                                check="persona_inconsistencies",
                                message=(f"{label(uid)} : « {sent} » donne a {pname(x.persona_id)} le role "
                                         f"'{role}', que la biographie attribue a {pname(y.persona_id)} "
                                         f"(present dans {where}){absent}."),
                                ids=[uid, x.persona_id, y.persona_id] + ([step_id] if step_id else []),
                                use_case_id=uid, step_id=step_id, detail={"role": role, "sentence": sent},
                            ))
                        break
        # 4. renvoi vers un autre use case
        for other in ex.use_cases_sorted():
            if other.use_case_id == uid:
                continue
            name_words = set(tokens(other.name))
            for step_id, text in texts:
                for sent in sentences(text):
                    if not refers_to(sent, name_words):
                        continue
                    a, b = linked[uid], linked[other.use_case_id]
                    only_here, only_there = sorted(a - b), sorted(b - a)
                    if not (only_here or only_there):
                        continue
                    diff = []
                    if only_here:
                        diff.append(f"{', '.join(pname(p) for p in only_here)} seulement ici")
                    if only_there:
                        diff.append(f"{', '.join(pname(p) for p in only_there)} seulement dans {label(other.use_case_id)}")
                    out.append(Finding(
                        check="persona_inconsistencies",
                        message=(f"{label(uid)} renvoie a {label(other.use_case_id)} (« {sent} ») "
                                 f"mais les personas different : {' ; '.join(diff)}."),
                        ids=[uid, other.use_case_id, *only_here, *only_there],
                        use_case_id=uid, step_id=step_id, detail={"sentence": sent},
                    ))
                    break
                else:
                    continue
                break
    return out


def implicit_needs(ex: Export) -> list[Finding]:
    """Insights négatifs (emotion_score < 0) : candidats requirements, non reliés à un user need.

    Le schéma ne relie pas un feedback à un user need : chaque insight négatif est donc un
    besoin implicite potentiel. On indique le user need existant le plus proche (mots communs).
    """
    steps, personas = ex.by_id("steps"), ex.by_id("personas")
    needs = sorted(ex.user_needs, key=lambda u: u.user_need_id)
    step_needs: dict[str, set[str]] = defaultdict(set)
    for r in ex.step_user_needs:
        step_needs[r.step_id].add(r.user_need_id)
    out = []
    for f in sorted(ex.persona_step_feedback, key=lambda f: (f.step_id, f.persona_id)):
        if f.emotion_score is None or f.emotion_score >= 0:
            continue
        text = f.insight or f.action or ""
        words = set(tokens(text))
        best, score = None, 0
        for u in needs:
            s = len(words & set(tokens(f"{u.name} {u.description or ''}")))
            if s > score:
                best, score = u, s
        step = steps.get(f.step_id)
        who = first_name(personas[f.persona_id].name) if f.persona_id in personas else f.persona_id
        hint = f" Besoin existant le plus proche : '{best.name}'." if best and score >= 2 else ""
        out.append(Finding(
            check="implicit_needs", severity="info",
            message=(f"Step '{step.name if step else f.step_id}' : {who} (emotion {f.emotion_score}) "
                     f"« {text} » n'est relie a aucun user need.{hint}"),
            ids=[f.feedback_id, f.step_id, f.persona_id], step_id=f.step_id,
            detail={"insight": text, "emotion_score": str(f.emotion_score),
                    "closest_need_id": best.user_need_id if best and score >= 2 else ""},
        ))
    return out


ALL_CHECKS = {
    "uncovered_needs": uncovered_needs,
    "unreachable_states": unreachable_states,
    "invalid_triggers": invalid_triggers,
    "invalid_grid_states": invalid_grid_states,
    "constraints_in_text_only": constraints_in_text_only,
    "persona_inconsistencies": persona_inconsistencies,
    "implicit_needs": implicit_needs,
}


# Règles appliquées, en langage clair, pour l'interface. À garder alignées sur le code ci-dessus
# (test_checks vérifie qu'une règle existe pour chaque contrôle).
CHECK_RULES: dict[str, list[str]] = {
    "constraints_in_text_only": [
        "Le trigger_text de chaque step est découpé en phrases.",
        "La 1re phrase décrit l'événement : elle est couverte si le step a une ligne step_triggers "
        "de kind = trigger.",
        "Chaque phrase suivante doit partager au moins un mot (hors mots vides) avec le nom du composant "
        "ou de l'état d'une ligne kind = condition du même step.",
        "Sinon, la phrase est signalée ; « seuil numérique » est ajouté si elle contient un chiffre.",
    ],
    "persona_inconsistencies": [
        "Textes lus : story et description du use case, description et trigger_text de ses steps.",
        "Persona cité par son prénom dans ces textes sans être rattaché au use case (use_case_personas).",
        "Persona rattaché au use case mais présent (is_present) à aucun de ses steps.",
        "Conflit de rôle : une phrase donne à X un rôle d'occupant (" + ", ".join(SEAT_ROLES) + ") que la "
        "biographie attribue à un autre persona Y.",
        "Renvoi à un autre use case (un marqueur temporel comme after ou before, suivi à moins de 5 mots "
        "d'au moins 2 mots du nom de ce use case) alors que les personas des deux use cases diffèrent.",
    ],
    "implicit_needs": [
        "Chaque persona_step_feedback dont emotion_score < 0 est retenu.",
        "Le schéma 1.0 ne relie jamais un feedback à un user need : tous les insights négatifs sont donc "
        "listés comme besoins implicites possibles.",
        "Le user need le plus proche est indiqué s'il partage au moins 2 mots avec l'insight.",
    ],
    "uncovered_needs": ["User need sans aucune ligne dans step_user_needs."],
    "unreachable_states": ["État de component_states absent de la grille du blueprint "
                           "(step_component_states) et de tout step_triggers."],
    "invalid_triggers": [
        "L'état visé par un trigger doit exister et appartenir au composant observé.",
        "Dans un step, la 1re ligne doit être kind = trigger, les suivantes kind = condition.",
    ],
    "invalid_grid_states": ["Dans la grille du blueprint, l'état doit appartenir au composant de sa ligne."],
}


def run_all(ex: Export) -> dict[str, list[Finding]]:
    return {name: fn(ex) for name, fn in ALL_CHECKS.items()}
