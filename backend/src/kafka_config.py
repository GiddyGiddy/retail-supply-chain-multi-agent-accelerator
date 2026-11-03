import os
import ssl
from typing import Any

from aiokafka.abc import AbstractTokenProvider


class EntraTokenProvider(AbstractTokenProvider):
    """Supplies Microsoft Entra tokens (managed identity in Azure) for Event Hubs' Kafka endpoint."""

    def __init__(self, scope: str) -> None:
        # Imported lazily so local development without azure-identity still works.
        from azure.identity.aio import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        self._scope = scope

    async def token(self) -> str:
        return (await self._credential.get_token(self._scope)).token


def kafka_client_config() -> dict[str, Any]:
    """Connection settings shared by the Kafka producer and consumer.

    KAFKA_SECURITY_PROTOCOL: PLAINTEXT (local default) or SASL_SSL.
    KAFKA_SASL_MECHANISM:    OAUTHBEARER (Entra/managed identity) or PLAIN (username/password).
    """
    protocol = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper()
    config: dict[str, Any] = {
        "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092"),
        "security_protocol": protocol,
    }
    if protocol == "PLAINTEXT":
        return config
    if protocol != "SASL_SSL":
        raise ValueError("KAFKA_SECURITY_PROTOCOL must be PLAINTEXT or SASL_SSL")

    mechanism = os.getenv("KAFKA_SASL_MECHANISM", "OAUTHBEARER").upper()
    config["sasl_mechanism"] = mechanism
    config["ssl_context"] = ssl.create_default_context()
    if mechanism == "OAUTHBEARER":
        host = config["bootstrap_servers"].split(",")[0].rsplit(":", 1)[0]
        config["sasl_oauth_token_provider"] = EntraTokenProvider(f"https://{host}/.default")
    elif mechanism == "PLAIN":
        username, password = os.getenv("KAFKA_SASL_USERNAME"), os.getenv("KAFKA_SASL_PASSWORD")
        if not username or not password:
            raise ValueError("KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD are required for PLAIN")
        config["sasl_plain_username"] = username
        config["sasl_plain_password"] = password
    else:
        raise ValueError("KAFKA_SASL_MECHANISM must be OAUTHBEARER or PLAIN")
    return config
