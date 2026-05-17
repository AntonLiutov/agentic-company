import json
import os
from pathlib import Path

from agentic_company.integrations.codex.runner import (
    _codex_subprocess_env,
    _repo_local_node_bin_dir,
    build_codex_exec_command,
    build_codex_exec_environment,
    codex_reasoning_effort_from_env,
    codex_service_tier_from_env,
)


def test_codex_subprocess_env_exposes_azure_and_docker_plugins(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    docker_config_dir = home / ".docker"
    azure_config_dir = home / ".azure"
    rancher_plugins = tmp_path / "rancher" / "docker-cli-plugins"
    config_plugin_dir = tmp_path / "configured" / "plugins"
    docker_config_dir.mkdir(parents=True)
    azure_config_dir.mkdir()
    rancher_plugins.mkdir(parents=True)
    config_plugin_dir.mkdir(parents=True)
    (docker_config_dir / "config.json").write_text(
        json.dumps({"cliPluginsExtraDirs": [str(config_plugin_dir)]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "rancher"))
    monkeypatch.delenv("AZURE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DOCKER_CLI_PLUGIN_EXTRA_DIRS", raising=False)

    env = _codex_subprocess_env()

    assert env["AZURE_CONFIG_DIR"] == str(azure_config_dir)
    assert str(config_plugin_dir) in env["DOCKER_CLI_PLUGIN_EXTRA_DIRS"]


def test_codex_exec_command_allows_host_cli_config_dirs(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    azure_config_dir = home / ".azure"
    docker_config_dir = home / ".docker"
    plugin_dir = tmp_path / "plugins"
    for path in (run_dir, target_dir, azure_config_dir, docker_config_dir, plugin_dir):
        path.mkdir(parents=True)
    (docker_config_dir / "config.json").write_text(
        json.dumps({"cliPluginsExtraDirs": [str(plugin_dir)]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="workspace-write",
        target_project_dir=str(target_dir),
        run_dir=run_dir,
        summary_path=run_dir / "summary.md",
    )

    add_dirs = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
    assert str(run_dir) in add_dirs
    assert str(azure_config_dir) in add_dirs
    assert str(docker_config_dir) in add_dirs
    assert str(plugin_dir) in add_dirs


def test_codex_exec_environment_requires_codex_api_key(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    try:
        build_codex_exec_environment(tmp_path)
    except RuntimeError as exc:
        assert "CODEX_API_KEY is required" in str(exc)
    else:
        raise AssertionError("Expected CODEX_API_KEY requirement to fail fast.")


def test_codex_exec_environment_sets_tool_caches_when_api_key_exists(monkeypatch, tmp_path: Path):
    target_dir = tmp_path / "generated-project"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")

    env = build_codex_exec_environment(target_dir)

    assert env["CODEX_API_KEY"] == "sk-test"
    assert env["UV_CACHE_DIR"] == str(target_dir / ".uv-cache")
    assert env["DENO_DIR"] == str(target_dir / ".deno-cache")
    assert env["npm_config_cache"] == str(target_dir / ".npm-cache")


def test_repo_local_node_bin_dir_detects_portable_node(tmp_path: Path):
    node_root = tmp_path / "ops" / "codex-npm-smoke" / ".tools" / "node"
    if os.name == "nt":
        node_dir = node_root / "node-v24.15.0-win-x64"
        node_dir.mkdir(parents=True)
        (node_dir / "node.exe").write_text("", encoding="utf-8")
        expected = node_dir
    else:
        node_dir = node_root / "node-v24.15.0-linux-x64" / "bin"
        node_dir.mkdir(parents=True)
        (node_dir / "node").write_text("", encoding="utf-8")
        expected = node_dir

    assert _repo_local_node_bin_dir(tmp_path) == expected


def test_codex_exec_command_defaults_to_medium_reasoning_and_fast_service_tier(
    monkeypatch, tmp_path: Path
):
    monkeypatch.delenv("AGENTIC_CODEX_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AGENTIC_CODEX_SERVICE_TIER", raising=False)

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path / "generated-project"),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
    )

    assert codex_reasoning_effort_from_env() == "medium"
    assert codex_service_tier_from_env() == "fast"
    assert 'model_reasoning_effort="medium"' in command
    assert 'service_tier="fast"' in command


def test_codex_exec_command_allows_reasoning_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_REASONING_EFFORT", "xhigh")

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path / "generated-project"),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
    )

    assert 'model_reasoning_effort="xhigh"' in command


def test_codex_exec_command_allows_standard_service_tier(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_SERVICE_TIER", "standard")

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path / "generated-project"),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
    )

    assert codex_service_tier_from_env() == "standard"
    assert 'service_tier="fast"' not in command


def test_codex_exec_command_rejects_invalid_service_tier(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_SERVICE_TIER", "turbo")

    try:
        build_codex_exec_command(
            codex_binary="codex-test",
            model="gpt-5.3-codex",
            sandbox="workspace-write",
            target_project_dir=str(tmp_path / "generated-project"),
            run_dir=tmp_path,
            summary_path=tmp_path / "summary.md",
        )
    except ValueError as exc:
        assert "AGENTIC_CODEX_SERVICE_TIER must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid service tier to fail fast.")


def test_codex_exec_command_can_force_read_only_sandbox(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_SANDBOX", "danger-full-access")

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="read-only",
        target_project_dir=str(tmp_path),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
        force_sandbox=True,
    )

    assert command[command.index("--sandbox") + 1] == "read-only"


def test_codex_exec_command_can_resume_existing_session(tmp_path: Path):
    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.3-codex",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path / "generated-project"),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
        resume_session_id="thread-existing",
    )

    resume_index = command.index("resume")

    assert command[resume_index : resume_index + 3] == ["resume", "thread-existing", "-"]
    assert command.index("--output-last-message") < resume_index
    assert command[-3:] == ["resume", "thread-existing", "-"]
