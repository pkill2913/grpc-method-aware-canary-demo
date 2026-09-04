# Method-Aware Canary Deployments for gRPC

This repository demonstrates **method-aware canary releases for gRPC**:

- Gateway API `GRPCRoute` splits traffic by RPC method
- **Linkerd** implements the mesh `GRPCRoute` (Service parentRef / GAMMA) and exposes `response_total` metrics
- **Argo Rollouts** + the **Gateway API traffic-router plugin** drive progressive `setWeight` on `GetUser`
- `CreateUser` stays **100% on stable** for the whole experiment

During a canary, `setWeight: 10|25|50` becomes real `GetUser` traffic (90/10 → 75/25 → 50/50) while `CreateUser` remains on stable until you promote.

## What you will build

- Deploy the demo (`frontend`, `user-api`, `user-service` via Argo Rollouts)
- Route `GetUser` and `CreateUser` independently with one `GRPCRoute`
- Let Argo `setWeight` update **only** the `GetUser` rule through the Gateway API plugin
- Keep `CreateUser` on the stable Service
- Validate distribution with a load-test `AnalysisTemplate` (expected mix passed per step)
- Validate canary health with Linkerd Viz Prometheus
- Promote or abort and confirm `GetUser` returns to 100% stable

## Architecture

```
Browser
  └── frontend (HTTP :8080)
        └── user-api (HTTP :8080 → gRPC)
              └── user-service (gRPC :50051)   ← GRPCRoute parent (Service, no selector)
                    ├── user-service-stable
                    └── user-service-canary
```

Request flow:

1. Browser → `frontend` (HTTP). Optionally via Envoy Gateway `HTTPRoute`.
2. `frontend` proxies `/api/*` to `user-api`.
3. `user-api` opens a gRPC client to `user-service:50051`.
4. Linkerd applies `GRPCRoute` `userservice-route` (parentRef = Service `user-service`).
5. Method match selects backends; Argo updates stable/canary Service selectors + `GetUser` weights.

| Component | Responsibility in this demo |
|-----------|-----------------------------|
| **Gateway API `GRPCRoute`** | Method-aware rules. `GetUser` has stable+canary backends (plugin-managed weights). `CreateUser` / default are stable-only. |
| **Linkerd** | Mesh data plane + **controller** for the Service-attached `GRPCRoute` (`controllerName: linkerd.io/policy-controller`). mTLS + `response_total`. |
| **Envoy Gateway** | Optional north-south HTTP path (`GatewayClass` / `Gateway` / `HTTPRoute` → frontend). **Not** the controller of the mesh `GRPCRoute`. |
| **Argo Rollouts** | ReplicaSets, stable/canary Services, analysis, promote/abort. |
| **Gateway API plugin** (`argoproj-labs/gatewayAPI`) | Translates `setWeight` into `GRPCRoute` backend weights for rules that reference **both** stable and canary Services. |
| **Linkerd Viz Prometheus** | Queried by the success-rate `AnalysisTemplate`. |

### Why `CreateUser` is never canaried

The Gateway API plugin only changes weights on rules that contain **both** `user-service-stable` and `user-service-canary`. The `CreateUser` rule lists only stable, so `setWeight` never touches it. You do **not** need separate `GRPCRoute` objects for that behavior.

```
setWeight: N  →  GetUser: stable=(100-N), canary=N
                 CreateUser: stable=100   (unchanged)
```

## Repository layout

```
.
├── README.md
├── app/                          # frontend, user-api, user-service, load-test
└── k8s/
    ├── base/                     # namespace, frontend/user-api, storage
    ├── gateway-api/              # GatewayClass, Gateway, HTTPRoute, GRPCRoute
    ├── argo-rollouts/            # Rollout, Services, AnalysisTemplates
    └── platform/                 # extras required for plugin + Prometheus authz
        ├── argo-rollouts-gateway-plugin-configmap.yaml
        ├── argo-rollouts-gateway-plugin-rbac.yaml
        └── prometheus-argo-rollouts-authz.yaml
```

## Prerequisites

### Local tools

