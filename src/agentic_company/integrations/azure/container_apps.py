"""Azure Container Apps CLI command builders."""

from __future__ import annotations


def account_show_command() -> list[str]:
    return ["az", "account", "show"]


def account_set_command(subscription_id: str) -> list[str]:
    return ["az", "account", "set", "--subscription", subscription_id]


def resource_group_show_command(resource_group: str) -> list[str]:
    return ["az", "group", "show", "--name", resource_group]


def resource_group_create_command(resource_group: str, location: str) -> list[str]:
    return ["az", "group", "create", "--name", resource_group, "--location", location]


def registry_show_command(registry_name: str) -> list[str]:
    return ["az", "acr", "show", "--name", registry_name]


def registry_create_command(
    *,
    resource_group: str,
    registry_name: str,
    sku: str = "Basic",
    admin_enabled: bool = True,
) -> list[str]:
    return [
        "az",
        "acr",
        "create",
        "--resource-group",
        resource_group,
        "--name",
        registry_name,
        "--sku",
        sku,
        "--admin-enabled",
        str(admin_enabled).lower(),
    ]


def registry_login_command(registry_name: str) -> list[str]:
    return ["az", "acr", "login", "--name", registry_name]


def registry_credentials_command(registry_name: str) -> list[str]:
    return ["az", "acr", "credential", "show", "--name", registry_name]


def first_registry_password(credentials: dict[str, object]) -> str:
    passwords = credentials.get("passwords", [])
    if not isinstance(passwords, list):
        return ""
    for item in passwords:
        if isinstance(item, dict) and item.get("value"):
            return str(item["value"])
    return ""


def container_environment_show_command(
    *,
    environment_name: str,
    resource_group: str,
) -> list[str]:
    return [
        "az",
        "containerapp",
        "env",
        "show",
        "--name",
        environment_name,
        "--resource-group",
        resource_group,
    ]


def container_environment_create_command(
    *,
    environment_name: str,
    resource_group: str,
    location: str,
) -> list[str]:
    return [
        "az",
        "containerapp",
        "env",
        "create",
        "--name",
        environment_name,
        "--resource-group",
        resource_group,
        "--location",
        location,
    ]


def container_app_show_command(*, app_name: str, resource_group: str) -> list[str]:
    return [
        "az",
        "containerapp",
        "show",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
    ]


def container_app_create_command(
    *,
    app_name: str,
    resource_group: str,
    environment_name: str,
    registry_server: str,
    registry_username: str,
    registry_password: str,
    image: str,
    env_values: dict[str, str],
) -> list[str]:
    return [
        "az",
        "containerapp",
        "create",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--environment",
        environment_name,
        "--image",
        image,
        "--target-port",
        "8501",
        "--ingress",
        "external",
        "--registry-server",
        registry_server,
        "--registry-username",
        registry_username,
        "--registry-password",
        registry_password,
        "--secrets",
        *_secret_arguments(env_values),
        "--env-vars",
        *_env_var_arguments(env_values),
    ]


def container_app_registry_set_command(
    *,
    app_name: str,
    resource_group: str,
    registry_server: str,
    registry_username: str,
    registry_password: str,
) -> list[str]:
    return [
        "az",
        "containerapp",
        "registry",
        "set",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--server",
        registry_server,
        "--username",
        registry_username,
        "--password",
        registry_password,
    ]


def container_app_secret_set_command(
    *,
    app_name: str,
    resource_group: str,
    env_values: dict[str, str],
) -> list[str]:
    return [
        "az",
        "containerapp",
        "secret",
        "set",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--secrets",
        *_secret_arguments(env_values),
    ]


def container_app_update_image_env_command(
    *,
    app_name: str,
    resource_group: str,
    image: str,
    env_values: dict[str, str],
) -> list[str]:
    return [
        "az",
        "containerapp",
        "update",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--image",
        image,
        "--set-env-vars",
        *_env_var_arguments(env_values),
    ]


def container_app_public_url_command(*, app_name: str, resource_group: str) -> list[str]:
    return [
        "az",
        "containerapp",
        "show",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--query",
        "properties.configuration.ingress.fqdn",
        "-o",
        "tsv",
    ]


def safe_account_summary(account: dict[str, object], subscription_id: str) -> dict[str, str]:
    user = account.get("user", {})
    user_name = user.get("name", "") if isinstance(user, dict) else ""
    return {
        "subscription_id": subscription_id,
        "subscription_name": str(account.get("name", "")),
        "tenant_id": str(account.get("tenantId", "")),
        "user": str(user_name),
    }


def _secret_name(key: str) -> str:
    return key.lower().replace("_", "-")


def _secret_arguments(env_values: dict[str, str]) -> list[str]:
    return [f"{_secret_name(key)}={value}" for key, value in sorted(env_values.items())]


def _env_var_arguments(env_values: dict[str, str]) -> list[str]:
    return [f"{key}=secretref:{_secret_name(key)}" for key in sorted(env_values)]
