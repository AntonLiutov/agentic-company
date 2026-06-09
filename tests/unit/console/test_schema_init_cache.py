from agentic_company.console.web.db import _INITIALIZED_SCHEMA_KEYS, ConsoleRepository


def test_init_schema_is_cached_per_database_and_schema_version(tmp_path, monkeypatch):
    _INITIALIZED_SCHEMA_KEYS.clear()
    calls = {"count": 0}
    original = ConsoleRepository._init_schema_uncached

    def wrapped(self):
        calls["count"] += 1
        return original(self)

    monkeypatch.setattr(ConsoleRepository, "_init_schema_uncached", wrapped)
    repo = ConsoleRepository()

    repo.init_schema()
    repo.init_schema()
    ConsoleRepository().init_schema()

    assert calls["count"] == 1
