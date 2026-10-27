#!/usr/bin/env bash
# Local Kubernetes (kind): cluster + images + metrics-server + ingress-nginx + TLS + secret from .env + manifests.
# Needs docker, kind, kubectl, openssl. The ENTSO-E key is read from .env and never written to the repo.
# Images are tagged with the git SHA (override with TAG=...); the manifests' `:local` tag is rewritten at apply time.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=energy
TAG=${TAG:-$(git rev-parse --short HEAD)}
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config k8s/kind-cluster.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

docker build -t "energy-forecasting:$TAG" .
docker build -t "energy-mlflow:$TAG" -f docker/mlflow.Dockerfile .
kind load docker-image "energy-forecasting:$TAG" "energy-mlflow:$TAG" --name "$CLUSTER"

# metrics-server (HPA needs it); kind kubelets use self-signed certs
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

# ingress-nginx (kind flavour: hostPort 80/443 on the labelled control-plane node)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=180s

kubectl apply -f k8s/base/namespace.yaml
if [ -f .env ]; then
  key=$(grep -E '^ENTSOE_API_KEY=' .env | cut -d= -f2-)
  kubectl -n energy create secret generic entsoe --from-literal=ENTSOE_API_KEY="$key" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  unset key
fi

# self-signed certificate for the ingress host
crt=$(mktemp -d)
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -keyout "$crt/tls.key" -out "$crt/tls.crt" \
  -subj "/CN=energy.localtest.me" -addext "subjectAltName=DNS:energy.localtest.me" 2>/dev/null
kubectl -n energy create secret tls energy-tls --cert="$crt/tls.crt" --key="$crt/tls.key" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
rm -rf "$crt"

manifests=$(kubectl kustomize k8s/kind \
  | sed -e "s#energy-forecasting:local#energy-forecasting:$TAG#" -e "s#energy-mlflow:local#energy-mlflow:$TAG#")
# the ingress admission webhook can lag behind the controller's rollout: retry
for i in 1 2 3 4 5 6; do
  echo "$manifests" | kubectl apply -f - && break
  [ "$i" = 6 ] && exit 1
  sleep 10
done
kubectl -n energy rollout status deploy/mlflow --timeout=180s
kubectl -n energy rollout status deploy/api --timeout=180s
echo "images tagged $TAG; API: http://127.0.0.1:8080 and https://energy.localtest.me:8443 (self-signed, use curl -k)"
echo "Next: scripts/k8s-seed-model.sh, then kubectl -n energy create job --from=cronjob/infer infer-manual"
