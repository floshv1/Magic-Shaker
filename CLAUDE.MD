# CLAUDE.md — Projet 9 : traducteur UX → SysML v2 → CATIA Magic

Contexte du projet pour Claude Code. À placer à la racine du repo. Ce fichier est lu automatiquement à chaque session.

## Le projet en une phrase

Hackathon de 48 h (Renault) : on transforme un export **UserShaker** (parcours utilisateur en CSV) en un **modèle SysML v2** cohérent et traçable, puis on l'importe et on le visualise dans **CATIA Magic SysML v2 Community Edition**. Bonus : un LLM permet de « discuter » avec le modèle pour trouver les besoins non couverts, les incohérences et les omissions.

Ce que le jury évalue : **aucune information perdue entre l'intention utilisateur et le modèle système, et une preuve qui le démontre.**

## Principes non négociables

1. **La génération SysML est déterministe.** Elle se fait en Python avec des templates Jinja2, jamais avec un LLM. Pour une même entrée, on doit obtenir un fichier `.sysml` identique octet par octet.
2. **Chaque élément généré porte son id UserShaker** via la métadonnée `UserShakerRef`. Les noms ne servent jamais d'identifiant : ils changent et ne sont pas uniques.
3. **Le LLM propose, l'humain valide.** Tout ce que le LLM déduit est marqué `origin = "llm"` et `status = "to_validate"`. Rien n'entre dans le modèle principal sans validation. On ne mélange jamais le contenu déduit avec le contenu d'origine UserShaker.
4. **On ne perd rien en silence.** Quand une donnée ne peut pas être traduite, elle apparaît dans le rapport de traçabilité. On ne l'ignore jamais sans le signaler.
5. **`main` doit toujours tourner** : `make demo` passe et chaque `.sysml` généré passe le parseur OMG.

## Stack

- Python 3.11+, géré avec `uv` (ou `pip`)
- `pandas` + `pydantic` v2 : ingestion et validation
- `networkx` : graphe pivot
- `jinja2` : génération de la notation textuelle SysML v2
- `pytest` : tests
- OMG SysML v2 Pilot Implementation (kernel Jupyter) : validation de la syntaxe
- `sysml_v2_api_client` + SysML v2 API Services (Docker + PostgreSQL) : push et relecture du modèle (plan B / test aller-retour)
- LLM en tool calling (API cloud ou Ollama en local) : enrichissement + chat
- `streamlit` : interface de démo

## Arborescence cible

```
data/usershaker/        # export CSV d'origine, en lecture seule, ne jamais modifier
src/p9/
  ingest/               # chargement CSV → modèles pydantic, vérifications
  model/                # graphe pivot networkx
  generate/             # templates Jinja2 + rendu .sysml
    templates/
  checks/               # traçabilité et contrôles de cohérence
  llm/                  # enrichissement + agent de chat (tools)
  api/                  # push/relecture via l'API SysML v2
app/                    # Streamlit (app.py)
prototype/              # première version (app.py + core.py), comme référence ; ne pas l'étendre
out/                    # fichiers générés (.sysml, rapports), ignoré par git
tests/
Makefile
```

## Commandes

```
make install    # dépendances
make ingest     # charge et vérifie les CSV
make generate   # produit out/model.sysml
make validate   # passe out/*.sysml au parseur OMG
make trace      # produit out/traceability.csv et out/coverage.html
make test       # pytest
make demo       # tout le pipeline + lance Streamlit
```

Si une commande n'existe pas encore, crée la cible correspondante dans le Makefile plutôt que d'improviser un autre point d'entrée.

## Données d'entrée : export UserShaker (schéma 1.0)

Le projet exporté est « Renault E-Space concept » (ESILV). Comptes attendus, à vérifier à chaque chargement avec `meta.csv` :

