$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
Set-Location $repoRoot

$env:DJANGO_API_BASE_URL = "http://DESKTOP-VDUIMSD:8000/api/dispatch"
$env:DJANGO_PUBLIC_BASE_URL = "http://DESKTOP-VDUIMSD:8000"
$env:TELEGRAM_BOT_TOKEN = "8755561360:AAEXz5u78PsGPNKgWtsBIdk8bccdUs__VoE"
$env:TELEGRAM_CHAT_ID = "-5195811297"

streamlit run app.py --server.address 0.0.0.0 --server.port 8501
