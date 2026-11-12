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
| ERP | Business Central sandbox draft PO testing enabled; standard dispatch disabled |

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

### Sandbox dispatch and recovery

Approval creates a durable **held** handoff, not a Business Central order.
The API database identity must be able to create the handoff, attempt,
reconciliation, and recovery tables at startup. Existing approved proposals
are not automatically backfilled.

Dispatch is a separate, authenticated approver action. It requires valid
Business Central configuration, `BC_WRITES_ENABLED=true` and
`BC_DISPATCH_ENABLED=true`. Standard proposals also require
`BC_STANDARD_DISPATCH_ENABLED=true`; all three flags default to false.
The workflow uses demo data and is not suitable for real orders. 

Set `SANDBOX_PROPOSALS_ENABLED=true` to offer a fixed test proposal. This deployment enables sandbox testing and keeps standard
dispatch disabled. Enabling flags permits eligible approvers to dispatch;
it does not send an order. Review the full Terraform plan and obtain business
approval before changing these settings.

`POST /api/sandbox/verify-target` checks the pinned configuration and confirms
the environment is an active sandbox via the Business Central Admin Center
API. It does not check company existence or purchasing permissions; dispatch
and recovery verify the target again. The verifier uses a **separate** Entra
application with `BC_SANDBOX_VERIFIER_CLIENT_ID` and
`BC_SANDBOX_VERIFIER_CLIENT_SECRET`. It requires the powerful
`AdminCenter.ReadWrite.All` permission and Business Central Admin Center
authorization; protect this identity and do not grant that permission to the
purchasing app. See [Admin Center API authentication](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/administration/administration-center-api).
Create the verifier and purchasing secrets in Key Vault before applying
Terraform; `bc_sandbox_verifier` and `bc_purchasing_secret_name` configure
managed-identity references without storing those secret values in Terraform
state. See [terraform.tfvars.example](./infra/terraform/terraform.tfvars.example).

The dispatcher claims each attempt atomically. An uncertain or interrupted
attempt requires manual investigation in Business Central, including incomplete
headers and lines. **Do not retry or create a replacement until the outcome
is established.** For an eligible sandbox attempt with no known ERP ID, an
approver can record a no-order reconciliation after checking the company;
evidence is visible to queue readers. For an eligible attempt with a known ID,
an approver can complete an existing comment-only sandbox draft. Recovery
verifies the draft and reads it back, never creates a new header or releases,
posts, or deletes the order. The operator must coordinate exclusive access:
the API cannot lock Business Central against other editors. Failed, interrupted,
or ambiguous recovery stays locked and must not be retried automatically.
The queue shows the draft number or a reconciliation warning; a browser-session
marker also prevents a repeat dispatch after an attempted request.

## Authentication

The React dashboard signs users in with Microsoft Entra ID and requests an API
access token using the `access_as_user` scope. FastAPI validates the token's
signature, issuer, audience, and expiry. Authenticated users can read the
approval queue; approval, rejection, sandbox testing, dispatch, and recovery
require the `PurchaseOrder.Approver` application role. Users without that role
receive HTTP 403 for approver actions.

To configure an authenticated deployment:

1. Register an API application, expose the `access_as_user` scope, and define
   the `PurchaseOrder.Approver` app role. Assign the role to approvers in the
   API enterprise application; users may need to sign in again to get a new token.
2. Register a single-page application with the frontend origin as a redirect
   URI and grant it access to the API scope.
3. Configure the API with `AUTH_ENABLED=true`, `ENTRA_TENANT_ID`, and
   `ENTRA_API_CLIENT_ID`. Configure the frontend with `VITE_AUTH_ENABLED=true`,
   `VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_CLIENT_ID`, and `VITE_ENTRA_API_SCOPE`.

The local development profile disables authentication and does not enforce the
approver role; never expose that profile publicly. Business Central uses
separate service-to-service identities and Key Vault secrets, described in
[Business Central (ERP)](#business-central-erp).

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
