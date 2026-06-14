import json
import os
import sys
import threading
import time
from pathlib import Path

import agentic_company.integrations.commands as commands
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.integrations.codex.runner import (
    _codex_subprocess_env,
    _repo_local_node_bin_dir,
    build_codex_exec_command,
    build_codex_exec_environment,
    codex_reasoning_effort_from_env,
    codex_service_tier_from_env,
    stream_codex_exec_to_log,
)
from agentic_company.platform.run_trace import load_run_events


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
        model="gpt-5.5",
        sandbox="workspace-write",
        target_project_dir=str(target_dir),
        run_dir=run_dir,
        summary_path=run_dir / "summary.md",
        include_host_tool_dirs=True,
    )

    add_dirs = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
    assert str(run_dir) in add_dirs
    assert str(azure_config_dir) in add_dirs
    assert str(docker_config_dir) in add_dirs
    assert str(plugin_dir) in add_dirs


def test_codex_exec_command_omits_host_cli_dirs_by_default(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    run_dir = tmp_path / "run"
    azure_config_dir = home / ".azure"
    for path in (run_dir, azure_config_dir):
        path.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: home)

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
        sandbox="workspace-write",
        target_project_dir=str(run_dir / "generated-project"),
        run_dir=run_dir,
        summary_path=run_dir / "summary.md",
    )

    add_dirs = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
    assert str(run_dir) in add_dirs
    assert str(azure_config_dir) not in add_dirs


def test_workspace_write_command_enables_network(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("AGENTIC_CODEX_WORKSPACE_NETWORK", raising=False)
    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
        force_sandbox=True,
    )

    # QA/handoff/planning run workspace-write and need outbound network.
    assert "sandbox_workspace_write.network_access=true" in " ".join(command)


def test_workspace_network_can_be_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_WORKSPACE_NETWORK", "false")
    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
        force_sandbox=True,
    )

    assert "network_access" not in " ".join(command)


def test_danger_full_access_command_omits_workspace_network(monkeypatch, tmp_path: Path):
    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
        sandbox="danger-full-access",
        target_project_dir=str(tmp_path),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
        force_sandbox=True,
    )

    # danger-full-access already has network; the workspace-write knob is irrelevant.
    assert "network_access" not in " ".join(command)


def test_codex_exec_environment_requires_codex_api_key(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)

    try:
        build_codex_exec_environment(tmp_path)
    except RuntimeError as exc:
        assert "CODEX_API_KEY is required" in str(exc)
    else:
        raise AssertionError("Expected CODEX_API_KEY requirement to fail fast.")


def test_codex_exec_environment_reads_run_local_env(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    runtime_env = run_dir / "delivery" / "agent-runtime.env"
    runtime_env.parent.mkdir(parents=True)
    runtime_env.write_text("CODEX_API_KEY=sk-run-local\n", encoding="utf-8")
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)

    env = build_codex_exec_environment(target_dir)

    assert env["CODEX_API_KEY"] == "sk-run-local"


def test_codex_exec_environment_sets_tool_caches_when_api_key_exists(monkeypatch, tmp_path: Path):
    target_dir = tmp_path / "generated-project"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)

    env = build_codex_exec_environment(target_dir)
    cache_root = tmp_path / ".agentic-cache"

    assert env["CODEX_API_KEY"] == "sk-test"
    assert env["UV_CACHE_DIR"] == str(cache_root / "uv")
    assert env["UV_PROJECT_ENVIRONMENT"] == str(cache_root / "venv")
    assert env["DENO_DIR"] == str(cache_root / "deno")
    assert env["npm_config_cache"] == str(cache_root / "npm")


def test_codex_exec_environment_anchors_relative_browser_paths_to_repo_root(
    monkeypatch, tmp_path: Path
):
    # A relative PLAYWRIGHT_BROWSERS_PATH must not resolve against the worker cwd
    # (the generated project) or Playwright re-installs a ~700 MB browser inside the
    # deliverable. It must anchor to the repo root so the pre-installed runtime is reused.
    from agentic_company.integrations.codex import runner as runner_module

    target_dir = tmp_path / "generated-project"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "ops/qa-runtime/browsers")
    monkeypatch.setenv("NODE_PATH", "ops/qa-runtime/node_modules")

    env = build_codex_exec_environment(target_dir)
    repo_root = Path(runner_module.__file__).resolve().parents[4]

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str((repo_root / "ops/qa-runtime/browsers").resolve())
    assert env["NODE_PATH"] == str((repo_root / "ops/qa-runtime/node_modules").resolve())
    assert str(target_dir) not in env["PLAYWRIGHT_BROWSERS_PATH"]


