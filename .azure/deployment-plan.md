# Deployment plan

## Next change: sandbox-only dispatch enablement

Status: Validated — preparation and validation only.

Prepare and validate a Terraform update to the existing API in subscription
`68e4a31c-83b0-4db6-afd3-c0e780b8ac25`, resource group `rg-inventarmind`,
Sweden Central. Set the global write and dispatch flags and the sandbox dispatch
flag to true, while explicitly keeping standard dispatch false. Retain the
existing image, verifier and purchasing secret references, and all other
configuration. Review the complete Terraform plan before applying.

The deployed image contains the standard-dispatch restriction. An approver can
dispatch any qualifying fixed sandbox test handoff while flags are enabled;
this is not limited to one handoff. Enabling the flags alone does not dispatch
an order. Purchase-order write permissions are not yet proven. Any uncertain
write outcome requires manual reconciliation before retrying.

All validation checks pass:
- Verify Terraform, Azure CLI, authentication, initialization and state access.
- Check formatting and configuration and review a fresh plan.
- Scan template variables and any JSON variable file.
- Verify Azure policy, deployment roles and managed identity Key Vault access.
- Run relevant tests and build checks.

Validation proof: Terraform preflight passed init, formatting, validate, plan,
state access and template scan; optional JSON tfvars was absent. The reviewed
saved plan changes only the API and preserves all four Key Vault references.
It enables the three sandbox/write flags and disables standard dispatch; no
RBAC resources change. Previously checked API identity retains Key Vault Secrets
User at vault scope. Focused backend tests: 86 passed (existing Python 3.14
Pydantic warning); frontend build passed. Subscription policies were previously
inspected; final policy enforcement occurs on apply.

---

Status: Validated

## Scope

Prepare a Terraform-only update to the existing InventarMind API Container App
in subscription `68e4a31c-83b0-4db6-afd3-c0e780b8ac25`, resource group
`rg-inventarmind`, Sweden Central. Reference the existing Key Vault secret
`bc-purchasing-client-secret` using the API's existing user-assigned managed
identity and expose it as `BC_CLIENT_SECRET`. Do not retrieve the secret value.

Keep `BC_WRITES_ENABLED`, `BC_DISPATCH_ENABLED`, and
`BC_SANDBOX_DISPATCH_ENABLED` false. Preserve the running image and existing
Container App settings. This plan authorizes preparation and validation only,
not applying Terraform, deploying, or sending a Business Central order.

## Validation

All validation checks pass:
- Verify Terraform, Azure CLI, authentication, initialization and state access.
- Check formatting and configuration; generate and review a fresh plan.
- Scan template variables and any JSON variable file.
- Verify Azure policy, deployment roles, and managed identity Key Vault access.
- Check build as applicable for this infrastructure-only change.

The secret exists and is enabled. The API identity has Key Vault Secrets User
at the vault scope. Terraform configuration and formatting checks passed.

## Section 7: Validation Proof

- Terraform preflight passed init, formatting, validate, plan, state access and
  template scan; optional JSON tfvars check skipped because the file is absent.
- Read-only plan: one in-place API update, no other changed resources. Planned
  secret references retain database, OpenAI and sandbox verifier references,
  add the purchasing reference and use the existing API managed identity.
- The planned sandbox proposals flag remains true; all three write and dispatch
  flags remain false. The temporary sensitive plan file was deleted.
- The API identity's Key Vault Secrets User assignment exists at vault scope;
  no role assignment changes are planned. Subscription policy assignments were
  inspected; final policy enforcement occurs on apply.
- Focused backend tests: 82 passed, with the existing Python 3.14/Pydantic
  compatibility warning. Frontend production build passed.
