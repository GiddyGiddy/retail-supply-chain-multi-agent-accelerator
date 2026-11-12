# Deployment plan

Status: Validated

## Scope

Update the existing InventarMind API Container App configuration using the
repository's Terraform. Do not deploy application code or enable Business
Central writes.

## Context and changes

- Subscription: Azure subscription 1 (`68e4a31c-83b0-4db6-afd3-c0e780b8ac25`).
- Resource group: `rg-inventarmind`; API: `inventarmind-api`.
- Enable sandbox proposals, configure the sandbox target and separate verifier
  app ID, and reference the existing Key Vault verifier secret.
- Preserve the current API image, health probes, HTTP scaling and graceful
  termination settings. Keep `BC_WRITES_ENABLED` and `BC_DISPATCH_ENABLED` false.
- The verifier secret is created outside Terraform; no secret values belong in
  this document or Terraform configuration.

## Validation and deployment

- All validation checks pass:
  - Terraform and Azure CLI installed and authenticated.
  - Terraform initialization, formatting, validation, plan and state checks.
  - Check template variables and any JSON variable files.
  - Review Azure policy and deployment permissions.
- Review a fresh plan for unintended changes; apply only the approved API update.
- Verify the resulting revision, health, configuration and secret reference.
- A live authenticated sandbox verification requires an approver token.

## Risks

Updating the API creates a revision and can briefly affect availability. The
verifier identity has administrative permissions; the configured check uses
only a read-only request. The test handoff remains non-dispatchable.

## Role Assignment Verification

- API identity: Key Vault Secrets User at the Key Vault, AcrPull at the
  registry, and Event Hubs sender/receiver at the namespace.
- Web identity: AcrPull at the registry.
- Deployer: Key Vault Secrets Officer at the Key Vault.
- The new verifier Key Vault reference uses the existing API identity; no role
  assignments are changed by this plan. Business Central Admin Center
  authorization remains separately managed in its tenant.

## Section 7: Validation Proof

- 2026-11-12: Terraform Git Bash preflight passed init, recursive fmt, validate,
  plan, state list and template scan. Optional JSON tfvars check was skipped
  because the file does not exist.
- 2026-11-12: `python -m pytest backend\tests -q`: 88 passed, one existing
  Pydantic/Python 3.14 compatibility warning.
- 2026-11-12: `npm run build` in frontend and Python Ruff correctness lint passed.
- 2026-11-12: Reviewed plan: 0 add, 1 update, 0 destroy; no changes to
  liveness/readiness/startup probes, HTTP scaling or grace period.
