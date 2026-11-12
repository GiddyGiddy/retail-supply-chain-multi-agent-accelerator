subscription_id         = "68e4a31c-83b0-4db6-afd3-c0e780b8ac25"
resource_group_name     = "rg-inventarmind"
name_prefix             = "inventarmind"
postgres_admin_username = "supplyadmin"

# Secrets are never stored here. Set them in your shell instead:
#   $env:TF_VAR_postgres_admin_password = "<strong password>"
#   $env:TF_VAR_openai_api_key          = "<optional>"

# Filled in after the Entra app registrations exist (step 5):
# entra_tenant_id     = "fd58bbca-b54d-436b-a7bc-3cb0e2c1181a"
# entra_api_client_id = "<api app client id>"

entra_tenant_id     = "fd58bbca-b54d-436b-a7bc-3cb0e2c1181a"
entra_api_client_id = "4ea32a70-966a-48bb-bc85-35b908e18a24"

web_custom_domain = "inventarmind.giddyitsolutions.com"

sandbox_proposals_enabled = true
bc_purchasing_secret_name = "bc-purchasing-client-secret"
bc_sandbox_verifier = {
  tenant_id            = "8d132419-f288-4bcb-b3b9-42e1ffc6d824"
  environment          = "InventarMind"
  company_id           = "6ecc6e3d-f6bb-f111-85be-7c1e52401c43"
  purchasing_client_id = "f704e9fb-c57c-455d-a4e0-fb17a61ffe55"
  verifier_client_id   = "a254c52e-26b4-488e-bc55-35ee420f4c7e"
  secret_name          = "bc-sandbox-verifier-client-secret"
  local_currency       = "EUR"
}
