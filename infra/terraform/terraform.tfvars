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
