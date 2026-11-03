import asyncio

import pytest

from src.kafka_config import EntraTokenProvider, kafka_client_config


def test_local_default_is_plaintext(monkeypatch) -> None:
    monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)
    config = kafka_client_config()
    assert config["security_protocol"] == "PLAINTEXT" and "sasl_mechanism" not in config


def test_event_hubs_uses_entra_oauthbearer_scoped_to_namespace(monkeypatch) -> None:
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "OAUTHBEARER")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "ns.servicebus.windows.net:9093")
    config = kafka_client_config()
    provider = config["sasl_oauth_token_provider"]
    assert isinstance(provider, EntraTokenProvider)
    assert provider._scope == "https://ns.servicebus.windows.net/.default"
    asyncio.run(provider._credential.close())


def test_plain_requires_credentials_and_unknown_modes_fail(monkeypatch) -> None:
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
    monkeypatch.delenv("KAFKA_SASL_USERNAME", raising=False)
    with pytest.raises(ValueError):
        kafka_client_config()
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SSL")
    with pytest.raises(ValueError):
        kafka_client_config()
