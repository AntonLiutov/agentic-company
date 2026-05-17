Hello. This is a VM readiness smoke test for Codex CLI installed through npm.

Working directory: the repository root.
Run date from the smoke script: {{RUN_DATE}}.

Please complete both checks and return the final answer as JSON matching the
provided output schema:

1. Inspect the repository and prepare a concise project summary for:
   {{SUMMARY_PATH}}

2. Use internet search to find today's weather in Milan, Italy, and prepare the
   weather report for:
   {{WEATHER_PATH}}

Requirements:
- do not write files directly;
- do not call shell commands that create, update, or delete files;
- do not use apply_patch;
- do not read `.env`, `.git`, `.venv`, `.codex-*`, `.tools`, `.npm-cache`,
  `outputs`, caches, generated run outputs, or any secret/config files;
- prefer focused read-only inspection of `README.md`, `pyproject.toml`,
  `docs/README.md`, `agents/README.md`, and `src/agentic_company`;
- keep the project summary short and business-readable;
- in `milan_weather_markdown`, include the exact calendar date, city,
  temperature, weather conditions, and source link if available;
- use the run date above as the meaning of "today";
- do not edit unrelated files.
