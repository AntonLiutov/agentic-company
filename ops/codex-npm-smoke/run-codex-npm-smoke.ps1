param(
    [string]$Model = "gpt-5.3-codex",
    [string]$ReasoningEffort = "medium",
    [ValidateSet("fast", "standard")]
    [string]$ServiceTier = "fast",
    [string]$CodexPackage = "@openai/codex",
    [string]$CodexVersion = "latest",
    [string]$NodeVersion = "",
    [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Import-SmokeEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing .env file: $Path. Create it from .env.example and set CODEX_API_KEY."
    }

    $loaded = $false

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        if ($trimmed -notmatch "^\s*([^#=\s]+)\s*=\s*(.*)\s*$") {
            continue
        }

        $name = $Matches[1].Trim()
        if ($name -ne "CODEX_API_KEY") {
            continue
        }

        $value = $Matches[2].Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
        $loaded = $true
    }

    if (-not $loaded -or -not [Environment]::GetEnvironmentVariable("CODEX_API_KEY", "Process")) {
        throw "Missing CODEX_API_KEY in $Path. Set it explicitly; no fallback or alias is used."
    }
}

function Get-NodePlatform {
    if (-not ($env:OS -eq "Windows_NT")) {
        throw "This PowerShell script bootstraps Windows Node.js. Use run-codex-npm-smoke.sh on Linux."
    }

    switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()) {
        "X64" { return "win-x64" }
        "Arm64" { return "win-arm64" }
        default {
            throw "Unsupported Windows architecture for portable Node.js bootstrap: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"
        }
    }
}

function Resolve-NodeVersion {
    param([string]$RequestedVersion)

    if ($RequestedVersion) {
        if ($RequestedVersion.StartsWith("v")) {
            return $RequestedVersion
        }
        return "v$RequestedVersion"
    }

    Write-Host "Resolving latest Node.js LTS version from nodejs.org ..."
    $index = Invoke-RestMethod -Uri "https://nodejs.org/dist/index.json" -UseBasicParsing
    $latestLts = $index | Where-Object { $_.lts } | Select-Object -First 1
    if (-not $latestLts -or -not $latestLts.version) {
        throw "Could not resolve latest Node.js LTS version from nodejs.org."
    }
    return [string]$latestLts.version
}

function Install-PortableNode {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$RequestedVersion = ""
    )

    $platform = Get-NodePlatform
    $version = Resolve-NodeVersion -RequestedVersion $RequestedVersion
    $nodeFolder = Join-Path $InstallRoot "node-$version-$platform"
    $nodeExe = Join-Path $nodeFolder "node.exe"
    $npmCmd = Join-Path $nodeFolder "npm.cmd"

    if ((Test-Path -LiteralPath $nodeExe) -and (Test-Path -LiteralPath $npmCmd)) {
        return [PSCustomObject]@{
            Node = $nodeExe
            Npm = $npmCmd
            BinDir = $nodeFolder
            Version = $version
        }
    }

    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $archiveName = "node-$version-$platform.zip"
    $archivePath = Join-Path $InstallRoot $archiveName
    $downloadUrl = "https://nodejs.org/dist/$version/$archiveName"

    Write-Host "Downloading portable Node.js $version for $platform ..."
    Write-Host $downloadUrl
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing

    Write-Host "Extracting Node.js to $InstallRoot ..."
    Expand-Archive -LiteralPath $archivePath -DestinationPath $InstallRoot -Force

    if (-not (Test-Path -LiteralPath $nodeExe) -or -not (Test-Path -LiteralPath $npmCmd)) {
        throw "Portable Node.js install did not produce expected node/npm files under $nodeFolder"
    }

    return [PSCustomObject]@{
        Node = $nodeExe
        Npm = $npmCmd
        BinDir = $nodeFolder
        Version = $version
    }
}

function Get-NpmCommand {
    param(
        [Parameter(Mandatory = $true)][string]$NodeInstallRoot,
        [string]$RequestedNodeVersion = ""
    )

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }

    $nodeInstall = Install-PortableNode -InstallRoot $NodeInstallRoot -RequestedVersion $RequestedNodeVersion
    $env:PATH = "$($nodeInstall.BinDir);$env:PATH"
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }

    if (Test-Path -LiteralPath $nodeInstall.Npm) {
        return $nodeInstall.Npm
    }

    throw "npm was not found after portable Node.js bootstrap."
}

