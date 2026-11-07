"""Identifiants SysML : camelCase / PascalCase ASCII, sans mot-clé réservé, uniques par portée."""

from __future__ import annotations

import re
import unicodedata

# Mots réservés SysML v2 / KerML (notation textuelle). Un identifiant égal à l'un d'eux
# reçoit un suffixe '_' : on évite ainsi les noms entre guillemets.
RESERVED = frozenset("""
about abstract accept action actor after alias all allocate allocation analysis and as assert assign
assoc attribute bind binding by calc case chains class classifier comment composite concern connect
connection connector const constant constraint crosses datatype decide def default defined dependency
derived differences disjoining disjoint do doc else end entry enum event exhibit exit expose expr false
feature featured featuring filter first flow for fork frame from function hastype if implies import in
include individual inout interaction interface intersects inv istype item join language library locale
loop member merge message meta metaclass metadata multiplicity namespace new nonunique not null objective
occurrence of or ordered out package parallel part perform port portion predicate private protected
public redefines redefinition ref references render rendering rep require requirement return satisfy
send snapshot specialization specializes stakeholder standard state step struct subclassifier subject
subset subsets subtype succession terminate then timeslice to transition true type typed typing unions
until use var variant variation verification verify via view viewpoint when while xor
start done self this that
""".split())


def ident(text: str, upper: bool = False) -> str:
    """'Léa starts a game' → 'leaStartsAGame' ; upper=True → 'LeaStartsAGame'."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", ascii_text) or ["x"]
    # Les mots tout en majuscules (sigles) gardent leur casse interne ; les autres sont capitalisés.
    def cap(w: str) -> str:
        return w if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:].lower()
    head = cap(words[0]) if upper else words[0].lower()
    name = head + "".join(cap(w) for w in words[1:])
    if name[0].isdigit():
        name = "_" + name
    if name in RESERVED:
        name += "_"
    return name


class Namer:
    """Attribue des noms uniques et stables dans une portée (suffixe 2, 3… en cas de collision).

    Le résultat ne dépend que de l'ordre des appels : le générateur appelle toujours dans
    un ordre trié, donc les noms sont déterministes.
    """

    def __init__(self, reserved: set[str] | None = None) -> None:
        self._used: set[str] = set(reserved or ())

    def take(self, base: str) -> str:
        name, k = base, 2
        while name in self._used:
            name, k = f"{base}{k}", k + 1
        self._used.add(name)
        return name

    def name(self, text: str, upper: bool = False, suffix: str = "") -> str:
        return self.take(ident(text, upper) + suffix)


def pascal(text: str) -> str:
    return ident(text, upper=True)


def camel(text: str) -> str:
    return ident(text)


def lower_first(name: str) -> str:
    return name[:1].lower() + name[1:]
