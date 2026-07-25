# Method-Aware Canary Deployments for gRPC

This repository demonstrates method-aware canary releases for gRPC: Gateway API `GRPCRoute` splits traffic by RPC method, Linkerd supplies mesh metrics for analysis, and Argo Rollouts drives progressive delivery. During the canary, `GetUser` can reach stable and canary backends while `CreateUser` stays on stable until you promote.

## What you will build

- Deploy the demo application (`frontend`, `user-api`, and `user-service` via Argo Rollouts)
- Route `GetUser` and `CreateUser` independently with a `GRPCRoute`
- Send a percentage of `GetUser` traffic to the canary
- Keep `CreateUser` on the stable version
- Validate routing with a load-test `AnalysisTemplate`
- Validate canary health with Linkerd Prometheus metrics
- Promote or abort the rollout

## Architecture

```
Browser
  └── frontend (HTTP :8080)
        └── user-api (HTTP :8080 → gRPC)
              └── user-service (gRPC :50051)
                    ├── user-service-stable  (stable ReplicaSet)
                    └── user-service-canary  (canary ReplicaSet)
```

Request flow:

1. The browser talks to `frontend` over HTTP.
2. `frontend` proxies `/api/*` to `user-api`.
3. `user-api` opens a gRPC client to `user-service:50051`.
4. The `GRPCRoute` attached to Service `user-service` selects backends by method (`GetUser`, `CreateUser`, or default).
5. Stable and canary pods are selected through `user-service-stable` and `user-service-canary`.

| Component | Responsibility in this demo |
|-----------|-----------------------------|
| **Gateway API** | Declares `GatewayClass`, `Gateway`, `HTTPRoute` (browser → frontend), and `GRPCRoute` (method-aware gRPC routing to user-service backends). |
| **GRPCRoute** | Owns the method-aware weights: `GetUser` 90% stable / 10% canary; `CreateUser` and default 100% stable. |
| **Linkerd** | Injects the data-plane proxy into the `app-demo` namespace (`linkerd.io/inject: enabled`) and provides mTLS plus `response_total` metrics. |
| **Linkerd Viz / Prometheus** | Exposes Prometheus at `http://prometheus.linkerd-viz.svc.cluster.local:9090`, which the success-rate `AnalysisTemplate` queries. |
| **Argo Rollouts** | Manages the `user-service` Rollout, stable/canary Services, analysis Jobs/metrics, pauses, and promotion. |

> **Note:** The Rollout strategy includes `setWeight: 50`, but this repository does **not** configure Argo `trafficRouting` against the `GRPCRoute`. Method-aware percentages come from `k8s/gateway-api/grpcroute.yaml`. Argo still advances canary steps, updates stable/canary Service selectors, and runs analysis.

## Repository layout

```
.
├── README.md
├── .gitignore
├── app/
│   ├── frontend/                 # Flask UI (HTTP :8080)
│   │   ├── Dockerfile
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   ├── static/
│   │   └── templates/
│   ├── user-api/                 # HTTP API → gRPC client (HTTP :8080)
│   │   ├── Dockerfile
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   └── proto/user.proto
│   ├── user-api-load-test/       # Load test used by AnalysisTemplate + local runs
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── proto/user.proto
│   └── user-service/             # gRPC UserService (gRPC :50051)
│       ├── Dockerfile
│       ├── server.py
│       ├── requirements.txt
│       ├── user_pb2.py
│       ├── user_pb2_grpc.py
│       └── proto/user.proto
└── k8s/
    ├── base/
    │   ├── namespace.yaml         # Namespace app-demo + Linkerd inject
    │   ├── storage.yaml          # PVC user-service-store
    │   ├── services.yaml         # frontend + user-api Services
    │   ├── deployments.yaml      # frontend + user-api Deployments
    │   └── user-service.yaml     # Alternate static v1/v2 setup (do not apply with Rollouts)
    ├── gateway-api/
    │   ├── gatewayclass.yaml     # GatewayClass envoy-gatewayclass
    │   ├── gateway.yaml          # Gateway app-gateway (HTTP :80)
    │   ├── httproute.yaml        # HTTPRoute frontend-route
    │   └── grpcroute.yaml        # GRPCRoute userservice-route
    └── argo-rollouts/
        ├── rollout.yaml          # Rollout user-service
        ├── analysistemplate-loadtest.yaml
        ├── analysistemplate-metrics.yaml
        └── services/
            ├── user-service.yaml # parent Service for GRPCRoute (no selector)
            ├── stable.yaml       # user-service-stable
            └── canary.yaml       # user-service-canary
```

