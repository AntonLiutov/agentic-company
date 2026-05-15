"""Docker CLI command builders."""

from __future__ import annotations


def docker_info_command() -> list[str]:
    return ["docker", "info"]


def docker_build_command(*, image: str, context: str = ".") -> list[str]:
    return ["docker", "build", "-t", image, context]


def docker_push_command(image: str) -> list[str]:
    return ["docker", "push", image]
