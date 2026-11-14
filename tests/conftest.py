import os
import sys
from pathlib import Path

import fakeredis
import pytest

os.environ.setdefault("LOAD_MODELS", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_ai.redis_store import RedisStore  # noqa: E402


@pytest.fixture
def redis_store() -> RedisStore:
    """A RedisStore backed by an in-process fake Redis server."""
    client = fakeredis.FakeRedis(decode_responses=True)
    return RedisStore(client=client)


@pytest.fixture
def offline_store() -> RedisStore:
    """A RedisStore that behaves as if Redis is unreachable."""
    return RedisStore()
