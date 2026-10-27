# Pipeline: ingest -> features -> train -> backtest -> dispatch -> report
# On macOS without Homebrew's libomp, LightGBM is pointed at the libomp shipped with scikit-learn.
ifeq ($(shell uname),Darwin)
  PY = DYLD_LIBRARY_PATH=$(CURDIR)/.venv/lib/python3.12/site-packages/sklearn/.dylibs $(CURDIR)/.venv/bin/python -m
else
  PY = uv run python -m
endif
CLI = $(PY) energy_forecasting.cli

.PHONY: setup ingest features train backtest dispatch report pipeline lint types test test-all fmt mlflow-up mlflow-down tf-check k8s-check clean

setup:
	uv sync --frozen
	uv run pre-commit install

ingest:
	$(CLI) ingest
features:
	$(CLI) features
train:
	$(CLI) train
backtest:
	$(CLI) backtest
dispatch:
	$(CLI) dispatch
report:
	$(CLI) report

pipeline: ingest features train backtest dispatch report

lint:
	uv run ruff check . && uv run ruff format --check .
fmt:
	uv run ruff check --fix . && uv run ruff format .
types:
	uv run mypy src
test:
	$(PY) pytest
test-all:
	$(PY) pytest -m "slow or not slow"

mlflow-up:
	docker compose up -d --build mlflow
mlflow-down:
	docker compose down

tf-check:
	terraform -chdir=infra fmt -check -recursive && terraform -chdir=infra init -backend=false && terraform -chdir=infra validate

k8s-check:
	for d in base kind aks; do kubectl kustomize k8s/$$d | kubeconform -strict -summary -kubernetes-version 1.33.0; done

clean:
	rm -rf data/processed mlruns .mypy_cache .ruff_cache .pytest_cache