- `kubectl`
- `podman` (builds + `podman save`)
- `python3` (optional manual load test)
- [k3d](https://k3d.io/) (local cluster + `k3d image import`)
- [Argo Rollouts kubectl plugin](https://argo-rollouts.readthedocs.io/en/stable/installation/#kubectl-plugin)
- `helm` (convenient for Envoy Gateway install)
- [Linkerd CLI](https://linkerd.io/2/getting-started/) (install / check)

On macOS with Podman, point Docker-compatible tools at the Podman socket before using k3d:

```bash
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
```

If Helm OCI pulls fail with `docker-credential-osxkeychain`, use a stub or an empty `DOCKER_CONFIG` that does not call a missing helper.

### Kubernetes platform (install separately)

| Component | Notes |
|-----------|--------|
| Gateway API CRDs (incl. `GRPCRoute`) | Standard channel; Envoy Gateway chart can install them |
| Envoy Gateway | For the demo HTTP `Gateway` / `HTTPRoute` |
| Linkerd + Linkerd Viz | Mesh + Prometheus |
| Argo Rollouts controller | Plus Gateway API **plugin** (see below) |

This repo applies the demo in `app-demo`, plus helper manifests under `k8s/platform/`.

### Environment assumptions

| Assumption | Value |
|------------|-------|
| Demo namespace | `app-demo` (`linkerd.io/inject: enabled`) |
| Images | `localhost/frontend|user-api|user-service|user-api-load-test:latest` |
| Image pull policy | `IfNotPresent` (import into k3d) |
| user-service | gRPC `50051`, `appProtocol: kubernetes.io/h2c` |
| Local cluster | k3d + Podman |

---

## Platform extras that this lab needs

These are easy to miss; without them the demo fails in subtle ways.

### 1) Argo Rollouts Gateway API plugin

```bash
# Match linux-amd64 vs linux-arm64 to your cluster nodes
kubectl apply -f k8s/platform/argo-rollouts-gateway-plugin-configmap.yaml
kubectl apply -f k8s/platform/argo-rollouts-gateway-plugin-rbac.yaml
kubectl -n argo-rollouts rollout restart deploy/argo-rollouts
kubectl -n argo-rollouts rollout status deploy/argo-rollouts
kubectl -n argo-rollouts logs deploy/argo-rollouts -c argo-rollouts --since=5m \
  | grep -i 'gatewayAPI\|Download'
```

Expected log lines: `Downloading plugin argoproj-labs/gatewayAPI` and `Download complete`.

Docs: [plugin installation](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io/en/stable/installation/).

### 2) Let Argo query Linkerd Viz Prometheus (avoid HTTP 403)

Linkerd Viz protects Prometheus with `Server/prometheus-admin` (`accessPolicy: deny`). Only `metrics-api` is allowed by default, so Argo’s Prometheus provider gets **403**.

Fix used in this lab (preferred over removing the Prometheus sidecar):

1. Mesh the Argo Rollouts controller (pod **template** annotation, not only the Deployment object):

```bash
kubectl -n argo-rollouts patch deploy/argo-rollouts --type strategic -p '
spec:
  template:
    metadata:
      annotations:
        linkerd.io/inject: enabled
'
kubectl -n argo-rollouts rollout status deploy/argo-rollouts
```

2. Authorize that ServiceAccount:

```bash
kubectl apply -f k8s/platform/prometheus-argo-rollouts-authz.yaml
```

### 3) `failureLimit` on analysis metrics

`failureLimit` is “how many failed measurements are **allowed**”. With `count: 1` and `failureLimit: 1`, a single failed sample still leaves the AnalysisRun **Successful**. This repo sets `failureLimit: 0` on the success-rate template so a hard gate actually fails the canary.

---

## Step 1 — Clone

```bash
git clone https://github.com/pkill2913/grpc-method-aware-canary-demo.git
cd grpc-method-aware-canary-demo
```

## Step 2 — Verify platform

```bash
kubectl get crd grpcroutes.gateway.networking.k8s.io
kubectl get ns linkerd linkerd-viz argo-rollouts
kubectl -n linkerd-viz get svc prometheus
kubectl -n argo-rollouts get deploy
kubectl argo rollouts version
# plugin loaded?
kubectl -n argo-rollouts get cm argo-rollouts-config -o yaml
```

## Step 3 — Build images (Podman)

```bash
podman build -t localhost/frontend:latest ./app/frontend
podman build -t localhost/user-api:latest ./app/user-api
podman build -t localhost/user-service:latest ./app/user-service
podman build -t localhost/user-api-load-test:latest ./app/user-api-load-test
```

## Step 4 — Import into k3d

```bash
CLUSTER=<your-k3d-cluster-name>

for image in frontend user-api user-service user-api-load-test; do
  podman save "localhost/${image}:latest" -o "/tmp/${image}.tar"
  k3d image import "/tmp/${image}.tar" -c "${CLUSTER}"
done
```

## Step 5 — Namespace and storage

```bash
kubectl apply -f k8s/base/namespace.yaml
kubectl apply -n app-demo -f k8s/base/storage.yaml
```

## Step 6 — frontend + user-api

```bash
kubectl apply -n app-demo -f k8s/base/services.yaml
kubectl apply -f k8s/base/deployments.yaml
```

**Do not apply** `k8s/base/user-service.yaml` when using Argo Rollouts (static alternate setup).

## Step 7 — Rollout Services

```bash
kubectl apply -f k8s/argo-rollouts/services/
```

| Service | Purpose |
|---------|---------|
| `user-service` | Parent for `GRPCRoute` (no selector) |
| `user-service-stable` / `user-service-canary` | Backends; Argo owns selectors |

## Step 8 — Gateway API resources

```bash
kubectl apply -f k8s/gateway-api/gatewayclass.yaml
kubectl apply -f k8s/gateway-api/gateway.yaml
kubectl apply -f k8s/gateway-api/httproute.yaml
kubectl apply -f k8s/gateway-api/grpcroute.yaml
```

Confirm the mesh accepted the route:

```bash
kubectl get grpcroute userservice-route -n app-demo -o yaml | grep -A5 'controllerName:'
# expect: linkerd.io/policy-controller, Accepted=True, ResolvedRefs=True
```

Initial `GetUser` weights are `stable=100 / canary=0`. The plugin changes them during the Rollout.

## Step 9 — AnalysisTemplates

```bash
kubectl apply -f k8s/argo-rollouts/analysistemplate-loadtest.yaml
kubectl apply -f k8s/argo-rollouts/analysistemplate-metrics.yaml
```

| Template | Role |
|----------|------|
| `grpc-load-generator` | Job load test; `DISTRIBUTION` from Rollout arg `distribution` |
| `user-service-success-rate` | Canary success ratio from Linkerd `response_total` ≥ `0.99` |

## Step 10 — Deploy the Rollout

```bash
kubectl apply -f k8s/argo-rollouts/rollout.yaml
```

Important fields in `k8s/argo-rollouts/rollout.yaml`:

- `trafficRouting.plugins.argoproj-labs/gatewayAPI` → `grpcRoute: userservice-route`
- Steps: `setWeight` **10 → 25 → 50**, each followed by a short pause + distribution analysis
- Final success-rate analysis, then indefinite `pause: {}` for manual promote

### Start the canary (v2)

First apply usually settles revision 1 as stable (`APP_VERSION=v1`) without exercising the full path. Start canary:

```bash
kubectl patch rollout user-service -n app-demo --type='json' -p='[
  {
    "op": "replace",
    "path": "/spec/template/spec/containers/0/env/0/value",
    "value": "v2"
  }
]'
```

Watch:

```bash
kubectl argo rollouts get rollout user-service -n app-demo --watch
kubectl get grpcroute userservice-route -n app-demo -o yaml
```

At each weight, confirm **only** `GetUser` changed:

| Step | GetUser (stable/canary) | CreateUser |
|------|-------------------------|------------|
| `setWeight: 10` | 90 / 10 | 100 / — |
| `setWeight: 25` | 75 / 25 | 100 / — |
| `setWeight: 50` | 50 / 50 | 100 / — |
| after promote | 100 / 0 | 100 / — |
| after abort / failed analysis | 100 / 0 | 100 / — |

While a canary is in progress the plugin labels the route with `rollouts.argoproj.io/gatewayapi-canary=in-progress`.

## Step 11 — Verify install

```bash
kubectl get pods,svc,gateway,httproute,grpcroute,rollout,analysistemplate -n app-demo
```

Injected app pods include a Linkerd proxy (often as a native sidecar / init container).

## Step 12 — Access the UI

```bash
kubectl port-forward -n app-demo svc/frontend 8080:8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080).

## Step 13 — Manual load test

With canary at a known weight (example: 10%):

```bash
kubectl port-forward -n app-demo svc/user-api 8080:8080
```

```bash
API_BASE_URL=http://127.0.0.1:8080 \
REQUESTS=100 \
DISTRIBUTION='GetUser=v1:90,v2:10;CreateUser=v1:100' \
MARGIN=5 \
python3 app/user-api-load-test/main.py
```

Change `DISTRIBUTION` when testing 25% (`v1:75,v2:25`) or 50% (`v1:50,v2:50`).

## Step 14 — Promote

At the final manual pause, after analyses succeeded:

```bash
kubectl argo rollouts promote user-service -n app-demo
```

Verify:

```bash
kubectl argo rollouts get rollout user-service -n app-demo
kubectl get grpcroute userservice-route -n app-demo -o yaml
kubectl get svc user-service-stable user-service-canary -n app-demo -o yaml
```

Expected: previous canary revision becomes stable; `GetUser` weights return to `100/0`; traffic reports the **new** stable version (e.g. all `v2`).

## Step 15 — Abort / failed analysis

```bash
kubectl argo rollouts abort user-service -n app-demo
```

Or let a failing AnalysisRun abort the step. In both cases, re-check the live `GRPCRoute`: the plugin should restore `GetUser` to `stable=100 / canary=0` while `CreateUser` remains stable-only. Confirm with a load test against the stable version.

## Expected progressive behavior

```
GetUser:   90/10 → 75/25 → 50/50 → (promote) 100/0 on new stable
CreateUser: 100% stable for the entire rollout
```

## Troubleshooting

### ImagePullBackOff

Rebuild + `k3d image import`, then delete pods so they recreate.

### Prometheus analysis 403

Argo is not authorized (or not meshed). Re-apply `k8s/platform/prometheus-argo-rollouts-authz.yaml` and ensure the controller pod template has `linkerd.io/inject: enabled`.

### `setWeight` does not change the GRPCRoute

- Plugin ConfigMap missing / wrong arch binary / controller not restarted
- RBAC missing `update/patch` on `grpcroutes` in `app-demo`
- `GetUser` rule missing **both** stable and canary `backendRefs`

### CreateUser suddenly gets canary traffic

Stop and inspect the `GRPCRoute`. The plugin should not rewrite stable-only rules. If a rule incorrectly lists both backends, fix the manifest.

### AnalysisRun Successful despite a failed sample

Check `failureLimit`. Use `0` for fail-closed gates.

### Gateway `Programmed=False` / `AddressNotAssigned` on k3d

Common without an external LB IP. Listener may still be programmed; use `kubectl port-forward` to the frontend Service for the UI.

### Linkerd proxy not injected

Namespace annotation + restart workloads. For Argo Rollouts itself, annotate `spec.template.metadata`, not only `metadata` on the Deployment.

## Cleanup

```bash
kubectl delete namespace app-demo
kubectl delete gatewayclass envoy-gatewayclass
kubectl delete -f k8s/platform/prometheus-argo-rollouts-authz.yaml
# optional: remove plugin ConfigMap / RBAC under k8s/platform/
k3d cluster delete <cluster-name>
rm -f /tmp/frontend.tar /tmp/user-api.tar /tmp/user-service.tar /tmp/user-api-load-test.tar
```

## Learn more

- [Linkerd](https://linkerd.io/docs/)
- [Linkerd Viz](https://linkerd.io/2/tasks/observability/)
- [Gateway API](https://gateway-api.sigs.k8s.io/) / [GRPCRoute](https://gateway-api.sigs.k8s.io/api-types/grpcroute/)
- [Envoy Gateway](https://gateway.envoyproxy.io/docs/)
- [Argo Rollouts](https://argo-rollouts.readthedocs.io/)
- [Gateway API traffic-router plugin](https://rollouts-plugin-trafficrouter-gatewayapi.readthedocs.io/)

This demo accompanies the Buoyant blog post:

**[Method-Aware Canary Releases for gRPC with GRPCRoute, Linkerd, and Argo Rollouts](https://buoyant.io/blog/)**

## License

Intended to be Apache License 2.0. A `LICENSE` file is not present in the repository at the time of writing.
