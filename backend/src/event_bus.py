import json
from typing import Any

from aiokafka import AIOKafkaProducer

from src.config.settings import settings


class EventBus:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        if self._producer is None:
            raise RuntimeError("Kafka event bus is not started")
        await self._producer.send_and_wait(topic, event)


event_bus = EventBus()
