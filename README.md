# Projet 9 : UserShaker → SysML v2 → CATIA Magic

Transforme un export UserShaker (CSV, schéma 1.0) en modèle SysML v2 traçable, validé par le
parseur OMG, avec une interface Streamlit et un assistant LLM qui propose (l'humain valide).
Le contexte complet et les règles du projet sont dans [`CLAUDE.md`](CLAUDE.md).

## Démarrage

```sh
make install          # dépendances (uv) ; à relancer après un changement de pyproject.toml
make demo             # ingest + generate + trace, puis lance l'interface (http://localhost:8501)
```

Dans l'interface : déposer le `.zip` de l'export, ou activer « Utiliser l'export du dépôt ».

## Commandes

| Commande | Effet |
| --- | --- |
| `make ingest` | charge `data/usershaker/`, vérifie comptes vs `meta.csv`, clés étrangères, ordres, doublons |
| `make generate` | `out/sysml/ESpaceCommon.sysml` + un fichier par use case, et `out/model.sysml` (tout en un) |
| `make validate-setup` | installe le parseur OMG SysML v2 (kernel 0.63.1 + JDK 21 si absent) dans `~/.cache/p9-sysml` |
| `make validate` | passe les `.sysml` générés au parseur OMG (≈ 1 à 3 min) |
| `make trace` | `out/traceability.csv`, `out/coverage.html`, `out/findings.json` |
| `make test` | tests rapides (pytest) |
| `make test-omg` | tests qui lancent le parseur OMG (lents) |
| `make check` | `generate validate trace test` : à lancer après toute modification du générateur |

## Architecture

```
data/usershaker/  export d'origine (lecture seule)
src/p9/ingest/    CSV → modèles pydantic + rapport de contrôles (aucune exception sur un fichier mal formé)
src/p9/model/     graphe pivot networkx (requêtes de l'agent)
src/p9/generate/  mapping (build.py) + templates Jinja2 → .sysml déterministes, UserShakerRef partout
src/p9/checks/    traçabilité complète, couverture, contrôles de cohérence, parseur OMG
src/p9/llm/       outils + agent de chat (tool calling) + enrichissement (propositions à valider)
app/app.py        interface Streamlit (aucune logique métier)
tools/sysml/      lanceur Java du parseur OMG
```

Choix notables (détails dans les docstrings) :

- **Fichier commun + un fichier par use case** : `ESpaceCommon` porte `UserShakerRef`, personas,
  composants, `state def`, événements et `requirement def` ; chaque use case l'importe.
- **Phases = actions composites** : `use case def` → `action <phase>Phase` → `action <step>`,
  enchaînées par `first start; then …; then done;`.
- **Transitions déduites de la grille du blueprint** : un changement d'état au step k devient une
  `transition` avec `accept` (événement du step) et `if` (ses conditions). Règle à valider par l'expert.
- **Rien n'est perdu en silence** : la matrice part de l'inventaire de toutes les lignes de l'export ;
  ce qui n'est pas traduit est `not_mapped` / `partial` avec une raison, y compris les contraintes
  présentes seulement en texte libre (`text_constraint`).
- **Diagrammes d'aperçu** (`generate/diagrams.py`, section 4 de l'interface) : structure, use cases,
  activités et machines à états, dessinés en Graphviz depuis les mêmes contextes que les `.sysml`
  (mêmes noms, id UserShaker au survol). Aperçu seulement : la mise en page réelle se fait dans CATIA.
- **LLM** : Claude (`claude-opus-5-5`, repli serveur en cas de refus activé) ou Ollama local. Il n'écrit
  jamais de SysML ; les propositions acceptées sont rendues par template dans
  `ESpaceConceptLlmProposals.sysml`, marquées `origin = "llm"`, hors du modèle d'origine.

## Parseur OMG sous Windows

Le kernel 0.63 demande Java 21, et sa bibliothèque standard est rangée dans des dossiers à espaces que
`loadLibrary` décode mal sous Windows. `make validate-setup` en fait une copie sans espaces et la charge
via `tools/sysml/P9Validate.java`. Le dossier de travail (`P9_SYSML_HOME`) ne doit pas contenir d'espace.

## OneDrive

Le repo est dans OneDrive, qui verrouille parfois les fichiers du `.venv` : `make install` peut échouer
avec « Accès refusé » ; relancer suffit. Pour l'éviter, placer l'environnement hors de OneDrive :
`set UV_PROJECT_ENVIRONMENT=C:\venvs\p9` avant `make install`.
