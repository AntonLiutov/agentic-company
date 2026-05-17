#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-gpt-5.3-codex}"
REASONING_EFFORT="${REASONING_EFFORT:-medium}"
SERVICE_TIER="${SERVICE_TIER:-fast}"
CODEX_PACKAGE="${CODEX_PACKAGE:-@openai/codex}"
CODEX_VERSION="${CODEX_VERSION:-latest}"
NODE_VERSION="${NODE_VERSION:-}"
FORCE_INSTALL="${FORCE_INSTALL:-0}"

log_step() {
  printf '\n==> %s\n' "$1"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$script_dir/.env"
install_dir="$script_dir/.codex-npm"
codex_home="$script_dir/.codex-home"
npm_cache_dir="$script_dir/.npm-cache"
tools_dir="$script_dir/.tools"
node_install_root="$tools_dir/node"
outputs_dir="$script_dir/outputs"
summary_path="$outputs_dir/summary.md"
weather_path="$outputs_dir/milan-weather.md"
last_message_path="$outputs_dir/codex-last-message.md"
events_path="$outputs_dir/codex-events.jsonl"
prompt_template_path="$script_dir/smoke-prompt.md"
schema_path="$script_dir/smoke-output.schema.json"

load_smoke_env() {
  if [[ ! -f "$env_path" ]]; then
    printf 'Missing .env file: %s. Create it from .env.example and set CODEX_API_KEY.\n' "$env_path" >&2
    exit 1
  fi

  local line name value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#$'\xef\xbb\xbf'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *=* ]] || continue
    name="${line%%=*}"
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"
    [[ "$name" == "CODEX_API_KEY" ]] || continue
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$name=$value"
  done < "$env_path"

  if [[ -z "${CODEX_API_KEY:-}" ]]; then
    printf 'Missing CODEX_API_KEY in %s. Set it explicitly; no fallback or alias is used.\n' "$env_path" >&2
    exit 1
  fi
}

node_platform() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  if [[ "$os" != "Linux" ]]; then
    printf 'This bash script bootstraps Linux Node.js. Use run-codex-npm-smoke.ps1 on Windows.\n' >&2
    exit 1
  fi

  case "$arch" in
    x86_64|amd64) printf 'linux-x64' ;;
    aarch64|arm64) printf 'linux-arm64' ;;
    *)
      printf 'Unsupported Linux architecture for portable Node.js bootstrap: %s\n' "$arch" >&2
      exit 1
      ;;
  esac
}

resolve_node_version() {
  if [[ -n "$NODE_VERSION" ]]; then
    if [[ "$NODE_VERSION" == v* ]]; then
      printf '%s' "$NODE_VERSION"
    else
      printf 'v%s' "$NODE_VERSION"
    fi
    return
  fi

  python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("https://nodejs.org/dist/index.json", timeout=30) as response:
    versions = json.load(response)

for item in versions:
    if item.get("lts"):
        print(item["version"], end="")
        break
else:
    raise SystemExit("Could not resolve latest Node.js LTS version from nodejs.org.")
PY
}

install_portable_node() {
  local platform version node_dir node_bin npm_bin archive_name archive_path download_url
  platform="$(node_platform)"
  version="$(resolve_node_version)"
  node_dir="$node_install_root/node-$version-$platform"
  node_bin="$node_dir/bin/node"
  npm_bin="$node_dir/bin/npm"

  if [[ -x "$node_bin" && -x "$npm_bin" ]]; then
    export PATH="$node_dir/bin:$PATH"
    return
  fi

  mkdir -p "$node_install_root"
  archive_name="node-$version-$platform.tar.xz"
  archive_path="$node_install_root/$archive_name"
  download_url="https://nodejs.org/dist/$version/$archive_name"

  printf 'Downloading portable Node.js %s for %s ...\n' "$version" "$platform"
  printf '%s\n' "$download_url"
  if command -v curl >/dev/null 2>&1; then
    curl -fL "$download_url" -o "$archive_path"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$archive_path" "$download_url"
  else
    printf 'Neither curl nor wget is available for Node.js download.\n' >&2
    exit 1
  fi

  tar -xJf "$archive_path" -C "$node_install_root"
  if [[ ! -x "$node_bin" || ! -x "$npm_bin" ]]; then
    printf 'Portable Node.js install did not produce expected node/npm files under %s\n' "$node_dir" >&2
    exit 1
  fi

  export PATH="$node_dir/bin:$PATH"
}

