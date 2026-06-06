from agentic_company.console.support import (
    clear_console_runs,
    create_console_run,
    initial_env_value,
    list_sample_requirements,
    load_sample_requirements,
    missing_required_env_keys,
    read_required_configuration,
    saved_env_keys,
    write_target_env,
)


def test_console_run_writes_requirements_without_retired_execution_request(tmp_path):
    run_dir = create_console_run("Project type: UI/web app\nBuild a chat app.", tmp_path / "runs")

    assert (run_dir / "00-requirements.md").read_text(encoding="utf-8").startswith(
        "Project type: UI/web app"
    )
    assert not (run_dir / "delivery" / "execution-request.json").exists()


def test_console_support_lists_and_loads_sample_requirements(tmp_path):
    requirements_dir = tmp_path / "examples" / "requirements"
    requirements_dir.mkdir(parents=True)
    (requirements_dir / "b-sample.md").write_text("Project name: B\n", encoding="utf-8")
    (requirements_dir / "a-sample.md").write_text("Project name: A\n", encoding="utf-8")

    samples = list_sample_requirements(tmp_path)

    assert [sample.name for sample in samples] == ["a-sample.md", "b-sample.md"]
    assert load_sample_requirements(tmp_path, "a-sample.md") == "Project name: A\n"


def test_env_persistence_helpers_round_trip(tmp_path):
    run_dir = tmp_path / "run"

    write_target_env(run_dir, {"OPENAI_API_KEY": "sk-test", "EMPTY": ""})

    assert saved_env_keys(run_dir) == ["OPENAI_API_KEY"]
    assert initial_env_value("MISSING", tmp_path) == ""


def test_missing_required_env_keys_reads_saved_values(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    intake = run_dir / "01-intake-brief.json"
    intake.parent.mkdir(parents=True)
    intake.write_text('{"required_configuration":["OPENAI_API_KEY"]}', encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert "OPENAI_API_KEY" in missing_required_env_keys(run_dir)

    write_target_env(run_dir, {"OPENAI_API_KEY": "sk-test"})

    assert "OPENAI_API_KEY" not in missing_required_env_keys(run_dir)
    assert read_required_configuration(run_dir) == ["OPENAI_API_KEY"]


def test_clear_console_runs_only_removes_console_dirs(tmp_path):
    runs = tmp_path / "runs"
    (runs / "console-a").mkdir(parents=True)
    (runs / "project-a").mkdir()

    result = clear_console_runs(runs)

    assert result.deleted == 1
    assert not (runs / "console-a").exists()
    assert (runs / "project-a").exists()
