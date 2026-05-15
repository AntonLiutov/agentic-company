from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_web_app_requirements() -> str:
    return """# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Target user:
A solo builder testing simple assistant ideas locally.

Core features:
- User can enter a message
- App sends the message to an LLM

Required configuration:
- OPENAI_API_KEY

Preferred stack:
- Python
- Streamlit

Acceptance criteria:
- App starts locally with Streamlit
"""


@pytest.fixture
def write_sample_requirements(sample_web_app_requirements: str):
    def write(path: Path) -> Path:
        path.write_text(sample_web_app_requirements, encoding="utf-8")
        return path

    return write
