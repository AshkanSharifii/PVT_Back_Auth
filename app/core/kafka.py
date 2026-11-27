from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from aiokafka import AIOKafkaProducer

from app.config import get_settings

logger = logging.getLogger(__name__)
_producer: AIOKafkaProducer | None = None


async def start_kafka() -> None:
    global _producer
    settings = get_settings()
    if not settings.kafka_enabled:
        logger.info("Kafka disabled; auth events will be logged only.")
        return
    try:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await _producer.start()
        logger.info("Kafka producer started.")
    except Exception:
        logger.exception("Kafka unavailable; continuing without event bus.")
        _producer = None


async def stop_kafka() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def publish_auth_event(event_type: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    event = {
        "event_type": event_type,
        "payload": payload,
        "emitted_at": datetime.now(UTC).isoformat(),
        "service": "pvt-back-auth",
    }
    if _producer is None:
        logger.info("auth.event %s %s", event_type, payload)
        return
    try:
        await _producer.send_and_wait(settings.kafka_topic_auth_events, event)
    except Exception:
        logger.exception("Failed to publish auth event %s", event_type)
