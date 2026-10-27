#!/usr/bin/env bash
# Smoke test against the kind cluster from k8s-up.sh: probes, shared volume, NodePort and TLS ingress.
set -euo pipefail

kubectl -n energy wait --for=condition=available deploy/api deploy/mlflow --timeout=120s

# the API must answer before any forecast exists (404 with a clear message, not a crash)
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health)
[ "$code" = 200 ] || { echo "health: $code"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/forecast/latest)
[ "$code" = 404 ] || { echo "forecast before job: $code (expected 404)"; exit 1; }

# write a sample forecast to the shared volume the way the CronJob would, then read it through the API
kubectl -n energy apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: {name: writer, namespace: energy}
spec:
  restartPolicy: Never
  securityContext: {runAsUser: 1000, fsGroup: 1000}
  containers:
    - name: w
      image: busybox:1.37
      command: ["sh", "-c", "echo '{\"delivery_date\":\"2000-01-01\",\"quantiles\":{},\"charge_mw\":[],\"discharge_mw\":[]}' > /outputs/latest.json"]
      volumeMounts: [{name: o, mountPath: /outputs}]
  volumes: [{name: o, persistentVolumeClaim: {claimName: forecasts}}]
YAML
kubectl -n energy wait --for=jsonpath='{.status.phase}'=Succeeded pod/writer --timeout=60s
kubectl -n energy delete pod writer --wait=false >/dev/null

curl -fsS http://127.0.0.1:8080/forecast/latest | grep -q '"delivery_date":"2000-01-01"'
echo "NodePort OK"
curl -fsSk --resolve energy.localtest.me:8443:127.0.0.1 https://energy.localtest.me:8443/forecast/latest | grep -q '"delivery_date":"2000-01-01"'
echo "TLS ingress OK"
kubectl -n energy get hpa api -o jsonpath='{.spec.minReplicas}-{.spec.maxReplicas} replicas{"\n"}'
echo "smoke test passed"
