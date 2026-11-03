# Convenience commands for the current containerized PostgreSQL database.
# Pipeline orchestration belongs to Airflow, not Make.
.DEFAULT_GOAL := help
.PHONY: help login inventory query query-descriptions
PSQL := psql -h 127.0.0.1 -p 55432 -X -v ON_ERROR_STOP=1 -U radiofrance -d radiofrance

help:
	@echo 'Available targets: login, inventory, query, query-descriptions'
	@echo 'Example: make query QUERY="général de Gaulle pendant la guerre"'

login:
	$(PSQL)

inventory:
	$(PSQL) -f inventory.sql

# Override with make query QUERY="your question" QUERY_PYTHON=/path/to/python.
QUERY ?= général de Gaulle pendant la guerre
QUERY_PYTHON ?= venv/radiofrance/bin/python
QUERY_DESCRIPTIONS ?= false
query-descriptions: QUERY_DESCRIPTIONS = true
query query-descriptions:
	@queryvector=$$(PYTHONPATH="$(CURDIR)/src$${PYTHONPATH:+:$$PYTHONPATH}" $(QUERY_PYTHON) query_to_embedding.py "$(QUERY)") && \
	    $(PSQL) -v descriptions="$(QUERY_DESCRIPTIONS)" -v queryvector="$$queryvector" -f query.sql
