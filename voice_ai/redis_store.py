"""Redis persistence for hospital config, doctors, departments, FAQs and sessions.

Every method degrades gracefully when Redis is unavailable: reads return
defaults/empty collections and writes return a falsy value, so the voice
pipeline keeps working with in-memory sessions and the bundled sample data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import redis

logger = logging.getLogger(__name__)

CONFIG_KEY = "hospital:config"
DEFAULT_SESSION_TTL_MINUTES = 30

CONFIG_JSON_FIELDS = ("supported_languages", "escalation_keywords")
CONFIG_BOOL_FIELDS = ("disclaimer_required", "always_recommend_doctor", "tts_enabled")
CONFIG_INT_FIELDS = ("max_words", "session_timeout_minutes")
DOCTOR_JSON_FIELDS = ("available_days", "languages")
SESSION_JSON_FIELDS = ("symptoms", "context", "history")
SESSION_INT_FIELDS = ("turn_count",)

DEFAULT_CONFIG: dict[str, Any] = {
    "hospital_name": "Apollo Hospital",
    "city": "Bengaluru",
    "emergency_number": "108",
    "helpline": "1860-500-1066",
    "tone": "formal",
    "max_words": 50,
    "primary_language": "en",
    "supported_languages": ["kn", "ta", "te", "hi", "en"],
    "disclaimer_required": True,
    "disclaimer_text": "This is general information only. Please consult a doctor for medical advice.",
    "always_recommend_doctor": True,
    "tts_enabled": True,
    "session_timeout_minutes": DEFAULT_SESSION_TTL_MINUTES,
    "escalation_keywords": [],
}


def serialize_hash(data: dict[str, Any], skip: tuple[str, ...] = ("id",)) -> dict[str, str]:
    """Convert a dict into Redis hash values (JSON for collections, lowercase bools).

    `None` values are dropped instead of being stored as the string "None".
    """
    out: dict[str, str] = {}
    for key, value in data.items():
        if key in skip or value is None:
            continue
        if isinstance(value, (list, dict)):
            out[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            out[key] = str(value).lower()
        else:
            out[key] = str(value)
    return out


def _parse_json_fields(data: dict[str, Any], fields: tuple[str, ...]) -> None:
    for key in fields:
        value = data.get(key)
        if isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                logger.warning("Field %s is not valid JSON; leaving as string", key)


def _parse_int_fields(data: dict[str, Any], fields: tuple[str, ...]) -> None:
    for key in fields:
        value = data.get(key)
        if isinstance(value, str):
            try:
                data[key] = int(value)
            except ValueError:
                logger.warning("Field %s is not an integer: %r", key, value)


class RedisStore:
    """Redis connection and CRUD operations."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        client: redis.Redis | None = None,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.client: redis.Redis | None = client
        self._connected = client is not None

    def connect(self) -> bool:
        try:
            self.client = redis.Redis(
                host=self.host, port=self.port, db=self.db, decode_responses=True, socket_timeout=5
            )
            self.client.ping()
            self._connected = True
            logger.info("Connected to Redis at %s:%s", self.host, self.port)
        except redis.RedisError as exc:
            logger.warning("Redis connection failed (%s); using in-memory fallback.", exc)
            self._connected = False
        return self._connected

    @property
    def is_connected(self) -> bool:
        return self._connected and self.client is not None

    # ------------------------------------------------------------------ config

    @staticmethod
    def default_config() -> dict[str, Any]:
        return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    def get_config(self) -> dict[str, Any]:
        if not self.is_connected:
            return self.default_config()
        try:
            stored = self.client.hgetall(CONFIG_KEY)
        except redis.RedisError:
            logger.exception("Error reading config")
            return self.default_config()

        config = self.default_config()
        config.update(stored)
        _parse_json_fields(config, CONFIG_JSON_FIELDS)
        _parse_int_fields(config, CONFIG_INT_FIELDS)
        for key in CONFIG_BOOL_FIELDS:
            if isinstance(config.get(key), str):
                config[key] = config[key].lower() == "true"
        return config

    def set_config(self, config: dict[str, Any]) -> bool:
        if not self.is_connected:
            return False
        try:
            self.client.hset(CONFIG_KEY, mapping=serialize_hash(config, skip=()))
            return True
        except redis.RedisError:
            logger.exception("Error writing config")
            return False

    def session_ttl_minutes(self) -> int:
        try:
            return int(self.get_config().get("session_timeout_minutes", DEFAULT_SESSION_TTL_MINUTES))
        except (TypeError, ValueError):
            return DEFAULT_SESSION_TTL_MINUTES

    # ------------------------------------------------------- generic entities

    def _list(self, kind: str, json_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        if not self.is_connected:
            return []
        try:
            items = []
            for item_id in sorted(self.client.smembers(f"hospital:{kind}:ids")):
                item = self.client.hgetall(f"hospital:{kind}:{item_id}")
                if item:
                    _parse_json_fields(item, json_fields)
                    item["id"] = item_id
                    items.append(item)
            return items
        except redis.RedisError:
            logger.exception("Error listing %s", kind)
            return []

    def _get(self, kind: str, item_id: str, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
        if not self.is_connected:
            return None
        try:
            item = self.client.hgetall(f"hospital:{kind}:{item_id}")
        except redis.RedisError:
            logger.exception("Error reading %s %s", kind, item_id)
            return None
        if not item:
            return None
        _parse_json_fields(item, json_fields)
        item["id"] = item_id
        return item

    def _add(self, kind: str, prefix: str, data: dict[str, Any]) -> str | None:
        if not self.is_connected:
            return None
        try:
            item_id = data.get("id") or self._next_id(kind, prefix)
            self.client.hset(f"hospital:{kind}:{item_id}", mapping=serialize_hash(data))
            self.client.sadd(f"hospital:{kind}:ids", item_id)
            return item_id
        except redis.RedisError:
            logger.exception("Error adding %s", kind)
            return None

    def _next_id(self, kind: str, prefix: str) -> str:
        """Allocate an unused id, skipping ids that were inserted explicitly (e.g. by seeding)."""
        while True:
            candidate = f"{prefix}{self.client.incr(f'hospital:{kind}:counter'):03d}"
            if not self.client.sismember(f"hospital:{kind}:ids", candidate):
                return candidate

    def _update(self, kind: str, item_id: str, data: dict[str, Any]) -> bool:
        if not self.is_connected:
            return False
        key = f"hospital:{kind}:{item_id}"
        try:
            if not self.client.exists(key):
                return False
            mapping = serialize_hash(data)
            if mapping:
                self.client.hset(key, mapping=mapping)
            return True
        except redis.RedisError:
            logger.exception("Error updating %s %s", kind, item_id)
            return False

    def _delete(self, kind: str, item_id: str) -> bool:
        """Delete an entity. Returns False if it did not exist."""
        if not self.is_connected:
            return False
        try:
            removed = self.client.srem(f"hospital:{kind}:ids", item_id)
            deleted = self.client.delete(f"hospital:{kind}:{item_id}")
            return bool(removed or deleted)
        except redis.RedisError:
            logger.exception("Error deleting %s %s", kind, item_id)
            return False

    # ----------------------------------------------------------------- doctors

    def get_doctors(self) -> list[dict[str, Any]]:
        return self._list("doctors", DOCTOR_JSON_FIELDS)

    def get_doctor(self, doc_id: str) -> dict[str, Any] | None:
        return self._get("doctors", doc_id, DOCTOR_JSON_FIELDS)

    def add_doctor(self, doctor: dict[str, Any]) -> str | None:
        return self._add("doctors", "d", doctor)

    def update_doctor(self, doc_id: str, doctor: dict[str, Any]) -> bool:
        return self._update("doctors", doc_id, doctor)

    def delete_doctor(self, doc_id: str) -> bool:
        return self._delete("doctors", doc_id)

    # ------------------------------------------------------------- departments

    def get_departments(self) -> list[dict[str, Any]]:
        return self._list("departments")

    def add_department(self, department: dict[str, Any]) -> str | None:
        return self._add("departments", "dept", department)

    def delete_department(self, dept_id: str) -> bool:
        return self._delete("departments", dept_id)

    # -------------------------------------------------------------------- FAQs

    def get_faqs(self) -> list[dict[str, Any]]:
        return self._list("faqs")

    def add_faq(self, faq: dict[str, Any]) -> str | None:
        return self._add("faqs", "faq", faq)

    def update_faq(self, faq_id: str, faq: dict[str, Any]) -> bool:
        return self._update("faqs", faq_id, faq)

    def delete_faq(self, faq_id: str) -> bool:
        return self._delete("faqs", faq_id)

    def clear_hospital_data(self) -> None:
        """Remove config, doctors, departments and FAQs (used by the seed script)."""
        if not self.is_connected:
            return
        self.client.delete(CONFIG_KEY)
        for kind in ("doctors", "departments", "faqs"):
            for item_id in self.client.smembers(f"hospital:{kind}:ids"):
                self.client.delete(f"hospital:{kind}:{item_id}")
            self.client.delete(f"hospital:{kind}:ids", f"hospital:{kind}:counter")

    # ---------------------------------------------------------------- sessions

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        if not self.is_connected:
            return None
        try:
            session = self.client.hgetall(f"session:{session_id}")
        except redis.RedisError:
            logger.exception("Error reading session %s", session_id)
            return None
        if not session:
            return None
        _parse_json_fields(session, SESSION_JSON_FIELDS)
        _parse_int_fields(session, SESSION_INT_FIELDS)
        return session

    def set_session(self, session_id: str, data: dict[str, Any], ttl_minutes: int | None = None) -> bool:
        return self._write_session(session_id, data, ttl_minutes)

    def update_session(
        self, session_id: str, updates: dict[str, Any], ttl_minutes: int | None = None
    ) -> bool:
        return self._write_session(session_id, updates, ttl_minutes)

    def _write_session(self, session_id: str, data: dict[str, Any], ttl_minutes: int | None) -> bool:
        if not self.is_connected:
            return False
        ttl = ttl_minutes if ttl_minutes is not None else self.session_ttl_minutes()
        mapping = serialize_hash(data, skip=())
        mapping["updated_at"] = datetime.now().isoformat()
        key = f"session:{session_id}"
        try:
            pipe = self.client.pipeline()
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, ttl * 60)
            pipe.execute()
            return True
        except redis.RedisError:
            logger.exception("Error writing session %s", session_id)
            return False

    def delete_session(self, session_id: str) -> bool:
        if not self.is_connected:
            return False
        try:
            return bool(self.client.delete(f"session:{session_id}"))
        except redis.RedisError:
            logger.exception("Error deleting session %s", session_id)
            return False

    # ----------------------------------------------------------------- helpers

    def search_doctors(self, query: str, language: str = "en") -> list[dict[str, Any]]:
        """Case-insensitive substring search over doctor name/specialization/department."""
        query_lower = query.lower()
        results = []
        for doctor in self.get_doctors():
            fields = (
                doctor.get("name", ""),
                doctor.get(f"name_{language}", ""),
                doctor.get("specialization", ""),
                doctor.get("department", ""),
            )
            if any(query_lower in f.lower() for f in fields if f):
                results.append(doctor)
        return results

    def get_available_doctors(self, day: str | None = None) -> list[dict[str, Any]]:
        day = day or datetime.now().strftime("%A")
        return [d for d in self.get_doctors() if day in d.get("available_days", [])]


_redis_store: RedisStore | None = None


def get_redis() -> RedisStore:
    """Return the process-wide RedisStore, connecting on first use."""
    global _redis_store
    if _redis_store is None:
        from .settings import settings

        _redis_store = RedisStore(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db)
        _redis_store.connect()
    return _redis_store
