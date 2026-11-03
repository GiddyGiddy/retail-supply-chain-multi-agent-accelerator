import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://supply_chain:supply_chain@localhost:5432/supply_chain",
    )
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")
    auth_enabled: bool = _as_bool(os.getenv("AUTH_ENABLED", "false"))
    entra_tenant_id: str = os.getenv("ENTRA_TENANT_ID", "")
    entra_api_client_id: str = os.getenv("ENTRA_API_CLIENT_ID", "")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    )


settings = Settings()
