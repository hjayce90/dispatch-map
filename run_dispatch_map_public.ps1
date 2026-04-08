$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
Set-Location $repoRoot

$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:NO_PROXY = "localhost,127.0.0.1,::1,sales.nasilfamily.com,dispatch.nasilfamily.com,milkrun.nasilfamily.com"
$env:DJANGO_API_BASE_URL = "http://127.0.0.1:8010/api/dispatch"
$env:DJANGO_PUBLIC_BASE_URL = "https://sales.nasilfamily.com"
$env:TELEGRAM_BOT_TOKEN = "8755561360:AAEXz5u78PsGPNKgWtsBIdk8bccdUs__VoE"
$env:TELEGRAM_CHAT_ID = "-5195811297"
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
$streamlitExe = Join-Path $repoRoot ".venv\Scripts\streamlit.exe"

& $streamlitExe run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
