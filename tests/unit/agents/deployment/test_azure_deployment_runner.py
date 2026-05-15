from agentic_company.agents.deployment.runner import (
    _resolve_command,
    render_deployment_summary,
)


def test_deployment_summary_includes_failed_command_output():
    summary = render_deployment_summary(
        {
            "status": "blocked",
            "target_project_dir": "generated-project",
            "steps": [
                {
                    "name": "Azure account",
                    "status": "failed",
                    "details": "Command failed. az was not found on PATH.",
                    "output": "az was not found on PATH.",
                }
            ],
        }
    )

    assert "Status: blocked" in summary
    assert "Command failed. az was not found on PATH." in summary
    assert "## Failure Output" in summary
    assert "az was not found on PATH." in summary


def test_resolve_command_uses_az_cmd_on_windows(monkeypatch):
    monkeypatch.setattr("agentic_company.agents.deployment.runner.os.name", "nt")

    def fake_which(name):
        if name == "az.cmd":
            return r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
        return None

    monkeypatch.setattr("agentic_company.agents.deployment.runner.shutil.which", fake_which)

    resolved = _resolve_command(["az", "account", "show"])

    assert resolved == [
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        "account",
        "show",
    ]
