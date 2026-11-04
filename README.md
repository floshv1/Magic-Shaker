# Projet 9 : UserShaker → SysML v2 → CATIA Magic

Transforme un export UserShaker (CSV, schéma 1.0) en modèle SysML v2 traçable, validé par le
parseur OMG, avec une interface Streamlit et un assistant LLM qui propose (l'humain valide).
Le contexte complet et les règles du projet sont dans [`CLAUDE.md`](CLAUDE.md).

## Démarrage

```sh
make install          # dépendances (uv) ; à relancer après un changement de pyproject.toml
make demo             # ingest + generate + trace, puis lance l'interface (http://localhost:8501)
```

L'interface est un parcours en trois étapes :

1. **Importer** : déposer le `.zip` de l'export, ou cliquer sur « Utiliser l'export de démonstration » ;
   le résumé de l'export et les contrôles s'affichent ;
2. **Choisir** : cocher les use cases, puis cliquer sur « Traduire » (rien n'est généré avant) ;
3. **Résultats** : bandeau de traçabilité, téléchargement du modèle, puis onglets Diagrammes,
   Fichiers .sysml (couverture, matrice, parseur OMG), Points à valider et Assistant.

Les diagrammes s'ouvrent dans une visionneuse (molette : zoom, glisser : déplacer, plein écran,
nouvel onglet, export SVG). Tout est embarqué, aucun accès internet n'est nécessaire pendant la démo :

- `app/assets/viz-global.js` : Viz.js 3.31 (licence MIT, contient Graphviz) ;
- `app/static/fonts/` : IBM Plex Sans et Mono (licence OFL, voir `OFL.txt`) ;
- `.streamlit/config.toml` : thème (couleurs, polices) ; `make demo` doit être lancé depuis la racine.

## Points à valider : décisions humaines

Les contrôles automatiques (`src/p9/checks/consistency.py`, règles affichées dans l'interface) signalent ce
que le modèle ne peut pas représenter tel quel. Pour chaque point, une personne décide dans l'onglet
« Points à valider » : ajouter la garde manquante (composant + état, ou condition en texte), créer un
requirement, rattacher l'insight à un user need existant, renvoyer la correction à UserShaker, ou écarter
le point avec sa raison. Une proposition du LLM peut être reprise puis modifiée avant d'être enregistrée.

- Les décisions sont signées (nom, date) et enregistrées dans `review/decisions.json`, **à commiter** :
  l'équipe les partage et `make generate` produit le même modèle partout.
- Elles sont générées par template dans `ESpaceConceptReview.sysml` (`@ReviewDecision`, `origin = "human"`),
  séparé du contenu UserShaker, qui n'est jamais modifié.
- La traçabilité suit : une contrainte en texte libre traitée par une garde passe de `partial` à `mapped`
  (avec l'auteur), un point écarté garde sa raison ; `out/review.csv` liste toutes les décisions.
- Un point est reconnu d'un export à l'autre par un id stable ; une décision dont le point a disparu est
  signalée, pas supprimée.

## Commandes

| Commande | Effet |
| --- | --- |
| `make ingest` | charge `data/usershaker/`, vérifie comptes vs `meta.csv`, clés étrangères, ordres, doublons |
| `make generate` | `out/sysml/ESpaceCommon.sysml` + un fichier par use case, `out/model.sysml` (tout en un), et `out/catia/` : un fichier autonome par use case pour CATIA Community (voir plus bas) |
| `make validate-setup` | installe le parseur OMG SysML v2 (kernel 0.63.1 + JDK 21 si absent) dans `~/.cache/p9-sysml` |
| `make validate` | passe les `.sysml` générés au parseur OMG (≈ 1 à 3 min) |
| `make trace` | `out/traceability.csv`, `out/coverage.html`, `out/findings.json`, et une matrice par fichier CATIA (`out/catia/<UC>.traceability.csv`) |
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

## Charger le modèle dans CATIA Magic SysML v2 Community Edition

L'édition gratuite (2026x) a des limites constatées en test :

- **20 000 caractères au plus par éditeur textuel** (un éditeur par espace de noms racine) : au-delà,
  « The Textual Editor has reached the character limit allowed in Community Mode ». `ESpaceCommon.sysml`
  (≈ 64 Ko) n'entre pas, et un use case qui l'importe ne se résout pas (erreurs `UserShakerRef` en cascade) ;
- **500 éléments majeurs** au plus par modèle ;
- **pas de « Import From »** dans le menu Fichier, même en mode Expert.

D'où `out/catia/` : **un fichier autonome par use case**, sous la limite (`[tool.p9.catia]` dans
`pyproject.toml`). Il ne contient que ce que le use case utilise : ses personas et composants, leurs
`state def` réduites aux états atteints dans ce use case (sous leur nom d'origine : `state 'Welcome pulse'`),
les transitions déduites de ce use case, les besoins servis et des vues CATIA prêtes à afficher. Les
`UserShakerRef` restent sur le use case, les phases, les steps, les personas, les composants et les besoins ;
états, transitions, événements et acteurs sont tracés par nom qualifié (un compartiment de métadonnée par
symbole encombrerait les diagrammes). Si le fichier dépasse le budget, des réductions sont appliquées dans
un ordre fixe (`TRIMS` dans `generate/catia.py`, de la moins coûteuse en information à la plus coûteuse) et
notées dans `out/catia/<UC>.traceability.csv`. Le modèle complet reste la référence.

Dans CATIA (un use case par projet) :

1. Nouveau projet, perspective *System Engineer SysML v2*.
2. Ouvrir l'éditeur textuel de l'espace de noms racine, y coller `out/catia/UC<n>_<Nom>.sysml`, puis **Alt+S**.
3. Pour chaque vue du package (`structureView`, `flowView`, `statesView`, `needsView`) : clic droit > **Open**,
   puis clic droit sur le fond > **Display > Display Exposed Elements** ; sur une boîte : **Display Features**
   (états) ou **Display Actions** (steps) ; **Ctrl+A** puis **Display > Display Relations** ; **Ctrl+Q** pour la
   mise en page.

Les vues sont typées par la bibliothèque `DS_Views` de CATIA (`gv`, `stv`, `afv`). Pour la validation OMG,
`make validate` charge d'abord un remplaçant minimal (`tools/sysml/DS_Views.sysml`), à ne pas coller dans CATIA.

## Parseur OMG sous Windows

Le kernel 0.63 demande Java 21, et sa bibliothèque standard est rangée dans des dossiers à espaces que
`loadLibrary` décode mal sous Windows. `make validate-setup` en fait une copie sans espaces et la charge
via `tools/sysml/P9Validate.java`. Le dossier de travail (`P9_SYSML_HOME`) ne doit pas contenir d'espace.

- Si le `java` du PATH est plus ancien que 21 alors qu'un JDK 21 est installé : `set P9_JAVA_HOME=<dossier du JDK 21>`.
- Si le téléchargement échoue sur un certificat SSL (`CERTIFICATE_VERIFY_FAILED`), télécharger le zip du
  kernel (`kernel_url` dans `pyproject.toml`) avec `curl -L` dans `~/.cache/p9-sysml/dl/`, puis relancer
  `make validate-setup` : un fichier déjà présent n'est pas re-téléchargé.

## OneDrive

Le repo est dans OneDrive, qui verrouille parfois les fichiers du `.venv` : `make install` peut échouer
avec « Accès refusé » ; relancer suffit. Pour l'éviter, placer l'environnement hors de OneDrive :
`set UV_PROJECT_ENVIRONMENT=C:\venvs\p9` avant `make install`.
