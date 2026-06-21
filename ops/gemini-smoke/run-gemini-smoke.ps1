param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$Model = $env:AGENTIC_FORMATTER_MODEL
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "==> $Message"
}

function Import-DotEnvFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Step "No .env file found at $Path"
    return
  }

  Write-Step "Loading .env from $Path"
  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
      return
    }
    if ($line -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$") {
      $name = $matches[1]
      $value = $matches[2].Trim()
      if (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
      ) {
        $value = $value.Substring(1, $value.Length - 2)
      }
      if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
      }
    }
  }
}

function Get-GeminiKey {
  $keyNames = @(
    "GOOGLE_API_KEY"
  )

  foreach ($name in $keyNames) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($value -and $value.Trim()) {
      return [pscustomobject]@{
        Name = $name
        Value = $value.Trim()
      }
    }
  }

  return $null
}

if (-not $Model -or -not $Model.Trim()) {
  $Model = "gemini-3.1-flash-lite"
}

$envPath = Join-Path $RepoRoot ".env"
$outputDir = Join-Path $PSScriptRoot "outputs"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Write-Step "Gemini smoke test"
Write-Host "Repo root: $RepoRoot"
Write-Host "Model: $Model"

Import-DotEnvFile -Path $envPath

$geminiKey = Get-GeminiKey
if (-not $geminiKey) {
  Write-Host "Gemini key: not configured"
  Write-Host "Expected: GOOGLE_API_KEY"
  exit 2
}

Write-Host "Gemini key source: $($geminiKey.Name)"
Write-Host "Gemini key value: loaded (hidden)"

$uri = "https://generativelanguage.googleapis.com/v1beta/models/$($Model):generateContent"
$prompt = "Reply with one short sentence confirming Gemini is reachable for Agentic Delivery Lab."
$body = @{
  contents = @(
    @{
      parts = @(
        @{ text = $prompt }
      )
    }
  )
  generationConfig = @{
    temperature = 0
    maxOutputTokens = 80
  }
} | ConvertTo-Json -Depth 20

Write-Step "Calling Gemini API"
Write-Host "Endpoint: https://generativelanguage.googleapis.com/v1beta/models/$($Model):generateContent"

try {
  $response = Invoke-RestMethod `
    -Method Post `
    -Uri $uri `
    -Headers @{ "X-goog-api-key" = $geminiKey.Value } `
    -ContentType "application/json" `
    -Body $body
} catch {
  Write-Host "Gemini call failed"
  if ($_.Exception.Response) {
    Write-Host "HTTP status: $([int]$_.Exception.Response.StatusCode) $($_.Exception.Response.StatusDescription)"
    try {
      $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
      $errorBody = $reader.ReadToEnd()
      Write-Host "Error body:"
      Write-Host $errorBody
    } catch {
      Write-Host "Could not read error body."
    }
  } else {
    Write-Host $_.Exception.Message
  }
  exit 1
}

$responsePath = Join-Path $outputDir "gemini-smoke-response.json"
$response | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $responsePath -Encoding UTF8

$text = ""
try {
  $text = [string]$response.candidates[0].content.parts[0].text
} catch {
  $text = ""
}

if (-not $text.Trim()) {
  Write-Host "Gemini returned a response, but no text was found."
  Write-Host "Saved raw response: $responsePath"
  exit 1
}

Write-Step "Gemini response"
Write-Host $text.Trim()
Write-Host "Saved raw response: $responsePath"
Write-Step "Gemini smoke test passed"
