PYTHON ?= python3
VENV_DIR := .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_STAMP := $(VENV_DIR)/.dependencies-installed

.PHONY: install check dry-run run session clean

install: $(VENV_STAMP)

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_DIR)

$(VENV_STAMP): requirements.txt | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt
	touch $(VENV_STAMP)

check: $(VENV_STAMP)
	$(VENV_PYTHON) -m compileall -q app tests
	$(VENV_PYTHON) -m unittest discover -s tests -v

dry-run: $(VENV_STAMP)
	$(VENV_PYTHON) -m app.dry_run

run: $(VENV_STAMP)
	$(VENV_PYTHON) -m app.main

session: $(VENV_STAMP)
	$(VENV_PYTHON) -m app.create_session

clean:
	rm -rf $(VENV_DIR) app/__pycache__ tests/__pycache__
