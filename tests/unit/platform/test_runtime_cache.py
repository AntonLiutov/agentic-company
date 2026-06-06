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