ensure_npm() {
  if command -v npm >/dev/null 2>&1; then
    return
  fi
  install_portable_node
  if ! command -v npm >/dev/null 2>&1; then
    printf 'npm was not found after portable Node.js bootstrap.\n' >&2
    exit 1
  fi
}

ensure_codex_linux_sandbox() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    return
  fi

  if ! command -v bwrap >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
      printf 'Installing Linux sandbox prerequisite: bubblewrap ...\n'
      sudo apt-get update -y
      sudo apt-get install -y bubblewrap apparmor-utils apparmor-profiles
    else
      printf 'Missing bubblewrap (bwrap). Install it before running Codex workspace-write sandbox on Linux.\n' >&2
      exit 1
    fi
  fi

  local profile_source="/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
  local profile_target="/etc/apparmor.d/bwrap-userns-restrict"
  if command -v apparmor_parser >/dev/null 2>&1 && [[ -f "$profile_source" ]] && command -v sudo >/dev/null 2>&1; then
    if [[ ! -f "$profile_target" ]] || ! sudo cmp -s "$profile_source" "$profile_target"; then
      printf 'Installing AppArmor profile for bubblewrap user namespaces ...\n'
      sudo install -m 0644 "$profile_source" "$profile_target"
    fi
    printf 'Loading AppArmor profile for bubblewrap user namespaces ...\n'
    sudo apparmor_parser -r "$profile_target"
  fi
}

render_prompt() {
  local today
  today="$(date +%F)"
  if [[ ! -f "$prompt_template_path" ]]; then
    printf 'Missing prompt template: %s\n' "$prompt_template_path" >&2
    exit 1
  fi
  sed \
    -e "s|{{SUMMARY_PATH}}|ops/codex-npm-smoke/outputs/summary.md|g" \
    -e "s|{{WEATHER_PATH}}|ops/codex-npm-smoke/outputs/milan-weather.md|g" \
    -e "s|{{RUN_DATE}}|$today|g" \
    "$prompt_template_path"
}

log_step "Codex npm VM smoke test"
printf 'Smoke folder: %s\n' "$script_dir"
printf 'Repository root: %s\n' "$repo_root"

case "$SERVICE_TIER" in
  fast|standard) ;;
  *)
    printf 'SERVICE_TIER must be one of: fast, standard\n' >&2
    exit 1
    ;;
esac

log_step "Loading environment"
load_smoke_env
printf 'CODEX_API_KEY loaded from .env\n'

log_step "Checking Codex Linux sandbox prerequisites"
ensure_codex_linux_sandbox
if command -v bwrap >/dev/null 2>&1; then
  printf 'bubblewrap: %s\n' "$(command -v bwrap)"
else
  printf 'bubblewrap: not required on this OS\n'
fi

log_step "Checking Node.js and npm"
ensure_npm
printf 'Node: %s\n' "$(node --version)"
printf 'npm: %s\n' "$(npm --version)"

mkdir -p "$install_dir" "$codex_home" "$npm_cache_dir" "$outputs_dir"
export CODEX_HOME="$codex_home"
export npm_config_cache="$npm_cache_dir"
rm -f "$summary_path" "$weather_path" "$last_message_path" "$events_path"

if [[ "$CODEX_VERSION" != "latest" ]]; then
  package_spec="$CODEX_PACKAGE@$CODEX_VERSION"
else
  package_spec="$CODEX_PACKAGE@latest"
fi

codex_bin="$install_dir/node_modules/.bin/codex"

