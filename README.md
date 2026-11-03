# InventarMind

A runnable starter for a human-supervised retail supply-chain workflow. It includes a React dashboard, a FastAPI API, a LangGraph agent workflow, PostgreSQL persistence, Kafka events, local Docker Compose, and Azure Terraform/Kubernetes deployment starting points.

## Architecture

- **Frontend:** React + TypeScript dashboard and human approval queue. Microsoft Entra ID browser sign-in is enabled when configured.
- **Backend:** FastAPI API for health, approval decisions, and agent runs; PostgreSQL stores approvals and Kafka carries decision events.
- **Agents:** A deterministic LangGraph workflow creates a replenishment proposal. The API puts the proposal in the approval queue; no ERP/OMS writes are performed.
- **Local infrastructure:** PostgreSQL and Redpanda (Kafka API) run alongside the API, frontend, and event consumer.
- **Azure:** Terraform provisions a resource group, Azure Container Apps environment, Azure Database for PostgreSQL Flexible Server, and Event Hubs Kafka endpoint. Kubernetes manifests are deployment templates.

## Run locally

Requirements: Docker Desktop with Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open [http://localhost:5173](http://localhost:5173). The local profile leaves API authentication disabled so the app works without cloud credentials. To use Entra ID locally, set `AUTH_ENABLED=true` and configure the frontend and API values in `.env`; register a single-page application and expose the API scope `access_as_user`.

The API is available at [http://localhost:8000](http://localhost:8000), with OpenAPI docs at `/docs`. The consumer logs inventory and approval events from Kafka. Enter a high-level objective in the dashboard and run the supervisor workflow, then review the resulting proposal in the approval queue. The starter currently runs against demo inventory inputs.

The supervisor uses the configured OpenAI reasoning model when `OPENAI_API_KEY` is set (`ORCHESTRATOR_MODEL=o3`, `ORCHESTRATOR_REASONING_EFFORT=high` by default). Without a key, it uses a deterministic plan and records that choice. It never logs or returns hidden chain-of-thought; only a short plan summary is recorded. Failed agent nodes are retried up to two times before being routed to human review.

## Business Central (ERP)

`agents/tools/business_central.py` is the ERP adapter used by `procurement_vendor.py`. It creates **Draft** purchase orders through the Business Central API v2.0 (`purchaseOrders` and `purchaseOrderLines`) using Entra client-credentials. It is disabled unless `BC_WRITES_ENABLED=true` and the `BC_*` variables are set; it never releases or posts a PO. Register an Entra app, grant it the Business Central API permission, and add it to Business Central (Microsoft Entra Applications page) with a permission set limited to purchase documents. Store `BC_CLIENT_SECRET` in Key Vault, not in `.env`.

## Authentication

For deployments, set `AUTH_ENABLED=true`, `ENTRA_TENANT_ID`, and `ENTRA_API_CLIENT_ID` in the API environment. Configure `VITE_AUTH_ENABLED=true`, `VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_CLIENT_ID`, and `VITE_ENTRA_API_SCOPE` for the frontend. The API validates Entra-issued RS256 access tokens, including issuer and audience. Do not use the local unauthenticated profile in a reachable environment.

## Repository map

```text
frontend/                 React HITL dashboard and approval queue
backend/src/              FastAPI routes, database, Kafka producer and consumer
agents/                   LangGraph state, workflow, nodes, and agent tools
infra/terraform/           Azure resource starting point
infra/k8s/                 Kubernetes manifests
.github/workflows/         Azure Container Apps deployment workflow
```

## Azure deployment

1. Review and apply `infra/terraform` with your Azure subscription, region, and non-empty database administrator credentials.
2. Configure GitHub Actions OIDC federation and repository variables/secrets documented in `.github/workflows/deploy.yml`.
3. Configure the Container Apps API with `AUTH_ENABLED=true` and Entra settings; provide the frontend build-time Entra settings.
4. Set the API database and Kafka connection values to the outputs from your Azure resources.

Terraform provisions platform resources, not application secrets or Entra app registrations. The GitHub workflow is intentionally gated on repository configuration and should be adapted to your organization's environment and image registry policies before production use.

## Development checks

```powershell
python -m pytest backend/tests
npm --prefix frontend install
npm --prefix frontend run build
```

See `.env.example` for local settings. Never commit `.env` or production credentials.
