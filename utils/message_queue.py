"""
utils/message_queue.py — Inter-agent message passing.

Primary backend: Redis pub/sub
Fallback: In-memory queue.Queue (if Redis unavailable)

All messages are JSON-serialized dicts with a TTL envelope.
"""

import json
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

from config.settings import REDIS
from config.constants import MESSAGE_TTL_SECONDS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# In-memory fallback store
# ──────────────────────────────────────────────
_in_memory_queues: Dict[str, queue.Queue] = {}
_in_memory_lock = threading.Lock()


def _get_in_memory_queue(channel: str) -> queue.Queue:
    with _in_memory_lock:
        if channel not in _in_memory_queues:
            _in_memory_queues[channel] = queue.Queue(maxsize=100)
        return _in_memory_queues[channel]


# ──────────────────────────────────────────────
# Redis backend
# ──────────────────────────────────────────────
_redis_client = None
_redis_available = False


def _init_redis() -> bool:
    """Attempt to connect to Redis. Returns True if successful."""
    global _redis_client, _redis_available
    try:
        import redis as redis_lib
        client = redis_lib.Redis(
            host=REDIS.HOST,
            port=REDIS.PORT,
            password=REDIS.PASSWORD or None,
            db=REDIS.DB,
            socket_timeout=5,
            decode_responses=True,
        )
        client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("Redis message queue connected at %s:%d", REDIS.HOST, REDIS.PORT)
        return True
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — falling back to in-memory queue", exc)
        _redis_available = False
        return False


def init_queue() -> None:
    """Initialize the message queue backend. Call once at startup."""
    _init_redis()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def publish(channel: str, payload: Dict[str, Any]) -> bool:
    """
    Publish a message to a channel.

    Args:
        channel: One of the CHANNEL_* constants in config/constants.py
        payload: Serializable dict

    Returns:
        True if published successfully
    """
    envelope = {
        "channel": channel,
        "payload": payload,
        "timestamp": time.time(),
        "expires_at": time.time() + MESSAGE_TTL_SECONDS,
    }
    message = json.dumps(envelope)

    if _redis_available and _redis_client:
        try:
            _redis_client.publish(channel, message)
            # Also push to list for persistent retrieval (not just pub/sub)
            _redis_client.rpush(f"queue:{channel}", message)
            _redis_client.expire(f"queue:{channel}", MESSAGE_TTL_SECONDS)
            logger.debug("Published to Redis channel '%s'", channel)
            return True
        except Exception as exc:
            logger.warning("Redis publish failed (%s), falling back to memory", exc)

    # In-memory fallback
    q = _get_in_memory_queue(channel)
    try:
        q.put_nowait(envelope)
        logger.debug("Published to in-memory channel '%s'", channel)
        return True
    except queue.Full:
        logger.error("In-memory queue full for channel '%s'", channel)
        return False


def get_message(channel: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    Get the next message from a channel (blocking up to timeout seconds).

    Returns:
        The payload dict, or None if queue is empty / message expired.
    """
    if _redis_available and _redis_client:
        try:
            # BLPOP with timeout
            result = _redis_client.blpop(f"queue:{channel}", timeout=int(timeout))
            if result:
                _, raw = result
                envelope = json.loads(raw)
                if envelope.get("expires_at", 0) < time.time():
                    logger.debug("Discarding expired message on channel '%s'", channel)
                    return None
                return envelope.get("payload")
        except Exception as exc:
            logger.warning("Redis get_message failed (%s), falling back to memory", exc)

    # In-memory fallback
    q = _get_in_memory_queue(channel)
    try:
        envelope = q.get(timeout=timeout)
        if envelope.get("expires_at", 0) < time.time():
            return None
        return envelope.get("payload")
    except queue.Empty:
        return None


def get_all_messages(channel: str, max_count: int = 100) -> list:
    """
    Drain all pending messages from a channel (non-blocking).

    Returns:
        List of payload dicts
    """
    messages = []
    for _ in range(max_count):
        msg = get_message(channel, timeout=0.1)
        if msg is None:
            break
        messages.append(msg)
    return messages


def peek_latest(channel: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent message without removing it (Redis only).
    Falls back to None for in-memory backend.
    """
    if _redis_available and _redis_client:
        try:
            raw = _redis_client.lindex(f"queue:{channel}", -1)
            if raw:
                envelope = json.loads(raw)
                return envelope.get("payload")
        except Exception as exc:
            logger.warning("Redis peek_latest error: %s", exc)
    return None


def queue_size(channel: str) -> int:
    """Return number of pending messages in the channel."""
    if _redis_available and _redis_client:
        try:
            return _redis_client.llen(f"queue:{channel}")
        except Exception:
            pass
    q = _get_in_memory_queue(channel)
    return q.qsize()


def clear_channel(channel: str) -> None:
    """Remove all pending messages from a channel."""
    if _redis_available and _redis_client:
        try:
            _redis_client.delete(f"queue:{channel}")
        except Exception:
            pass
    with _in_memory_lock:
        _in_memory_queues[channel] = queue.Queue(maxsize=100)
    logger.debug("Cleared channel '%s'", channel)