log_step "Installing Codex CLI through npm"
if [[ "$FORCE_INSTALL" == "1" || ! -x "$codex_bin" ]]; then
  npm install --prefix "$install_dir" --no-audit --no-fund "$package_spec"
else
  printf 'Using existing local Codex install.\n'
fi

if [[ ! -x "$codex_bin" ]]; then
  printf 'Codex binary was not found after npm install: %s\n' "$codex_bin" >&2
  exit 1
fi
printf 'Codex binary: %s\n' "$codex_bin"
printf 'Codex home: %s\n' "$codex_home"

log_step "Checking Codex web search support"
if ! "$codex_bin" --help 2>&1 | grep -q -- '--search'; then
  printf 'Installed Codex CLI does not expose --search. Internet access is mandatory for this smoke test.\n' >&2
  exit 1
fi
printf 'Codex --search is available.\n'

prompt="$(render_prompt)"

log_step "Running Codex"
printf 'Model: %s\n' "$MODEL"
printf 'Reasoning effort: %s\n' "$REASONING_EFFORT"
printf 'Service tier/speed: %s\n' "$SERVICE_TIER"
printf 'Sandbox: workspace-write\n'
printf 'Approval policy: never\n'
printf 'Internet/search: enabled\n'
printf 'Git repo check: skipped for VM/archive smoke compatibility\n'

codex_config_args=(
  -c 'approval_policy=never'
  -c "model_reasoning_effort=$REASONING_EFFORT"
)
if [[ "$SERVICE_TIER" == "fast" ]]; then
  codex_config_args+=(-c 'service_tier=fast')
fi
codex_config_args+=(
  -c 'sandbox_mode=workspace-write'
  -c 'sandbox_workspace_write.network_access=true'
)

printf '%s\n' "$prompt" | "$codex_bin" \
  --search \
  exec \
  --skip-git-repo-check \
  --cd "$repo_root" \
  --add-dir "$outputs_dir" \
  --ephemeral \
  --model "$MODEL" \
  --sandbox workspace-write \
  --ignore-user-config \
  --ignore-rules \
  "${codex_config_args[@]}" \
  --json \
  --output-schema "$schema_path" \
  --output-last-message "$last_message_path" \
  - 2>&1 | tee "$events_path"

if [[ ! -f "$last_message_path" ]]; then
  printf 'Codex completed but did not create expected structured response: %s\n' "$last_message_path" >&2
  exit 1
fi

node - "$last_message_path" "$summary_path" "$weather_path" <<'NODE'
const fs = require("fs");

const [lastMessagePath, summaryPath, weatherPath] = process.argv.slice(2);
const raw = fs.readFileSync(lastMessagePath, "utf8");
const result = JSON.parse(raw);

if (!result.summary_markdown) {
  throw new Error("Codex structured response did not include summary_markdown.");
}

if (!result.milan_weather_markdown) {
  throw new Error("Codex structured response did not include milan_weather_markdown.");
}

fs.writeFileSync(summaryPath, String(result.summary_markdown), "utf8");
fs.writeFileSync(weatherPath, String(result.milan_weather_markdown), "utf8");
NODE

if [[ ! -f "$summary_path" ]]; then
  printf 'Codex completed but did not create expected summary: %s\n' "$summary_path" >&2
  exit 1
fi

if [[ ! -f "$weather_path" ]]; then
  printf 'Codex completed but did not create expected Milan weather file: %s\n' "$weather_path" >&2
  exit 1
fi

if grep -qiE 'bwrap:|setting up uid map: Permission denied|sandbox wrapper could not initialize' "$events_path" "$summary_path" "$weather_path"; then
  printf 'Codex completed, but sandbox/tool execution failed inside the VM. See %s.\n' "$events_path" >&2
  exit 1
fi

log_step "Smoke test complete"
printf 'Summary: %s\n' "$summary_path"
printf 'Milan weather: %s\n' "$weather_path"
printf 'Last Codex message: %s\n' "$last_message_path"
printf 'Codex event log: %s\n' "$events_path"