def test_codex_exec_environment_leaves_absolute_browser_path_untouched(monkeypatch, tmp_path: Path):
    target_dir = tmp_path / "generated-project"
    absolute = tmp_path / "ops" / "qa-runtime" / "browsers"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(absolute))

    env = build_codex_exec_environment(target_dir)

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(absolute)


def test_codex_exec_environment_strips_api_key_for_extension_mode(monkeypatch, tmp_path: Path):
    target_dir = tmp_path / "generated-project"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    monkeypatch.setenv("AGENTIC_CODEX_BINARY_MODE", "extension")

    env = build_codex_exec_environment(target_dir)
    cache_root = tmp_path / ".agentic-cache"

    assert "CODEX_API_KEY" not in env
    assert env["UV_CACHE_DIR"] == str(cache_root / "uv")
    assert env["UV_PROJECT_ENVIRONMENT"] == str(cache_root / "venv")


def test_codex_exec_environment_strips_api_key_for_extension_binary(monkeypatch, tmp_path: Path):
    target_dir = tmp_path / "generated-project"
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    monkeypatch.delenv("AGENTIC_CODEX_BINARY_MODE", raising=False)
    monkeypatch.setenv(
        "CODEX_BINARY",
        str(
            tmp_path
            / ".vscode"
            / "extensions"
            / "openai.chatgpt-test"
            / "bin"
            / "windows-x86_64"
            / "codex.exe"
        ),
    )

    env = build_codex_exec_environment(target_dir)

    assert "CODEX_API_KEY" not in env


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