function Render-SmokePrompt {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][string]$SummaryPath,
        [Parameter(Mandatory = $true)][string]$WeatherPath
    )

    if (-not (Test-Path -LiteralPath $TemplatePath)) {
        throw "Missing prompt template: $TemplatePath"
    }

    $today = (Get-Date).ToString("yyyy-MM-dd")
    $template = Get-Content -LiteralPath $TemplatePath -Raw
    return $template.
        Replace("{{SUMMARY_PATH}}", $SummaryPath).
        Replace("{{WEATHER_PATH}}", $WeatherPath).
        Replace("{{RUN_DATE}}", $today)
}

function Quote-CmdArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Invoke-CodexProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$InputText,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    $promptInputPath = [System.IO.Path]::ChangeExtension($LogPath, ".prompt.md")
    Set-Content -LiteralPath $promptInputPath -Value $InputText -Encoding UTF8

    $quotedArgs = ($Arguments | ForEach-Object { Quote-CmdArgument -Value $_ }) -join " "
    $commandLine = "type $(Quote-CmdArgument -Value $promptInputPath) | $(Quote-CmdArgument -Value $FilePath) $quotedArgs > $(Quote-CmdArgument -Value $LogPath) 2>&1"

    & $env:ComSpec /d /s /c $commandLine
    $exitCode = $LASTEXITCODE

    if (Test-Path -LiteralPath $LogPath) {
        foreach ($line in Get-Content -LiteralPath $LogPath) {
            Write-Host $line
        }
    }

    return $exitCode
}

$SmokeDir = $PSScriptRoot
$RepoRoot = Resolve-Path (Join-Path $SmokeDir "..\..")
$EnvPath = Join-Path $SmokeDir ".env"
$InstallDir = Join-Path $SmokeDir ".codex-npm"
$CodexHome = Join-Path $SmokeDir ".codex-home"
$NpmCacheDir = Join-Path $SmokeDir ".npm-cache"
$ToolsDir = Join-Path $SmokeDir ".tools"
$NodeInstallRoot = Join-Path $ToolsDir "node"
$OutputsDir = Join-Path $SmokeDir "outputs"
$SummaryPath = Join-Path $OutputsDir "summary.md"
$WeatherPath = Join-Path $OutputsDir "milan-weather.md"
$LastMessagePath = Join-Path $OutputsDir "codex-last-message.md"
$EventsPath = Join-Path $OutputsDir "codex-events.jsonl"
$PromptTemplatePath = Join-Path $SmokeDir "smoke-prompt.md"
$SchemaPath = Join-Path $SmokeDir "smoke-output.schema.json"

Write-Step "Codex npm VM smoke test"
Write-Host "Smoke folder: $SmokeDir"
Write-Host "Repository root: $RepoRoot"

Write-Step "Loading environment"
Import-SmokeEnv -Path $EnvPath
Write-Host "CODEX_API_KEY loaded from .env"

Write-Step "Checking Node.js and npm"
$npm = Get-NpmCommand -NodeInstallRoot $NodeInstallRoot -RequestedNodeVersion $NodeVersion
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    Write-Host "Node: $(& $node.Source --version)"
}
Write-Host "npm: $(& $npm --version)"

New-Item -ItemType Directory -Force -Path $InstallDir, $CodexHome, $NpmCacheDir, $OutputsDir | Out-Null
$env:CODEX_HOME = $CodexHome
$env:npm_config_cache = $NpmCacheDir
Remove-Item -LiteralPath $SummaryPath, $WeatherPath, $LastMessagePath, $EventsPath -Force -ErrorAction SilentlyContinue

$packageSpec = if ($CodexVersion -and $CodexVersion -ne "latest") {
    "$CodexPackage@$CodexVersion"
} else {
    "$CodexPackage@latest"
}

