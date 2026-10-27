#!/usr/bin/env bash
# Local Kubernetes (kind): cluster + images + metrics-server + secret from .env + manifests.
# Needs docker, kind, kubectl. The ENTSO-E key is read from .env and never written to the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=energy
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config k8s/kind-cluster.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

docker build -t energy-forecasting:local .
docker build -t energy-mlflow:local -f docker/mlflow.Dockerfile .
kind load docker-image energy-forecasting:local energy-mlflow:local --name "$CLUSTER"

# metrics-server (HPA needs it); kind kubelets use self-signed certs
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

kubectl apply -f k8s/base/namespace.yaml
key=$(grep -E '^ENTSOE_API_KEY=' .env | cut -d= -f2-)
kubectl -n energy create secret generic entsoe --from-literal=ENTSOE_API_KEY="$key" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset key

kubectl apply -k k8s/base
kubectl -n energy rollout status deploy/mlflow --timeout=180s
echo "Next: scripts/k8s-seed-model.sh (register the trained model), then kubectl -n energy create job --from=cronjob/infer infer-manual"
