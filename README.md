# InventarMind

Human-supervised multi-agent supply-chain replenishment: a React dashboard, a FastAPI API, a LangGraph agent workflow, PostgreSQL, Kafka events, and Azure deployment via Terraform and GitHub Actions.

## Current cloud deployment

InventarMind is deployed to Azure (resource group `rg-inventarmind`, Sweden Central) and managed with Terraform.

| Item | Value |
| --- | --- |
| Web app | https://inventarmind.giddyitsolutions.com (CNAME at name.com, managed certificate) |
| API | `inventarmind-api` Container App, `/health` reports healthy |
| Auth | Microsoft Entra ID (API app + SPA app registration, scope `access_as_user`) |
| Database | Azure Database for PostgreSQL Flexible Server (B1ms, North Europe) |
| Events | Event Hubs Kafka endpoint, Entra-only (SASL OAUTHBEARER via managed identity) |
| Secrets | Key Vault (`database-url`, `openai-api-key`), read by managed identity |
| CI/CD | GitHub Actions with OIDC builds images, pushes to ACR, updates the Container Apps |
| ERP | Business Central Draft POs; writes are **off** (`BC_WRITES_ENABLED=false`) |

## Solution Architecture

![InventarMind solution architecture using Azure icons](./docs/solution-architecture.svg)

### Azure resources

- Container Apps environment `inventarmind-env` with `inventarmind-api` (port 8000) and `inventarmind-web` (port 80)
- Container Registry, Key Vault (RBAC), Log Analytics `inventarmind-logs`
- Event Hubs namespace with `inventory-events` and `approval-decisions` (local auth disabled)
- PostgreSQL Flexible Server, database `supply_chain`
- User-assigned managed identities for the API, web app and GitHub deploy

### Agents

| Node | LLM? | Role |
| --- | --- | --- |
| Orchestrator | Optional (OpenAI `o3`) | Decomposes the objective, routes tasks, retries twice then escalates to a human |
| Demand & Inventory | No | Deterministic stockout deficit = max(0, safety - on_hand) |
| Allocation & Sourcing | Optional | Deterministic transfer/vendor ranking; the LLM can only lower supplier reliability |
| Procurement & Vendor | Optional | Builds a draft proposal and an unsent vendor email; Python verifies optional LLM mapping |

Proposals wait in the approval queue; nothing is written to the ERP without human approval.

### Known gaps

- Approving a vendor proposal records a held PO handoff; automatic dispatch is not enabled.
- The Kafka consumer only logs events; inventory events do not yet feed the workflow.
- Live OpenAI calls are untested; the planner is active only once `openai-api-key` exists in Key Vault.
- Vendor email sending and acknowledgement checks are placeholders.
- `infra/k8s` is an unused alternative to Container Apps.
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

`agents/tools/business_central.py` is the ERP adapter used by the guarded API dispatcher. It creates **Draft** purchase orders through the Business Central API v2.0 (`purchaseOrders` and `purchaseOrderLines`) using Entra client-credentials; it never releases or posts a PO. The procurement node prepares proposals and email text only. It cannot dispatch an ERP order, send email, or check acknowledgements, even if the workflow state says `po_approved`.

For sandbox preparation, create or select a Business Central sandbox and company, and register an Entra app for service-to-service authentication. Grant the appropriate Business Central application permission and admin consent, then register the application in the sandbox's **Microsoft Entra applications** page with a least-privilege permission set. Configure `BC_TENANT_ID`, `BC_CLIENT_ID`, `BC_COMPANY_ID`, `BC_ENVIRONMENT` (the sandbox environment name) and `BC_LOCAL_CURRENCY`; supply `BC_CLIENT_SECRET` through Key Vault rather than source control. See [Microsoft's service-to-service authentication guidance](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/administration/automation-apis-using-s2s-authentication). Setting the environment name alone does not enforce that it is a sandbox: verify the actual target before testing. Keep both write flags disabled until the separate test and approval steps.

## Authentication