## Prerequisites

### Local tools

Required by the commands in this tutorial:

- `kubectl`
- `podman` (image builds and `podman save`)
- `python3` (optional manual load test from your machine)
- [k3d](https://k3d.io/) (recommended for local clusters and `k3d image import`)
- [Argo Rollouts kubectl plugin](https://argo-rollouts.readthedocs.io/en/stable/installation/#kubectl-plugin) (`kubectl argo rollouts …`)

### Kubernetes platform components

This repository does **not** install the following. Install them separately with their official docs before continuing.

| Component | Status |
|-----------|--------|
| Gateway API CRDs (including `GRPCRoute`) | Must be installed separately |
| Envoy Gateway | Must be installed separately |
| Linkerd | Must be installed separately |
| Linkerd Viz (includes Prometheus) | Must be installed separately |
| Argo Rollouts controller | Must be installed separately |
| Argo Rollouts kubectl plugin | Must be installed separately (local tool) |

This repository **does** apply:

- `GatewayClass` `envoy-gatewayclass` (`controllerName: gateway.envoyproxy.io/gatewayclass-controller`)
- Demo app resources in namespace `app-demo`
- Argo Rollout, Services, and AnalysisTemplates

### Environment assumptions

Confirmed from the manifests and build files:

| Assumption | Value |
|------------|-------|
| Demo namespace | `app-demo` |
| Linkerd injection | Namespace annotation `linkerd.io/inject: enabled` |
| Local images | `localhost/frontend:latest`, `localhost/user-api:latest`, `localhost/user-service:latest`, `localhost/user-api-load-test:latest` |
| Image pull policy | `IfNotPresent` (expects images present on the node / imported into k3d) |
| Frontend / user-api ports | HTTP `8080` |
| user-service port | gRPC `50051` (`appProtocol: kubernetes.io/h2c` on Services) |
| Gateway listener | HTTP `80` on Gateway `app-gateway` |
| Recommended local cluster | k3d (for `k3d image import`) |
| Container tooling in docs | Podman |

## Step 1 — Clone and inspect the repository

```bash
git clone https://github.com/pkill2913/grpc-method-aware-canary-demo.git
cd grpc-method-aware-canary-demo
```

If you use SSH:

```bash
git clone git@github.com:pkill2913/grpc-method-aware-canary-demo.git
cd grpc-method-aware-canary-demo
```

Inspect the layout:

```bash
ls
ls app k8s
```

## Step 2 — Verify platform dependencies

Confirm Gateway API `GRPCRoute` exists:

```bash
kubectl get crd grpcroutes.gateway.networking.k8s.io
```

Expected: the CRD is listed.

Confirm a GatewayClass API is available (this repo later creates `envoy-gatewayclass`):

```bash
kubectl get gatewayclass
```

Expected: the API responds. After Step 8 you should see `envoy-gatewayclass`.

Confirm Linkerd control plane:

```bash
kubectl get ns linkerd
kubectl -n linkerd get deploy
```

Expected: namespace `linkerd` exists and control-plane Deployments are present.

Confirm Linkerd Viz (Prometheus used by analysis):

```bash
kubectl get ns linkerd-viz
kubectl -n linkerd-viz get svc prometheus
```

Expected: Service `prometheus` exists in `linkerd-viz` (AnalysisTemplate address: `http://prometheus.linkerd-viz.svc.cluster.local:9090`).

Confirm Argo Rollouts controller:

```bash
kubectl get ns argo-rollouts
kubectl -n argo-rollouts get deploy
```

Expected: the Rollouts controller Deployment is present (namespace name may differ if you installed elsewhere; the controller must be running in the cluster).

Confirm the kubectl plugin:

```bash
kubectl argo rollouts version
```

Expected: plugin version output (not “unknown command”).

## Step 3 — Build the container images

From the repository root:

```bash
podman build -t localhost/frontend:latest ./app/frontend
podman build -t localhost/user-api:latest ./app/user-api
podman build -t localhost/user-service:latest ./app/user-service
podman build -t localhost/user-api-load-test:latest ./app/user-api-load-test
```

| Image | Used by |
|-------|---------|
| `localhost/frontend:latest` | `frontend` Deployment |
| `localhost/user-api:latest` | `user-api` Deployment (`image: localhost/user-api` in the manifest resolves to `:latest`) |
| `localhost/user-service:latest` | `user-service` Rollout |
| `localhost/user-api-load-test:latest` | `grpc-load-generator` AnalysisTemplate Job |

## Step 4 — Load or publish the images

For k3d, import the local images into the cluster nodes:

```bash
CLUSTER=<your-k3d-cluster-name>

for image in frontend user-api user-service user-api-load-test; do
  podman save "localhost/${image}:latest" -o "/tmp/${image}.tar"
  k3d image import "/tmp/${image}.tar" -c "${CLUSTER}"
done
```

This step is optional if your cluster can pull the same image references from a registry you control. The manifests use `imagePullPolicy: IfNotPresent` and `localhost/...` names, so a local import (or equivalent node-local image) is the workflow this repository expects.

## Step 5 — Create the namespace and shared resources

```bash
kubectl apply -f k8s/base/namespace.yaml
kubectl apply -n app-demo -f k8s/base/storage.yaml
```

`storage.yaml` does not set `metadata.namespace`, so `-n app-demo` is required.

Verify:

```bash
kubectl get ns app-demo -o yaml | grep -A1 'linkerd.io/inject'
kubectl get pvc -n app-demo
```

Expected:

- Namespace `app-demo` with `linkerd.io/inject: enabled`
- PVC `user-service-store`

> **Note:** The PVC is included in `k8s/base/storage.yaml`. The Rollout and Deployments in this repository do not currently mount it.

## Step 6 — Deploy the application components

Apply frontend and user-api Services and Deployments:

```bash
kubectl apply -n app-demo -f k8s/base/services.yaml
kubectl apply -f k8s/base/deployments.yaml
```

Use `-n app-demo` for `services.yaml` because `user-api` has no namespace field (only `frontend` sets `namespace: app-demo`).

**Do not apply** `k8s/base/user-service.yaml` when following the Argo Rollouts path. That file is an alternate static setup with Deployments `user-service-v1` / `user-service-v2`, Services of the same names, and a different `GRPCRoute` weighting those static Services. Applying it conflicts with the Rollout-based Services and route in this tutorial.

Verify:

```bash
kubectl get deploy,svc -n app-demo
```

Expected resources include Deployments `frontend` and `user-api`, and Services `frontend` and `user-api`.

## Step 7 — Deploy the stable and canary Services

```bash
kubectl apply -f k8s/argo-rollouts/services/
```

This creates:

| Service | Purpose |
|---------|---------|
| `user-service` | Parent Service for the `GRPCRoute` (`parentRefs`). Selector is intentionally unset so routing is method-aware via backend Services. |
| `user-service-stable` | Stable backend; Argo Rollouts updates its selector to the stable ReplicaSet. |
| `user-service-canary` | Canary backend; Argo Rollouts updates its selector to the canary ReplicaSet. |

Verify selectors and ports:

```bash
kubectl get svc user-service user-service-stable user-service-canary -n app-demo -o wide
kubectl get svc user-service user-service-stable user-service-canary -n app-demo -o yaml | grep -E 'name:|port:|targetPort:|appProtocol:|app:|rollouts_pod_template_hash|selector:' -A2
```

Expected: all three expose port `50051` with `appProtocol: kubernetes.io/h2c`. Before the Rollout exists, `user-service-stable` and `user-service-canary` select `app: user-service`. After the Rollout runs, Argo adds pod-template-hash selectors.

## Step 8 — Deploy Gateway API resources

Apply in dependency order:

```bash
kubectl apply -f k8s/gateway-api/gatewayclass.yaml
kubectl apply -f k8s/gateway-api/gateway.yaml
kubectl apply -f k8s/gateway-api/httproute.yaml
kubectl apply -f k8s/gateway-api/grpcroute.yaml
```

Inspect status:

```bash
kubectl get gatewayclass envoy-gatewayclass -o yaml
kubectl get gateway app-gateway -n app-demo -o yaml
kubectl get httproute frontend-route -n app-demo -o yaml
kubectl get grpcroute userservice-route -n app-demo -o yaml
```

In this verification context:

- **Accepted** — the parent (`Gateway` or, for this `GRPCRoute`, Service `user-service`) accepted the route attachment.
- **Programmed** — the Gateway implementation (Envoy Gateway) has programmed dataplane config for the Gateway.
- **ResolvedRefs** — backend Service references resolve (for example `frontend`, `user-service-stable`, `user-service-canary`).

## Step 9 — Deploy the AnalysisTemplates

Apply both templates:

```bash
kubectl apply -f k8s/argo-rollouts/analysistemplate-loadtest.yaml
kubectl apply -f k8s/argo-rollouts/analysistemplate-metrics.yaml
```

| Template | What it does | What it proves |
|----------|--------------|----------------|
| `grpc-load-generator` | Runs a one-shot Job using `localhost/user-api-load-test:latest` against `http://user-api.app-demo.svc.cluster.local:8080` with `REQUESTS=100` and `DISTRIBUTION=GetUser=v1:90,v2:10;CreateUser=v1:100` (±`MARGIN=5`) | Observed method-aware version mix matches the `GRPCRoute` expectations |
| `user-service-success-rate` | Queries Linkerd Viz Prometheus for canary `response_total` success ratio (`grpc_status="0"`) over `[1m]`, success when `result[0] >= 0.99` | Canary pods are healthy from Linkerd’s perspective |

Inspect:

```bash
kubectl get analysistemplate -n app-demo
kubectl get analysistemplate grpc-load-generator user-service-success-rate -n app-demo -o yaml
```

## Step 10 — Deploy the Rollout

```bash
kubectl apply -f k8s/argo-rollouts/rollout.yaml
```

Important fields from `k8s/argo-rollouts/rollout.yaml`:

- Name: `user-service`, namespace `app-demo`, `replicas: 1`
- Image: `localhost/user-service:latest`, initial `APP_VERSION=v1`
- Canary services: `stableService: user-service-stable`, `canaryService: user-service-canary`
- Steps, in order:
  1. `setWeight: 50`
  2. `pause` for `5s`
  3. `analysis` with template `grpc-load-generator`
  4. `pause` for `5s`
  5. `analysis` with template `user-service-success-rate` (arg `canary-hash` from `podTemplateHashValue: Latest`)
  6. `pause: {}` (indefinite manual pause before promotion)

### Establish stable, then start the canary (v2)

On first apply, Argo Rollouts typically brings up the initial revision as stable without exercising the full canary analysis path. The load-test template expects **stable = v1** and **canary = v2**, which matches `GRPCRoute` weights for `GetUser`.

After the initial Rollout is healthy with `APP_VERSION=v1`, update the Rollout to start a canary:

```bash
kubectl patch rollout user-service -n app-demo --type='json' -p='[
  {
    "op": "replace",
    "path": "/spec/template/spec/containers/0/env/0/value",
    "value": "v2"
  }
]'
```

That change creates a new pod template (`APP_VERSION=v2`) and starts the canary steps above.

## Step 11 — Verify the complete installation

```bash
kubectl get pods -n app-demo
kubectl get svc -n app-demo
kubectl get gateway -n app-demo
kubectl get httproute -n app-demo
kubectl get grpcroute -n app-demo
kubectl get rollout -n app-demo
kubectl get analysistemplate -n app-demo
```

Expected high-level state:

- Pods for `frontend`, `user-api`, and `user-service` (stable and, during canary, canary) become Ready; Linkerd-injected pods include a `linkerd-proxy` container
- Services: `frontend`, `user-api`, `user-service`, `user-service-stable`, `user-service-canary`
- Gateway `app-gateway`, HTTPRoute `frontend-route`, GRPCRoute `userservice-route`
- Rollout `user-service`
- AnalysisTemplates `grpc-load-generator` and `user-service-success-rate`

## Step 12 — Access the application

The `HTTPRoute` `frontend-route` sends Gateway traffic to Service `frontend:8080`. The Gateway listener is HTTP port `80`.

**Confirmed local access method** (Service from `k8s/base/services.yaml`):

```bash
kubectl port-forward -n app-demo svc/frontend 8080:8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) in a browser.

Optionally inspect whether Envoy Gateway published an address on the Gateway:

```bash
kubectl get gateway app-gateway -n app-demo
kubectl get gateway app-gateway -n app-demo -o jsonpath='{.status.addresses[*].value}{"\n"}'
```

If an address is present in your cluster, you can use that HTTP endpoint on port `80` instead of port-forwarding. This repository does not hardcode a LoadBalancer hostname or NodePort.

## Step 13 — Run the traffic test manually

With the canary active (`APP_VERSION=v2` on the canary ReplicaSet) and the `GRPCRoute` applied, run the load test against `user-api`:

```bash
kubectl port-forward -n app-demo svc/user-api 8080:8080
```

In another terminal, from the repository root:

```bash
API_BASE_URL=http://127.0.0.1:8080 \
REQUESTS=20 \
USER_NAME=test \
THEME=dark \
DISTRIBUTION='GetUser=v1:90,v2:10;CreateUser=v1:100' \
MARGIN=5 \
python3 app/user-api-load-test/main.py
```

Environment variables / flags supported by `app/user-api-load-test/main.py`:

| Variable | Purpose | Default |
|----------|---------|---------|
| `API_BASE_URL` | Base URL for user-api | `http://127.0.0.1` |
| `REQUESTS` | Iterations; each iteration calls `GetUser` and `CreateUser` | `100` |
| `USER_NAME` | Name query/body value | `demo-user` |
| `THEME` | Theme query/body value | `blue` |
| `REQUEST_TIMEOUT` | Per-request timeout seconds | `5` |
| `SLEEP_SECONDS` | Delay between calls | `0` |
| `QUIET` | Compact progress output (`true`/`1`/`yes`) | `false` |
| `DISTRIBUTION` | Expected version mix per method | `GetUser=v1:90,v2:10;CreateUser=v1:100` |
| `MARGIN` | Allowed absolute percentage-point error | `5` |

Expected result when routing matches the `GRPCRoute`:

- **GetUser** — roughly 90% `v1` (stable) and 10% `v2` (canary), within ±5 percentage points
- **CreateUser** — 100% `v1` (stable)
- Process exits `0` and prints `PASS: observed traffic matched the expected distribution within the margin.`

If the canary is not running yet, both methods will show only `v1` and validation fails against the default distribution.

## Step 14 — Watch the rollout

With the plugin:

```bash
kubectl argo rollouts get rollout user-service -n app-demo --watch
```

Without the plugin:

```bash
kubectl get rollout user-service -n app-demo -w
kubectl describe rollout user-service -n app-demo
kubectl get rs -n app-demo -l app=user-service
```

Observe:

- **Stable ReplicaSet** — previous template (`APP_VERSION=v1`)
- **Canary ReplicaSet** — new template (`APP_VERSION=v2`)
- **Current step** — weight step, timed pauses, analysis, then indefinite pause
- **AnalysisRuns** — created for `grpc-load-generator` and `user-service-success-rate`
- **Pause state** — after successful analysis, the Rollout waits at `pause: {}` until promote or abort

## Step 15 — Inspect the AnalysisRuns

List runs and templates:

```bash
kubectl get analysistemplate -n app-demo
kubectl get analysisrun -n app-demo
```

Describe a specific run (replace the name with one from the list; names are generated):

```bash
kubectl describe analysisrun <analysisrun-name> -n app-demo
```

Find the load-test Job and logs:

```bash
kubectl get jobs -n app-demo
kubectl get pods -n app-demo | grep load-test || true
kubectl logs -n app-demo job/<job-name>
```

Example pattern only (actual names include generated suffixes):

```bash
# Example: kubectl logs -n app-demo job/user-service-grpc-load-generator-<hash>
```

Inspect metric results on the AnalysisRun status (success/fail, Prometheus values):

```bash
kubectl get analysisrun -n app-demo -o yaml
```

## Step 16 — Verify Linkerd metrics

Confirm namespace injection and proxies:

```bash
kubectl get ns app-demo -o jsonpath='{.metadata.annotations.linkerd\.io/inject}{"\n"}'
kubectl get pods -n app-demo -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].name}{"\n"}{end}'
```

Expected: annotation `enabled`, and application pods include `linkerd-proxy` (the analysis Job sets `linkerd.io/inject: disabled` by design).

Optional Viz / route views (if your Linkerd CLI is installed):

```bash
linkerd viz stat deploy -n app-demo
linkerd viz routes deploy/user-api -n app-demo
```

Port-forward Linkerd Viz Prometheus:

```bash
kubectl port-forward -n linkerd-viz svc/prometheus 9090:9090
```

Run the PromQL used by `user-service-success-rate` (replace `CANARY_HASH` with the canary ReplicaSet’s `rollouts-pod-template-hash` label):

```bash
CANARY_HASH=<rollouts-pod-template-hash>

curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode "query=sum(rate(response_total{namespace=\"app-demo\",target_port=\"50051\",rollouts_pod_template_hash=\"${CANARY_HASH}\",grpc_status=\"0\"}[1m])) / sum(rate(response_total{namespace=\"app-demo\",target_port=\"50051\",rollouts_pod_template_hash=\"${CANARY_HASH}\"}[1m]))"
```

Find the canary hash:

```bash
kubectl get pods -n app-demo -l app=user-service -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.rollouts-pod-template-hash}{"\t"}{.spec.containers[?(@.name=="user-service")].env[?(@.name=="APP_VERSION")].value}{"\n"}{end}'
```

Generate traffic (Step 13) before expecting non-empty metric results.

## Step 17 — Promote the rollout

When the Rollout is paused at the final manual pause and analysis succeeded:

```bash
kubectl argo rollouts promote user-service -n app-demo
```

After promotion:

- The canary revision becomes the new stable
- `user-service-stable` selects the promoted ReplicaSet
- The previous stable ReplicaSet is scaled down per Rollouts behavior

Verify:

```bash
kubectl argo rollouts get rollout user-service -n app-demo
kubectl get pods -n app-demo -l app=user-service
kubectl get svc user-service-stable user-service-canary -n app-demo -o yaml
```

## Step 18 — Test the failure path

This repository does not ship a dedicated “break the canary” scenario (no failing image tag or forced metric failure manifest).

If an AnalysisRun fails or you need to stop a bad canary:

```bash
kubectl argo rollouts abort user-service -n app-demo
```

Verify:

```bash
kubectl argo rollouts get rollout user-service -n app-demo
kubectl get analysisrun -n app-demo
kubectl describe analysisrun <analysisrun-name> -n app-demo
```

Inspect Job logs for `grpc-load-generator` failures (Step 15) and Prometheus query results for `user-service-success-rate` (Step 16). After abort, Rollouts returns traffic selection to the stable revision according to controller behavior; confirm with `kubectl argo rollouts get rollout user-service -n app-demo` and the stable/canary Service selectors.

## Expected behavior

From `k8s/gateway-api/grpcroute.yaml`:

| RPC method | Stable (`user-service-stable`) | Canary (`user-service-canary`) | Expected version behavior |
|------------|----------------------------------|--------------------------------|-----------------------------|
| `GetUser` | 90% | 10% | Mostly `v1`, some `v2` while canary runs `APP_VERSION=v2` |
| `CreateUser` | 100% | 0% | Always `v1` while stable remains `v1` |
| Default (unmatched) | 100% | 0% | Stable only |

Rollout / analysis sequence from `k8s/argo-rollouts/rollout.yaml` after the `v2` canary starts:

1. `setWeight: 50`
2. Pause `5s`
3. Analysis `grpc-load-generator` (Job load test, expected distribution `GetUser=v1:90,v2:10;CreateUser=v1:100`)
4. Pause `5s`
5. Analysis `user-service-success-rate` (Prometheus success ratio ≥ `0.99`, `failureLimit: 1`, `interval: 30s`, `count: 1`)
6. Indefinite pause until `promote` or `abort`

## Troubleshooting

### ImagePullBackOff

- **Symptom:** Pods stuck with `ImagePullBackOff` / `ErrImageNeverPull` for `localhost/...` images.
- **Probable cause:** Images were not built or not imported into the cluster nodes.
- **Verification:**
  ```bash
  kubectl describe pod -n app-demo <pod-name> | grep -A5 'Events:'
  podman images | grep localhost/
  ```
- **Resolution:** Re-run Steps 3–4 (`podman build` + `k3d image import`), then delete the failing pods so they are recreated:
  ```bash
  kubectl delete pod -n app-demo -l app.kubernetes.io/name=frontend
  kubectl delete pod -n app-demo -l app.kubernetes.io/name=user-api
  kubectl delete pod -n app-demo -l app=user-service
  ```

### GRPCRoute not Accepted

- **Symptom:** `GRPCRoute` `userservice-route` does not show Accepted / ResolvedRefs.
- **Probable cause:** Parent Service missing, backend Services missing, or Gateway API / mesh policy not ready.
- **Verification:**
  ```bash
  kubectl get grpcroute userservice-route -n app-demo -o yaml
  kubectl get grpcroute userservice-route -n app-demo -o jsonpath='{.status.parents[*].conditions[*]}{"\n"}'
  kubectl get svc user-service user-service-stable user-service-canary -n app-demo
  ```
- **Resolution:** Ensure Steps 7–8 completed; fix missing Services; confirm Gateway API CRDs and controller support `GRPCRoute` with Service `parentRefs`.

### Gateway not Programmed

- **Symptom:** Gateway `app-gateway` never becomes Programmed.
- **Probable cause:** Envoy Gateway not installed, or `GatewayClass` controller name mismatch.
- **Verification:**
  ```bash
  kubectl get gatewayclass envoy-gatewayclass -o yaml
  kubectl get gateway app-gateway -n app-demo -o yaml
  kubectl get pods -A | grep -i envoy
  ```
- **Resolution:** Install / repair Envoy Gateway so it reconciles `controllerName: gateway.envoyproxy.io/gatewayclass-controller`. Re-apply `k8s/gateway-api/gatewayclass.yaml` and `gateway.yaml`.

### Linkerd proxy not injected

- **Symptom:** App pods have only the application container; no `linkerd-proxy`.
- **Probable cause:** Namespace annotation missing, or pods created before injection was enabled.
- **Verification:**
  ```bash
  kubectl get ns app-demo -o yaml | grep linkerd.io/inject
  kubectl get pod -n app-demo <pod-name> -o jsonpath='{.spec.containers[*].name}{"\n"}'
  ```
- **Resolution:** Re-apply `k8s/base/namespace.yaml`, then restart workloads:
  ```bash
  kubectl rollout restart deploy/frontend deploy/user-api -n app-demo
  kubectl argo rollouts restart user-service -n app-demo
  ```

### Linkerd metrics return no data

- **Symptom:** Success-rate AnalysisRun fails or PromQL returns empty / `NaN`.
- **Probable cause:** No traffic yet, proxies missing, Prometheus unreachable, wrong `rollouts-pod-template-hash`, or `[1m]` window empty.
- **Verification:**
  ```bash
  kubectl -n linkerd-viz get svc prometheus
  kubectl get pods -n app-demo -l app=user-service --show-labels
  # Generate traffic (Step 13), then re-query Prometheus (Step 16)
  ```
- **Resolution:** Confirm injection (above), generate traffic, use the canary hash from the canary pod labels, and ensure `prometheus.linkerd-viz.svc.cluster.local:9090` is reachable from the cluster.

### Rollout stuck or paused

- **Symptom:** Rollout does not complete.
- **Probable cause:** Expected `pause: {}` after successful analysis, timed `5s` pauses, or a failed AnalysisRun.
- **Verification:**
  ```bash
  kubectl argo rollouts get rollout user-service -n app-demo
  kubectl get analysisrun -n app-demo
  ```
- **Resolution:** If status shows a healthy pause at the final step, promote (Step 17). If analysis failed, inspect AnalysisRuns (Step 15) or abort (Step 18).

### AnalysisRun failed

- **Symptom:** Rollout degrades / aborts analysis; AnalysisRun `Successful` is false.
- **Probable cause:** Distribution mismatch, load-test errors, or success-rate below `0.99`.
- **Verification:**
  ```bash
  kubectl get analysisrun -n app-demo
  kubectl describe analysisrun <analysisrun-name> -n app-demo
  kubectl get jobs -n app-demo
  kubectl logs -n app-demo job/<job-name>
  ```
- **Resolution:** Fix routing/images/proxies, ensure canary is `v2` and stable is `v1`, re-run the canary by updating the Rollout again after abort/undo as appropriate.

### Services do not point to the expected ReplicaSets

- **Symptom:** `GetUser` never hits canary, or `CreateUser` hits canary.
- **Probable cause:** Stable/canary Service selectors not updated, or wrong pods labeled.
- **Verification:**
  ```bash
  kubectl get svc user-service-stable user-service-canary -n app-demo -o yaml
  kubectl get pods -n app-demo -l app=user-service --show-labels
  kubectl get endpointslices -n app-demo | grep user-service
  ```
- **Resolution:** Confirm the Rollout owns the Services (`stableService` / `canaryService` names). Restart or re-apply the Rollout if selectors were manually edited.

## Cleanup

Remove demo resources by deleting the namespace:

```bash
kubectl delete namespace app-demo
```

Optionally delete the GatewayClass created by this repo (cluster-scoped):

```bash
kubectl delete gatewayclass envoy-gatewayclass
```

Remove a local k3d cluster (if you created one for this demo):

```bash
k3d cluster delete <cluster-name>
```

Remove temporary image archives created in Step 4:

```bash
rm -f /tmp/frontend.tar /tmp/user-api.tar /tmp/user-service.tar /tmp/user-api-load-test.tar
```

## Learn more

- [Linkerd](https://linkerd.io/docs/)
- [Linkerd Viz](https://linkerd.io/2/tasks/observability/)
- [Gateway API](https://gateway-api.sigs.k8s.io/)
- [GRPCRoute](https://gateway-api.sigs.k8s.io/api-types/grpcroute/)
- [Envoy Gateway](https://gateway.envoyproxy.io/docs/)
- [Argo Rollouts](https://argo-rollouts.readthedocs.io/)

This demo accompanies the Buoyant blog post:

**[Method-Aware Canary Releases for gRPC with GRPCRoute, Linkerd, and Argo Rollouts](https://buoyant.io/blog/)**

## License

This project is intended to be licensed under the Apache License 2.0. A `LICENSE` file is not present in the repository at the time of writing.