| Feuille | Nb | Contenu |
| --- | --- | --- |
| use_cases | 4 | Welcome on board · Long trip with personal audio zones · Urban manoeuvre with steer-by-wire · Lounge stop while charging |
| step_groups | 10 | Phases d'un use case (ex. Approach, Boarding, Departure) |
| steps | 24 | Étapes ordonnées par `position`, avec `description` et `trigger_text` (texte libre) |
| step_triggers | 29 | « Quand composant est dans état ». Le premier rang (`kind=trigger`) est l'événement, les suivants (`kind=condition`) doivent aussi être vrais. `operator` vaut toujours `is` |
| personas | 4 | Jacques (senior), Camille (conductrice), Léa (enfant), Hugo (passager avant) |
| use_case_personas | 9 | Personas présents dans chaque use case |
| persona_step_feedback | 54 | Par step et par persona : `action`, `insight`, `emotion_score` (de -5 à +5) |
| components | 20 | `role` : `trigger` (son état déclenche des steps), `reactive` (son état répond), `object` (regroupe d'autres composants) |
| component_states | 72 | États possibles de chaque composant, ordonnés par `position` |
| use_case_components | 44 | Composants présents dans chaque use case |
| step_component_states | 279 | La grille du blueprint : état de chaque composant à chaque step. Plusieurs rangs ordonnés par `sequence` si l'état change pendant le step |
| step_unused_components | 0 | Composants marqués « non utilisés » à un step |
| user_needs | 10 | Besoins exprimés par l'utilisateur |
| step_user_needs | 27 | Besoins servis par chaque step |

Règles du format :

- Les ids sont du texte d'environ 25 caractères, pas des UUID. Ils sont stables d'un export à l'autre.
- Les relations passent par des colonnes de clés étrangères nommées d'après l'id cible (`steps.use_case_id → use_cases.use_case_id`).
- Une cellule vide veut dire « non renseigné ». Le texte est en UTF-8. Attention au BOM possible en tête de `component_states.csv` : lire avec `encoding="utf-8-sig"`.
- Les colonnes `position` et `sequence` vont de 1 à n sans trou à l'intérieur de leur parent.
- `image_url` ne s'ouvre que pour les utilisateurs UserShaker connectés : on ne la télécharge pas, on la garde comme référence.

## Mapping UserShaker → SysML v2

| UserShaker | SysML v2 |
| --- | --- |
| project | `package ESpaceConcept` |
| personas | `part def Persona` + une part par persona, utilisée comme `actor` |
| use_cases | `use case def` avec `subject vehicle : ESpace` et ses acteurs |
| step_groups | regroupement des actions (action composite ou commentaire de phase) |
| steps | `action` dans le use case, enchaînées par `first … then` selon `position` |
| components | `part def` composés dans `part def ESpace` ; `role` stocké en attribut ou métadonnée |
| component_states | `state def` par composant, un `state` par état |
| step_triggers | transitions : `kind=trigger` → `accept`, `kind=condition` → garde `if` |
| step_component_states | états attendus à chaque step, qui servent à déduire les transitions |
| user_needs | `requirement def` (texte d'origine dans `doc`) |
| step_user_needs | `dependency` ou `satisfy` du step vers le requirement |
| persona_step_feedback | `doc` + attributs (`emotion_score`) sur l'action ; les scores négatifs deviennent des candidats requirements |

Métadonnée de traçabilité à appliquer partout :

```sysml
metadata def UserShakerRef {
    attribute id : ScalarValues::String;
    attribute kind : ScalarValues::String;
}
```

Conventions de nommage SysML : identifiants en `camelCase` ASCII dérivés du nom (sans accents ni espaces). En cas de collision, ajouter un suffixe numérique. Le nom d'origine va dans `doc` ou dans un nom entre guillemets simples (`'Léa Martin'`).

Les détails de grammaire SysML v2 varient selon les versions des outils : **toujours valider avec le parseur OMG** avant de supposer qu'une syntaxe est correcte.

## Pertes d'information connues dans ce dataset

Les contrôles doivent les détecter automatiquement. Ce sont aussi les exemples phares de la démo :

1. **Contrainte seulement en texte libre** : le step « Parking ratio » contient « Only below 10 km/h » dans `trigger_text`, mais aucun composant ne représente la vitesse dans `step_triggers`.
2. **Même cas** : le step « Léa starts a game » contient « The console is in the centre position », sans condition structurée.
3. **Incohérence entre personas** : dans « Long trip… », Jacques est passager avant « une heure après la recharge », alors que dans « Lounge stop while charging » c'est Hugo, et Hugo est absent du use case trajet.
4. **Besoins implicites** : des insights négatifs ne sont reliés à aucun user need, par exemple « Wants a clearer distance cue » (emotion -1, step « Obstacle close »).

## CATIA Magic Community : contraintes constatées (2026-11-04)

- L'éditeur textuel refuse plus de **20 000 caractères** par espace de noms racine ; le modèle est limité à
  **500 éléments majeurs** ; le menu Fichier n'a pas « Import From » (on colle le texte dans l'éditeur, puis Alt+S).
- Le modèle complet (`out/sysml/`, `out/model.sysml`) n'y entre donc pas : on y charge `out/catia_community/`, un fichier
  autonome par use case (`generate/catia.py`, template `catia_use_case.sysml.j2`), testé sous la limite
  (`tests/test_catia.py`). Toute réduction ajoutée va dans `TRIMS` et dans la matrice du fichier CATIA.
- Syntaxe vérifiée dans CATIA : `@UserShakerRef { :>> id = "..."; }` (CATIA réécrit `id =` en `:>> id =`),
  noms entre guillemets, unités `1.5 [m]`, gardes `if a and b`, `satisfy`, `dependency`, accents. Les
  commentaires `//` sont supprimés à la synchronisation.
- Vues : `DS_Views::SymbolicViews::gv` (générale), `::stv` (états), `::afv` (flux d'actions). Les éléments
  exposés ne s'affichent qu'après « Display > Display Exposed Elements ». Les vues typées par la bibliothèque
  OMG (`StandardViewDefinitions::GeneralView`) ne s'ouvrent pas en diagramme. Les noms de transition ne sont
  pas affichés, les libellés `accept` le sont.

## Traçabilité (livrable clé)

- `out/traceability.csv` : `usershaker_id, kind, sysml_qualified_name, status` (`mapped`, `partial`, `not_mapped` + raison).
- Contrôles automatiques : comptes conformes à `meta.csv`, chaque user need relié à au moins un step, chaque état atteint au moins une fois, chaque trigger pointant vers un état valide du bon composant, contraintes présentes uniquement en texte libre.
- Test aller-retour (si l'API tourne) : push du modèle, relecture, comparaison de l'ensemble des ids avec l'export.

## Partie LLM

- **Enrichissement** : lit le texte libre (descriptions, `trigger_text`, insights) et propose des gardes, des requirements et des reformulations. La sortie est en JSON structuré (validée par pydantic), toujours marquée `to_validate`.
- **Chat** : agent en tool calling. Le LLM ne répond jamais de mémoire. Il appelle des fonctions Python qui interrogent le graphe (`uncovered_needs`, `unreachable_states`, `constraints_in_text_only`, `persona_inconsistencies`, `find_element(id)`…) et cite les ids dans sa réponse.
- **Data protection** : n'envoyer au LLM que ce qui est nécessaire. Prévoir un mode Ollama local. Les clés vont dans `.env`, jamais commitées.
- **Mesurer avant de changer** : `make eval-llm` passe les questions de `evals/llm_questions.json` (ids et mots attendus, outils attendus, réponse en français, aucun id inventé) et écrit `out/llm_eval.json`. Lancer avant et après toute modification du prompt, des outils ou du modèle ; les réponses varient d'un passage à l'autre, comparer sur plusieurs passages avant de conclure. Un modèle cloud Ollama (suffixe `-cloud`) ne tourne pas en local : les requêtes quittent la machine.

## Interface (Streamlit) : spécification

Fichier d'entrée : `app/app.py`. L'interface ne contient **aucune logique métier**. Elle appelle uniquement des fonctions de `src/p9/` (ingest, generate, checks, llm), pour que le générateur puisse évoluer sans toucher à l'UI. Lancement : `make demo`, équivalent de `streamlit run app/app.py`.

Une seule page, en 4 sections numérotées, dans cet ordre :

### 1. Importer l'export

- `st.file_uploader` qui n'accepte que `.zip`.
- Les CSV peuvent être à la racine du zip ou dans un sous-dossier. Ignorer les fichiers `__MACOSX/` et `._*`.
- Associer chaque fichier à sa feuille par son nom exact (`components.csv`). Accepter aussi un préfixe séparé par `-` (`xxxx-components.csv`). Attention : `components` ne doit pas capturer `use_case_components`, ni `component_states` capturer `step_component_states`.
- Lire en `dtype=str`, `keep_default_na=False`, `encoding="utf-8-sig"`.
- Afficher : nom du projet, date d'export, version du schéma, nombre de use cases et de steps, et un indicateur global « Contrôles OK / À vérifier ».
- Panneau « Détail des contrôles » (ouvert automatiquement en cas de problème) :
  - comptes chargés vs `meta.csv`, feuille par feuille ;
  - feuilles manquantes ;
  - clés étrangères qui pointent vers un id introuvable.
- Si une feuille obligatoire manque (`use_cases`, `steps`, `personas`, `components`, `user_needs`), afficher une erreur claire et s'arrêter. Si le zip est illisible, afficher un message, jamais une stack trace.

### 2. Afficher les use cases importés

Une carte par use case, triée par `position` :

- titre `position. name`, et des badges : statut, nombre de steps, de personas, de user needs ;
- description ;
- personas sous forme de pastilles avec leur `color` et leur `age_category` ;
- phases (step_groups) dans l'ordre : `Approach → Boarding → Departure` ;
- un panneau dépliable « Parcours, besoins et émotions » avec :
  - la `story` ;
  - le tableau des steps (#, phase, nom, déclencheur `trigger_text`) ;
  - la liste des user needs servis ;
  - une courbe d'`emotion_score` par persona au fil des steps (seulement les lignes où `is_present = true`).

### 3. Choisir les use cases à traduire

- Une case à cocher sur chaque carte, cochée par défaut.
- Boutons « Tout sélectionner » et « Tout désélectionner ».
- Initialiser l'état avec `st.session_state.setdefault(...)` et ne pas passer `value=` à la checkbox, pour éviter l'avertissement Streamlit sur la double définition.
- Si rien n'est coché, afficher « Cochez au moins un use case » et ne rien générer.

### 4. Exporter les fichiers .sysml

- **Un fichier par use case** sélectionné, nommé `UC<position>_<NomEnPascalCase>.sysml`, contenant un `package` du même nom.
- **Un fichier commun `ESpaceCommon.sysml`** pour les éléments partagés : métadonnée `UserShakerRef`, `Persona` et les personas, les `part def` des composants, `part def ESpace`, les `requirement def`. Chaque fichier de use case l'importe (`private import ESpaceCommon::*;`). Sans ce fichier commun, CATIA trouverait des doublons dès qu'on importe deux use cases.
- Dans un fichier de use case : `use case def` avec `subject vehicle : ESpace`, les acteurs, une `action` par step (avec `UserShakerRef`, la phase et la description en `doc`), l'enchaînement `first … then`, et une `dependency` de chaque step vers les user needs qu'il sert.
- **Tableau de couverture** pour les use cases sélectionnés : use cases, steps, personas, user needs servis, triggers/conditions. Pour chacun : nombre dans l'export, nombre traduit, pourcentage, et une note sur ce qui manque. Ne jamais afficher 100 % pour un élément qui n'est pas réellement traduit.
- Téléchargements :
  - un bouton principal « Tout télécharger » : `.zip` contenant le fichier commun, les fichiers des use cases et `traceability.csv` ;
  - un bouton par fichier ;
  - un onglet par fichier avec l'aperçu du code et les numéros de ligne.
- Panneau dépliable avec la matrice de traçabilité complète (`usershaker_id, kind, sysml_qualified_name, status`).

### 5. Points à valider (après le générateur de base)

- Contraintes présentes seulement dans le texte libre (`trigger_text` sans condition structurée équivalente).
- Incohérences entre personas d'un use case à l'autre.
- Insights négatifs (`emotion_score < 0`) non reliés à un user need.
- Propositions du LLM, chacune avec les boutons « Accepter » / « Rejeter ». Seules les propositions acceptées sont ajoutées au modèle, et elles restent marquées `origin = "llm"`.

### Règles d'UI

- Interface en français, termes SysML en anglais (`use case`, `part def`…).
- `width="stretch"` plutôt que `use_container_width` (déprécié).
- Mettre en cache le chargement et la génération (`st.cache_data`, avec le contenu du zip comme clé).
- Tester sur l'export réel : 4 use cases, 24 steps, contrôles tous verts.

## Conventions de code

- Type hints partout ; pydantic pour toute donnée qui traverse une frontière (CSV, LLM, API).
- Fonctions pures pour l'ingestion, le mapping et les contrôles, faciles à tester.
- Sortie triée de façon stable (par `position`, puis par id) pour que les diffs du `.sysml` restent lisibles.
- Pas de chemin en dur : configuration dans `pyproject.toml` ou `.env`.
- Messages de commit courts, en français ou en anglais, avec l'id de la tâche du kanban (ex. `T15: génère le use case Lounge`).

## Définition de « fini »

1. Le code est sur `main` et `make demo` tourne.
2. Le `.sysml` généré passe le parseur OMG.
3. Les éléments générés portent leur `UserShakerRef`.
4. Un test `pytest` couvre la fonctionnalité.
5. La tâche a été montrée au Product Owner.

## Équipe (7)

| Rôle | Filière | Périmètre |
| --- | --- | --- |
| Product Owner & pitch | EVD | Priorités, expert UserShaker, revue métier, deck |
| Scrum Master & architecture | EVD | Rituels, schéma d'architecture, justification des choix |
| CATIA Magic & diagrammes | EVD | Import, vues, vidéo de démo |
| Ingestion & traçabilité | Objets connectés & cybersécu | `ingest/`, `model/`, `checks/` |
| Génération SysML v2 | Objets connectés & cybersécu | `generate/`, Streamlit |
| États, API, DevOps & data protection | Objets connectés & cybersécu | State machines, repo, tests, API Docker, confidentialité |
| LLM | Data IA | `llm/` : enrichissement et chat |

## Ce que Claude doit faire / éviter dans ce repo

- Commencer par lire le module concerné et les tests existants avant de modifier quoi que ce soit.
- Ne jamais modifier `data/usershaker/`.
- Ne jamais générer de SysML avec un LLM, ni coder en dur des ids ou des noms tirés du dataset dans le générateur : il doit fonctionner sur n'importe quel export au schéma 1.0. Ces ids peuvent apparaître dans les tests, mais pas dans le code de production.
- Après une modification du générateur : lancer `make generate validate test` et vérifier que les comptes de traçabilité n'ont pas baissé.
- En cas de doute sur la sémantique d'une donnée UserShaker, le signaler (TODO + question pour l'expert) plutôt que d'inventer une interprétation.

## Ordre de travail recommandé

Si le repo est vide, procéder dans cet ordre. Après chaque étape, lancer les tests, puis s'arrêter pour montrer le résultat avant de passer à la suite.

1. Structure du repo, `pyproject.toml`, `Makefile`, `.gitignore` (`out/`, `.env`).
2. `src/p9/ingest/` : chargement du zip ou d'un dossier, modèles pydantic, contrôles des comptes et des clés étrangères. Tests sur l'export réel.
3. `src/p9/generate/` : templates Jinja2 pour `ESpaceCommon.sysml` et un fichier par use case, avec `UserShakerRef` partout. Sortie stable, triée.
4. `src/p9/checks/` : matrice de traçabilité et tableau de couverture.
5. `app/app.py` : sections 1 à 4 de la spécification d'interface.
6. Ensuite seulement : états et transitions (`state def` + `step_triggers`), puis la section 5 et le LLM.

Si `prototype/` existe, s'en inspirer pour le chargement du zip et la mise en page, mais ranger le code dans `src/p9/` selon l'arborescence ci-dessus.
