$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
Set-Location $repoRoot

. (Join-Path (Split-Path -Parent $repoRoot) "server_stack_settings.ps1")
$settings = Get-ServerStackSettings
$profile = $settings.Public

Set-DispatchMapRuntimeEnvironment -Profile $profile
$streamlitExe = Join-Path $repoRoot ".venv\Scripts\streamlit.exe"

& $streamlitExe run app.py --server.address $profile.DispatchListenHost --server.port $profile.DispatchPort --browser.gatherUsageStats false