def test_stream_codex_exec_mirrors_live_agent_messages_to_run_trace(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "codex" / "execution.log"
    raw_events_path = run_dir / "codex" / "events.jsonl"
    script = (
        "import json\n"
        "print(json.dumps({'type':'item.completed',"
        "'item':{'id':'item_0','type':'agent_message','text':'Planning the sprint now.'}}),"
        "flush=True)\n"
        "print(json.dumps({'type':'item.started',"
        "'item':{'id':'item_1','type':'command_execution','command':'Get-Content plan.json'}}),"
        "flush=True)\n"
        "print(json.dumps({'type':'item.completed',"
        "'item':{'id':'item_1','type':'command_execution','command':'Get-Content plan.json',"
        "'status':'completed','exit_code':0}}),flush=True)\n"
    )

    stream_codex_exec_to_log(
        [sys.executable, "-c", script],
        "",
        10,
        log_path,
        raw_events_path,
        env=dict(os.environ),
        codex_execution_id="codex-run-project-manager-agent",
        trace_run_dir=run_dir,
        trace_run_id="run-1",
        trace_agent_id="project-manager-agent",
        trace_work_item_id="PLAN-03",
    )

    trace_events = load_run_events(run_dir)

    assert [event.event_type for event in trace_events] == ["codex_agent_message"]
    assert trace_events[0].message == "Planning the sprint now."
    assert trace_events[0].work_item_id == "PLAN-03"
    assert trace_events[0].agent_id == "project-manager-agent"


def test_stream_codex_exec_mirrors_raw_lines_to_db(tmp_path: Path, monkeypatch):
    repo = _db_repo(tmp_path, monkeypatch)
    user = repo.create_user(
        email="raw-codex@example.test",
        username="raw-codex",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Raw Codex",
        request_text="Build",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-raw-codex",
        run_dir=tmp_path / "run",
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    run_dir = Path(run.run_dir)
    log_path = run_dir / "codex" / "execution.log"
    raw_events_path = run_dir / "codex" / "events.jsonl"
    script = (
        "import json\n"
        "print(json.dumps({'type':'item.completed',"
        "'item':{'id':'item_0','type':'agent_message','text':'Reading requirements.'}}),"
        "flush=True)\n"
        "print('plain progress line', flush=True)\n"
    )

    stream_codex_exec_to_log(
        [sys.executable, "-c", script],
        "",
        10,
        log_path,
        raw_events_path,
        env=dict(os.environ),
        codex_execution_id="codex-raw-1",
        trace_run_dir=run_dir,
        trace_run_id="run-raw-codex",
        trace_agent_id="business-analyst-agent",
        trace_work_item_id="PLAN-01",
    )

    raw_logs = repo.list_raw_log_events(run.id, work_item_id="PLAN-01")

    assert [event.seq for event in raw_logs] == [1, 2]
    assert [event.stream for event in raw_logs] == ["codex-json", "stdout"]
    assert all(event.tool_name == "codex_exec" for event in raw_logs)
    assert all(event.tool_call_id == "codex-raw-1" for event in raw_logs)
    assert "Reading requirements." in raw_logs[0].message
    assert raw_logs[1].message == "plain progress line"


def test_codex_stream_suppresses_duplicate_active_execution_lock(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    lock_dir = run_dir / ".agentic-codex-locks"
    lock_dir.mkdir()
    lock_path = lock_dir / "exec-duplicate.lock"
    lock_path.write_text("active", encoding="utf-8")

    def release_lock() -> None:
        time.sleep(0.1)
        lock_path.unlink()

    thread = threading.Thread(target=release_lock)
    thread.start()
    result = stream_codex_exec_to_log(
        [sys.executable, "-c", "raise SystemExit('should not run')"],
        "",
        2,
        run_dir / "codex" / "execution.log",
        run_dir / "codex" / "events.jsonl",
        env=dict(os.environ),
        codex_execution_id="exec-duplicate",
        trace_run_dir=run_dir,
        trace_run_id="run-1",
        trace_agent_id="qa-agent",
        trace_work_item_id="US-rooms",
    )
    thread.join()

    assert result.returncode == 0
    assert result.stderr == "duplicate_execution_suppressed"
    events = load_run_events(run_dir)
    assert events[0].event_type == "duplicate_execution_suppressed"
    assert events[0].work_item_id == "US-rooms"


def test_codex_stream_terminates_subprocess_when_stop_requested(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminated = {"called": False}
    original_terminate = commands._terminate_process

    def terminate(process):
        terminated["called"] = True
        original_terminate(process)

    monkeypatch.setattr(commands, "_terminate_process", terminate)

    result = stream_codex_exec_to_log(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        "prompt",
        10,
        run_dir / "codex" / "execution.log",
        run_dir / "codex" / "events.jsonl",
        env=dict(os.environ),
        stop_requested=lambda: True,
    )

    assert terminated["called"] is True
    assert result.returncode == 130
    assert result.stderr == "stop_requested"


def test_codex_exec_command_defaults_to_medium_reasoning_and_standard_service_tier(
    monkeypatch, tmp_path: Path
):
    monkeypatch.delenv("AGENTIC_CODEX_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AGENTIC_CODEX_SERVICE_TIER", raising=False)

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
        sandbox="workspace-write",
        target_project_dir=str(tmp_path / "generated-project"),
        run_dir=tmp_path,
        summary_path=tmp_path / "summary.md",
    )

    assert codex_reasoning_effort_from_env() == "medium"
    assert codex_service_tier_from_env() == "standard"
    assert 'model_reasoning_effort="medium"' in command
    assert 'service_tier="fast"' not in command


def test_codex_exec_command_allows_reasoning_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIC_CODEX_REASONING_EFFORT", "xhigh")

    command = build_codex_exec_command(
        codex_binary="codex-test",
        model="gpt-5.5",
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
        model="gpt-5.5",
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
            model="gpt-5.5",
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
        model="gpt-5.5",
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
        model="gpt-5.5",
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


def test_codex_subprocess_env_drops_unrelated_host_secrets(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("RANDOM_COMPANY_TOKEN", "leak-me-too")
    # Platform internals carry the AGENTIC_ prefix but are not worker-facing.
    monkeypatch.setenv("AGENTIC_DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("CODEX_API_KEY", "keep")
    monkeypatch.setenv("AGENTIC_CODEX_SERVICE_TIER", "standard")
    monkeypatch.delenv("AGENTIC_CODEX_ENV_PASSTHROUGH", raising=False)

    env = _codex_subprocess_env()

    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "RANDOM_COMPANY_TOKEN" not in env
    assert "AGENTIC_DATABASE_URL" not in env
    # Allowlisted platform + essential variables survive.
    assert env["CODEX_API_KEY"] == "keep"
    assert env["AGENTIC_CODEX_SERVICE_TIER"] == "standard"
    assert "PATH" in env or "Path" in env


def test_codex_subprocess_env_allows_playwright_browser_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "ops/qa-runtime/browsers")
    monkeypatch.delenv("AGENTIC_CODEX_ENV_PASSTHROUGH", raising=False)

    env = _codex_subprocess_env()

    # QA needs the pre-installed Playwright Chromium location to capture screenshots.
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "ops/qa-runtime/browsers"


def test_codex_subprocess_env_passthrough_escape_hatch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("CUSTOM_DEPLOY_TOKEN", "needed-by-worker")
    monkeypatch.setenv("AGENTIC_CODEX_ENV_PASSTHROUGH", "custom_deploy_token, OTHER_VAR")

    env = _codex_subprocess_env()

    assert env["CUSTOM_DEPLOY_TOKEN"] == "needed-by-worker"


def _db_repo(tmp_path: Path, monkeypatch) -> ConsoleRepository:
    repo = ConsoleRepository()
    repo.init_schema()
    return repo
