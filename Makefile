# Filing Analyst -- end-to-end pipeline.
# Typical first run:  make install && make all && make serve
# (fetch -> db -> index need credentials in .env; see .env.example.)

PY := .venv/bin/python

.PHONY: help venv install fetch db index serve eval test check all clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

venv:  ## Create the Python 3.12 virtual environment
	python3.12 -m venv .venv

install: venv  ## Install dependencies into the venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	@echo "Dependencies installed."

fetch:  ## Download the latest 10-K filings from SEC EDGAR -> data/raw/
	$(PY) -m scripts.fetch_filings

db:  ## Build the financials SQLite DB from SEC XBRL companyfacts
	$(PY) -m scripts.build_financials_db

index:  ## Build the vector index from the downloaded filings
	$(PY) -m scripts.build_index

check:  ## Preflight the provider (key + chat + embed models)
	$(PY) -m scripts.check_provider

serve:  ## Run the FastAPI server + chat UI at http://localhost:8000
	$(PY) -m uvicorn app.server:app --port 8000

eval:  ## Run the eval harness -> eval/results/latest.json + summary.md
	$(PY) -m eval.run_eval

test:  ## Run the unit tests (SQL guard, chunker, scorers)
	$(PY) -m pytest -q

all: fetch db index  ## Build the full data pipeline (fetch -> db -> index)
	@echo "Pipeline built. Run 'make serve' to chat or 'make eval' to grade."

clean:  ## Remove generated data (filings, DB, index)
	rm -rf data/raw data/index data/financials.db
