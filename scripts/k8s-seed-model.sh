#!/usr/bin/env bash
# Register the locally trained model (data/processed/model.joblib) in the cluster's MLflow.
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl -n energy port-forward svc/mlflow 5055:5000 >/dev/null &
pf=$!
trap 'kill $pf' EXIT
sleep 3
MLFLOW_TRACKING_URI=http://localhost:5055 \
DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.12/site-packages/sklearn/.dylibs" \
  .venv/bin/python scripts/register_model.py
