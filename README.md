# grpc-method-aware-canary-demo

Demonstration of **method-aware canary releases for gRPC** using Gateway API `GRPCRoute`, Linkerd, and Argo Rollouts.

This repository shows how to route gRPC traffic by method during a progressive delivery rollout. `GetUser` can be split between stable and canary backends, while `CreateUser` stays on stable until the team is ready to promote.

## Architecture

```
Browser
  └── frontend (HTTP)
        └── user-api (HTTP → gRPC)
              └── user-service (gRPC)
                    ├── stable (v1)
                    └── canary (v2)
```

| Component | Role |
|-----------|------|
| **frontend** | Web UI that calls `user-api` over HTTP |
| **user-api** | HTTP API that proxies requests to `user-service` over gRPC |
| **user-service** | gRPC service exposing `GetUser`, `CreateUser`, and `ListUsers` |
| **Linkerd** | Service mesh; provides mTLS and Prometheus metrics used during analysis |
| **Gateway API** | Routes HTTP traffic to the frontend and gRPC traffic by method via `GRPCRoute` |
| **Argo Rollouts** | Manages the canary rollout, traffic weights, and automated analysis |

During the demo, `GRPCRoute` sends most `GetUser` traffic to the canary and keeps `CreateUser` on stable. A Job-based load test validates that behavior before promotion.

## Repository structure

```
.
├── app/
│   ├── frontend/            # Demo web UI
│   ├── user-api/            # HTTP API and gRPC client
│   ├── user-api-load-test/  # Load test used by Argo Rollouts analysis
│   └── user-service/        # gRPC user service (stable/canary target)
└── k8s/
    ├── base/                # Namespace, storage, services, and app deployments
    ├── gateway-api/         # Gateway, HTTPRoute, and method-aware GRPCRoute
    └── argo-rollouts/       # Rollout, canary services, and AnalysisTemplates
```

## Prerequisites

- Kubernetes cluster (local or remote)
- [k3d](https://k3d.io/) (recommended for local development)
- Envoy Gateway
- Linkerd
- Linkerd Viz
- Argo Rollouts
- kubectl
- Podman

Install the platform components using their official documentation before deploying this demo.

## Quick start

### 1. Build images

From the repository root:

```bash
podman build -t localhost/frontend:latest ./app/frontend
podman build -t localhost/user-api:latest ./app/user-api
podman build -t localhost/user-service:latest ./app/user-service
podman build -t localhost/user-api-load-test:latest ./app/user-api-load-test
```

If you use k3d, import the images into your cluster:

```bash
CLUSTER=<your-k3d-cluster-name>

for image in frontend user-api user-service user-api-load-test; do
  podman save localhost/${image}:latest -o /tmp/${image}.tar
  k3d image import /tmp/${image}.tar -c "${CLUSTER}"
done
```

### 2. Deploy the demo

```bash
kubectl apply -f k8s/base/namespace.yaml
kubectl apply -f k8s/base/storage.yaml
kubectl apply -f k8s/base/services.yaml
kubectl apply -f k8s/base/deployments.yaml

kubectl apply -f k8s/argo-rollouts/services/
kubectl apply -f k8s/argo-rollouts/rollout.yaml
kubectl apply -f k8s/argo-rollouts/analysistemplate-loadtest.yaml
kubectl apply -f k8s/argo-rollouts/analysistemplate-metrics.yaml

kubectl apply -f k8s/gateway-api/gatewayclass.yaml
kubectl apply -f k8s/gateway-api/gateway.yaml
kubectl apply -f k8s/gateway-api/httproute.yaml
kubectl apply -f k8s/gateway-api/grpcroute.yaml
```

Do not apply `k8s/base/user-service.yaml` when using the Argo Rollouts path. That manifest is an alternate static setup for earlier experiments.

### 3. Watch the rollout

```bash
kubectl argo rollouts get rollout user-service -n app-demo --watch
```

The rollout runs a load test and queries Linkerd metrics from Prometheus before pausing for manual promotion.

### 4. Run the load test locally (optional)

```bash
kubectl port-forward -n app-demo svc/user-api 8080:8080

API_BASE_URL=http://127.0.0.1:8080 \
REQUESTS=20 \
USER_NAME=test \
THEME=dark \
python3 app/user-api-load-test/main.py
```

A successful run reports that `GetUser` may hit stable or canary, and that `CreateUser` stayed on stable.

## Cleanup

Remove the demo namespace:

```bash
kubectl delete namespace app-demo
```

To remove a local k3d cluster:

```bash
k3d cluster delete <cluster-name>
```

## Article

This demo accompanies the Buoyant blog post:

**[Method-Aware Canary Releases for gRPC with GRPCRoute, Linkerd, and Argo Rollouts](https://buoyant.io/blog/)**

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
