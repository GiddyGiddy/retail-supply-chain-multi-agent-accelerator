import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer

from src.kafka_config import kafka_client_config

logger = logging.getLogger(__name__)

START_TIMEOUT_SECONDS = 20


class EventBus:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Connect to Kafka. A broker outage must not take the API down, so failures are logged."""
        producer: AIOKafkaProducer | None = None
        try:
            producer = AIOKafkaProducer(
                **kafka_client_config(),
                value_serializer=lambda value: json.dumps(value).encode("utf-8"),
            )
            await asyncio.wait_for(producer.start(), timeout=START_TIMEOUT_SECONDS)
        except Exception:
            logger.exception("Kafka event bus unavailable; events will not be published")
            if producer is not None:
                try:
                    await producer.stop()
                except Exception:
                    pass
            return
        self._producer = producer

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        if self._producer is None:
            logger.warning("Kafka event bus not connected; dropping event for topic %s", topic)
            return
        await self._producer.send_and_wait(topic, event)


event_bus = EventBus()
