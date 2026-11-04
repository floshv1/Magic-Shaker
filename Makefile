# Projet 9 : UserShaker -> SysML v2. Toutes les commandes passent par uv.
# Les recettes restent compatibles cmd.exe et sh (Windows / Linux / macOS).

UV ?= uv
# --no-sync : l'environnement n'est synchronise que par `make install` (a relancer apres un
# changement de dependances). Evite les reinstallations a chaque commande, que OneDrive bloque.
RUN := $(UV) run --no-sync
# OneDrive refuse les liens physiques : uv copie les fichiers.
export UV_LINK_MODE := copy

.PHONY: install ingest generate validate validate-setup trace test test-omg check demo clean

install:
	$(UV) sync

ingest:
	$(RUN) python -m p9.ingest

generate:
	$(RUN) python -m p9.generate

# Premier lancement : telecharge le parseur OMG (kernel SysML v2 + JDK 21 si absent).
validate-setup:
	$(RUN) python -m p9.checks.sysml_parser setup

# Fichiers separes (commun + use cases) dans une session, fichier unique dans une autre.
validate: validate-setup
	$(RUN) python -m p9.checks.sysml_parser validate-out

trace:
	$(RUN) python -m p9.checks

test:
	$(RUN) pytest

# Tests qui lancent le parseur OMG (lents : ~1-3 min).
test-omg: validate-setup
	$(RUN) pytest -m omg

# Ce qu'il faut lancer apres une modification du generateur.
check: generate validate trace test

demo: ingest generate trace
	$(RUN) streamlit run app/app.py

clean:
	$(RUN) python -c "import shutil; shutil.rmtree('out', ignore_errors=True)"