When authentication is enabled, approval and rejection decisions require the
`PurchaseOrder.Approver` application role in the API access token. Define this
role on the API app registration, assign it to the appropriate users or groups
in the API enterprise application, and have those users acquire new tokens
before deploying this change. Without the role, decision requests return 403;
reading the queue remains available to authenticated users. Local development
with authentication disabled does not enforce the role. Approval does not yet
automatically dispatch a Business Central purchase order. Vendor approvals create a durable,
held handoff containing the approved proposal, approver, timestamp and a unique
reference in the same database transaction as the decision. A held handoff does
not mean a draft exists in Business Central.
The API creates `purchase_order_dispatches` and `purchase_order_dispatch_attempts` at startup; its
database identity must have permission to create tables. Existing approved
proposals are not automatically backfilled.

The approver-only `POST /api/approvals/{approval_id}/dispatch` endpoint is
disabled by default and requires authentication, an approver role, both `BC_DISPATCH_ENABLED=true` and
`BC_WRITES_ENABLED=true`, plus valid Business Central configuration. Both flags
default to false in the application; enable them only for the approved sandbox
test. The current
workflow uses demo data and is not suitable for real orders. Dispatch of
standard (non-test) proposals additionally requires
`BC_STANDARD_DISPATCH_ENABLED=true`; this flag defaults to false. Explicit
enablement is a behavior change for existing deployments that previously used
the standard dispatch endpoint. Do not enable it for sandbox-only testing.
The dispatcher validates the saved proposal and supports USD or EUR orders below 50,000 in
their respective currency; other currencies or amounts require further review.
The sourcing result must explicitly supply `cost_currency="EUR"` for EUR costs;
missing currency retains the existing USD default. The LLM cannot change this
currency, and the dispatcher verifies the draft currency against the saved
sourcing currency. Configure `BC_LOCAL_CURRENCY` to the actual company's local
currency, and verify that the vendor and Business Central order use the approved
currency before testing. The adapter checks the created header's currency before
adding lines; a mismatch requires reconciliation. A successful request records a
draft order number and ID; it does not release, post, or email the order.
The current dashboard workflow has no input for `cost_currency` or real vendor
quotes, so it cannot yet produce a verified EUR proposal from the sandbox.

For approval-queue testing only, set `SANDBOX_PROPOSALS_ENABLED=true` on the API
and restart it to show the separate sandbox test button on the dashboard. It
creates a clearly labeled pending proposal for vendor `V00010`, item `1000`,
10 units at EUR 5.00 each (EUR 50.00 before tax); this price is a test
assumption, not a vendor quote. Creating it requires the approver role when
authentication is enabled. It bypasses the agent workflow and does not access
Business Central. Approval creates a held handoff, but **sandbox test handoffs
remain unsent** until a separate dispatch request succeeds. Sandbox dispatch
additionally requires `BC_SANDBOX_DISPATCH_ENABLED=true` (default false), an
exact match to the agreed test facts, and a fresh successful sandbox-target
verification. Both global write flags must also be enabled. This deployment's
Terraform configuration enables these three flags for sandbox testing; review
the plan and obtain business approval before applying. Enabling them permits
any qualifying fixed sandbox test handoff to be dispatched by an approver; it
does not send an order by itself. The proposal option is off by default and
does not alter the normal demo workflow.
Terraform also explicitly keeps standard dispatch disabled. Enabling sandbox
dispatch requires first deploying a version of the API that enforces the
standard-dispatch flag; older revisions do not honor it.
For local Docker Compose, add `SANDBOX_PROPOSALS_ENABLED=true` to the local
`.env` file and run `docker compose up --build`; remove it or set it to `false`
to hide the button and disable proposal creation. Local authentication remains
disabled by default; do not use that mode for an exposed deployment.

The approver-only `POST /api/sandbox/verify-target` endpoint performs a read-only
target check without enabling writes. It requires authentication and compares
`BC_TENANT_ID`, `BC_ENVIRONMENT`, and `BC_COMPANY_ID` against the pinned
InventarMind sandbox target in `backend/src/sandbox_target.py`. It then checks
the environment's tenant, name, application family, active status, and actual
`Sandbox` type using the Business Central Admin Center API. The company ID is
compared to the configured target; this check does not query whether the company
exists or test purchase-order permissions.