$CodexBin = Join-Path $InstallDir "node_modules\.bin\codex.cmd"

Write-Step "Installing Codex CLI through npm"
if ($ForceInstall -or -not (Test-Path -LiteralPath $CodexBin)) {
    & $npm install --prefix $InstallDir --no-audit --no-fund $packageSpec
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed for $packageSpec"
    }
} else {
    Write-Host "Using existing local Codex install."
}

if (-not (Test-Path -LiteralPath $CodexBin)) {
    throw "Codex binary was not found after npm install: $CodexBin"
}
Write-Host "Codex binary: $CodexBin"
Write-Host "Codex home: $CodexHome"

Write-Step "Checking Codex web search support"
$codexHelp = (& $CodexBin --help 2>&1 | Out-String)
if ($codexHelp -notmatch "--search") {
    throw "Installed Codex CLI does not expose --search. Internet access is mandatory for this smoke test."
}
Write-Host "Codex --search is available."

$relativeSummaryPath = "ops/codex-npm-smoke/outputs/summary.md"
$relativeWeatherPath = "ops/codex-npm-smoke/outputs/milan-weather.md"
$prompt = Render-SmokePrompt `
    -TemplatePath $PromptTemplatePath `
    -SummaryPath $relativeSummaryPath `
    -WeatherPath $relativeWeatherPath

Write-Step "Running Codex"
Write-Host "Model: $Model"
Write-Host "Reasoning effort: $ReasoningEffort"
Write-Host "Service tier/speed: $ServiceTier"
Write-Host "Sandbox: workspace-write"
Write-Host "Approval policy: never"
Write-Host "Internet/search: enabled"
Write-Host "Git repo check: skipped for VM/archive smoke compatibility"

$serviceTierArgs = @()
if ($ServiceTier -eq "fast") {
    $serviceTierArgs = @("-c", "service_tier=fast")
}

$codexArgs = @(
    "--search",
    "exec",
    "--skip-git-repo-check",
    "--cd", $RepoRoot.Path,
    "--add-dir", $OutputsDir,
    "--ephemeral",
    "--model", $Model,
    "--sandbox", "workspace-write",
    "--ignore-user-config",
    "--ignore-rules",
    "-c", "approval_policy=never",
    "-c", "model_reasoning_effort=$ReasoningEffort",
    $serviceTierArgs,
    "-c", "sandbox_mode=workspace-write",
    "-c", "sandbox_workspace_write.network_access=true",
    "--json",
    "--output-schema", $SchemaPath,
    "--output-last-message", $LastMessagePath,
    "-"
)

$codexExitCode = Invoke-CodexProcess `
    -FilePath $CodexBin `
    -Arguments $codexArgs `
    -InputText $prompt `
    -LogPath $EventsPath

if ($codexExitCode -ne 0) {
    throw "Codex smoke test failed with exit code $codexExitCode"
}

if (-not (Test-Path -LiteralPath $LastMessagePath)) {
    throw "Codex completed but did not create expected structured response: $LastMessagePath"
}

$lastMessage = Get-Content -LiteralPath $LastMessagePath -Raw
$result = $lastMessage | ConvertFrom-Json

if (-not $result.summary_markdown) {
    throw "Codex structured response did not include summary_markdown."
}

if (-not $result.milan_weather_markdown) {
    throw "Codex structured response did not include milan_weather_markdown."
}

Set-Content -LiteralPath $SummaryPath -Value ([string]$result.summary_markdown) -Encoding UTF8
Set-Content -LiteralPath $WeatherPath -Value ([string]$result.milan_weather_markdown) -Encoding UTF8

if (-not (Test-Path -LiteralPath $SummaryPath)) {
    throw "Codex completed but did not create expected summary: $SummaryPath"
}

if (-not (Test-Path -LiteralPath $WeatherPath)) {
    throw "Codex completed but did not create expected Milan weather file: $WeatherPath"
}

Write-Step "Smoke test complete"
Write-Host "Summary: $SummaryPath"
Write-Host "Milan weather: $WeatherPath"
Write-Host "Last Codex message: $LastMessagePath"
Write-Host "Codex event log: $EventsPath"
