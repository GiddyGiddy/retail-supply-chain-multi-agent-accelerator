import os
from dataclasses import dataclass
from uuid import UUID

import httpx

from agents.tools.business_central import API_HOST, SCOPE, TOKEN_URL, BusinessCentralConfig

EXPECTED_TENANT_ID = "8d132419-f288-4bcb-b3b9-42e1ffc6d824"
EXPECTED_ENVIRONMENT = "InventarMind"
EXPECTED_COMPANY_ID = "6ecc6e3d-f6bb-f111-85be-7c1e52401c43"
ADMIN_ENVIRONMENT_URL = (
    f"{API_HOST}/admin/v2.29/applications/BusinessCentral/environments/{EXPECTED_ENVIRONMENT}"
)


class SandboxVerificationError(RuntimeError):
    """A sandbox target could not be verified safely."""


@dataclass(frozen=True)
class SandboxVerifierCredentials:
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> "SandboxVerifierCredentials":
        return cls(
            client_id=os.getenv("BC_SANDBOX_VERIFIER_CLIENT_ID", ""),
            client_secret=os.getenv("BC_SANDBOX_VERIFIER_CLIENT_SECRET", ""),
        )


def _uuid_matches(value: object, expected: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return UUID(value) == UUID(expected)
    except ValueError:
        return False


def verify_sandbox_target(
    config: BusinessCentralConfig,
    credentials: SandboxVerifierCredentials,
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    if (
        not _uuid_matches(config.tenant_id, EXPECTED_TENANT_ID)
        or config.environment != EXPECTED_ENVIRONMENT
        or not _uuid_matches(config.company_id, EXPECTED_COMPANY_ID)
    ):
        raise SandboxVerificationError("Configured Business Central target does not match the approved sandbox target")
    if (
        not credentials.client_id
        or not credentials.client_secret
        or not _uuid_matches(credentials.client_id, credentials.client_id)
        or not _uuid_matches(config.client_id, config.client_id)
        or _uuid_matches(credentials.client_id, config.client_id)
    ):
        raise SandboxVerificationError("A separate sandbox verifier identity and Business Central client ID are required")

    try:
        with httpx.Client(transport=transport, timeout=15.0, follow_redirects=False) as client:
            response = client.post(
                TOKEN_URL.format(tenant=EXPECTED_TENANT_ID),
                data={
                    "grant_type": "client_credentials",
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "scope": SCOPE,
                },
            )
            response.raise_for_status()
            token_body = response.json()
            token = token_body.get("access_token") if isinstance(token_body, dict) else None
            if not isinstance(token, str) or not token:
                raise SandboxVerificationError("Sandbox verifier did not receive an access token")
            response = client.get(ADMIN_ENVIRONMENT_URL, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise SandboxVerificationError("Could not verify the environment with Business Central Admin Center") from error

    environments = body.get("value") if isinstance(body, dict) else None
    if not isinstance(environments, list) or len(environments) != 1 or not isinstance(environments[0], dict):
        raise SandboxVerificationError("Business Central Admin Center returned an unexpected environment response")
    environment = environments[0]
    if (
        environment.get("name") != EXPECTED_ENVIRONMENT
        or environment.get("applicationFamily") != "BusinessCentral"
        or not _uuid_matches(environment.get("aadTenantId"), EXPECTED_TENANT_ID)
        or environment.get("type") != "Sandbox"
        or environment.get("status") != "Active"
    ):
        raise SandboxVerificationError("Business Central did not confirm the expected active sandbox environment")