The check uses a **separate** Entra app in the Business Central tenant, provided
as `BC_SANDBOX_VERIFIER_CLIENT_ID` and `BC_SANDBOX_VERIFIER_CLIENT_SECRET`; use
Key Vault for the secret. Microsoft requires `AdminCenter.ReadWrite.All` and
authorization in the Business Central Admin Center for service-to-service
access. This is a powerful administrative permission even though this endpoint
only performs a GET; protect the verifier identity accordingly. Do not grant it
to the purchase integration app. See [Admin Center API authentication](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/administration/administration-center-api).
Terraform supports this configuration through `sandbox_proposals_enabled` and
the optional `bc_sandbox_verifier` object (see `infra/terraform/terraform.tfvars.example`).
Before applying, create the named verifier client secret in the existing Key Vault
outside Terraform. Terraform configures a versionless Key Vault reference using
the API's managed identity; it never reads or stores this verifier secret's
value in state. The secret must exist and the identity must be able to read it
before the new Container App revision starts. Review the complete Terraform plan
before applying: Terraform manages the API's environment and can remove
portal-only settings. The configured BC target alone is not proof of purchase
order permissions.
The optional `bc_purchasing_secret_name` separately references an existing Key
Vault secret as `BC_CLIENT_SECRET` using the API's managed identity. Create that
secret outside Terraform before applying; only its name and versionless reference,
not its value, are configured by Terraform. Providing this credential does not
enable dispatch or verify purchasing permissions.
Failures return 503 without disclosing credentials. This endpoint is a point-in-time
diagnostic, not an authorization for a later dispatch. Each sandbox dispatch
independently rechecks the target before claiming the handoff, using the same
configuration for verification and the Business Central client. A failed
preflight leaves the handoff held without creating an attempt or sending an order.
The dispatcher atomically claims each handoff once. An error or an interrupted
`in_progress` attempt requires manual reconciliation in Business Central,
including checking for incomplete headers and lines. A timeout may leave a
header without a reference or known ID. Do not retry or create a replacement
until an operator has verified the outcome; there is no automatic retry or
reset endpoint. Email and vendor acknowledgements remain separate, unimplemented steps.
The approval queue offers a separate confirmation and dispatch button only for
approved, held sandbox test handoffs. It shows the resulting draft number or a
reconciliation warning. A browser-session safety marker prevents another click
after an attempted request, even if a refresh still shows `held`; investigate
the outcome before using another session or otherwise retrying.
To run the dashboard's isolated browser tests, run `npm ci`,
`npx playwright install chromium`, and `npm test` from `frontend`. They use
mock API responses and do not send orders to Business Central.

For deployments, set `AUTH_ENABLED=true`, `ENTRA_TENANT_ID`, and `ENTRA_API_CLIENT_ID` in the API environment. Configure `VITE_AUTH_ENABLED=true`, `VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_CLIENT_ID`, and `VITE_ENTRA_API_SCOPE` for the frontend. The API validates Entra-issued RS256 access tokens, including issuer and audience. Do not use the local unauthenticated profile in a reachable environment.

## Repository map

```text
frontend/                 React HITL dashboard and approval queue
backend/src/              FastAPI routes, database, Kafka producer and consumer
agents/                   LangGraph state, workflow, nodes, and agent tools
infra/terraform/          Azure infrastructure (Container Apps, PostgreSQL, Event Hubs, Key Vault)
infra/k8s/                 Kubernetes manifests
.github/workflows/         Image build and Container Apps deploy (OIDC)
docs/                      Solution architecture diagram
```

## Azure deployment

1. **Terraform** (from `infra/terraform`; copy `terraform.tfvars.example` to `terraform.tfvars`). Supply secrets through the environment, never files:

   ```powershell
   $env:TF_VAR_postgres_admin_password = Read-Host -MaskInput "Postgres password"
   $env:TF_VAR_openai_api_key = Read-Host -MaskInput "OpenAI API key"
   terraform init; terraform plan; terraform apply
   ```

   State is local and contains the database password; it is gitignored. Consider a remote backend.
2. **GitHub Actions**: the deploy identity uses OIDC federation. Set the secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and the variables listed in `.github/workflows/deploy.yml`. Pushes to `main` build and roll out new images only; Terraform changes are applied manually.
3. **Custom domain** (frontend only): at the DNS host add `CNAME inventarmind` -> the web Container App FQDN and `TXT asuid.inventarmind` -> the environment verification ID, then run `az containerapp hostname add` and `az containerapp hostname bind` for a managed certificate. Add the new origin to `CORS_ORIGINS` and the SPA redirect URIs in Entra.
## Development checks

```powershell
python -m pytest backend/tests
npm --prefix frontend install
npm --prefix frontend run build
```

See `.env.example` for local settings. Never commit `.env` or production credentials.
