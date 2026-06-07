import sys
from types import SimpleNamespace

from agentic_company.platform.runtime_cache import NoopRuntimeCache, runtime_cache_from_env


def test_runtime_cache_defaults_to_noop_without_redis_url(monkeypatch):
    monkeypatch.delenv("AGENTIC_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    cache = runtime_cache_from_env()

    assert isinstance(cache, NoopRuntimeCache)
    assert not cache.stop_requested("run-1")
    cache.publish_run_event("run-1", '{"type":"work_item_updated"}')
    cache.set_stop_requested("run-1")
    assert not cache.stop_requested("run-1")


def test_runtime_cache_uses_redis_client_when_url_is_configured(monkeypatch):
    class FakeRedisClient:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []
            self.values: dict[str, str] = {}

        def publish(self, channel: str, message: str) -> None:
            self.published.append((channel, message))

        def setex(self, key: str, _ttl_seconds: int, value: str) -> None:
            self.values[key] = value

        def exists(self, key: str) -> bool:
            return key in self.values

    fake_client = FakeRedisClient()

    class FakeRedis:
        @staticmethod
        def from_url(url: str, *, decode_responses: bool) -> FakeRedisClient:
            assert url == "redis://127.0.0.1:63799/0"
            assert decode_responses
            return fake_client

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedis))
    monkeypatch.setenv("AGENTIC_REDIS_URL", "redis://127.0.0.1:63799/0")
    monkeypatch.delenv("REDIS_URL", raising=False)

    cache = runtime_cache_from_env()

    cache.publish_run_event("run-1", '{"type":"work_item_updated"}')
    cache.set_stop_requested("run-1", ttl_seconds=10)

    assert fake_client.published == [("events:run:run-1", '{"type":"work_item_updated"}')]
    assert cache.stop_requested("run-1")
