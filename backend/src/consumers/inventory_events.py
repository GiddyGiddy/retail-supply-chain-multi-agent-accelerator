import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from src.kafka_config import kafka_client_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def consume_events() -> None:
    consumer = AIOKafkaConsumer(
        "inventory-events",
        "approval-decisions",
        **kafka_client_config(),
        group_id="supply-chain-event-consumer",
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
    )
    await consumer.start()
    try:
        async for message in consumer:
            logger.info("Received %s event: %s", message.topic, message.value)
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(consume_events())
