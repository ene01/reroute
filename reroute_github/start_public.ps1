# Starts Reroute and puts it on your public ngrok link.
# Double-click start_public.bat (it runs this). Keep the window open; close it to take the link down.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8501

# Find ngrok: the copy next to its config first, then anything on PATH.
$ngrok = "$env:LOCALAPPDATA\ngrok\ngrok.exe"
if (-not (Test-Path $ngrok)) {
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($cmd) { $ngrok = $cmd.Source } else { $ngrok = $null }
}
if (-not $ngrok) {
    Write-Host "ngrok not found. Download it from https://ngrok.com/download" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# Create our packaged "reroute" AI model if this computer doesn't have it yet (a few seconds).
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollamaExe)) { $c = Get-Command ollama -ErrorAction SilentlyContinue; if ($c) { $ollamaExe = $c.Source } }
if (Test-Path $ollamaExe) {
    & $ollamaExe show reroute *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Creating the 'reroute' AI model (one time)..." -ForegroundColor Cyan
        & $ollamaExe create reroute -f "$here\Modelfile" *> $null
    }
}

function Test-Reroute {
    try { (Invoke-WebRequest "http://localhost:$port/_stcore/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
    catch { $false }
}

if (Test-Reroute) {
    Write-Host "Reroute is already running on port $port." -ForegroundColor Green
} else {
    Write-Host "Starting Reroute..." -ForegroundColor Cyan
    Start-Process python -WindowStyle Minimized -ArgumentList @(
        "-m", "streamlit", "run", "`"$here\app.py`"", "--server.port", "$port", "--server.headless", "true",
        "--client.toolbarMode", "minimal",
        # Same dark theme as .streamlit/config.toml, in case Python starts from another folder.
        "--theme.base", "dark", "--theme.primaryColor", "`"#00e0ea`"", "--theme.backgroundColor", "`"#050b16`"",
        "--theme.secondaryBackgroundColor", "`"#0d1828`"", "--theme.textColor", "`"#e4f1f5`"")
    for ($i = 0; $i -lt 30 -and -not (Test-Reroute); $i++) { Start-Sleep 1 }
    if (-not (Test-Reroute)) {
        Write-Host "Reroute did not start. Check the minimized Python window for errors." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Reroute is running at http://localhost:$port" -ForegroundColor Green
}

# Is a public link already running (an ngrok window from earlier)? Then don't start a second one.
$existing = $null
try { $existing = (Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2).tunnels.public_url } catch {}
if ($existing) {
    Write-Host ""
    Write-Host "Your public link is already running (from an ngrok window that's still open):" -ForegroundColor Green
    Write-Host "   $($existing -join ', ')" -ForegroundColor Green
    Write-Host ""
    Write-Host "Everything is up. You don't need to start anything else." -ForegroundColor Cyan
    Write-Host "To restart from scratch: close the old ngrok and Python windows, then run this again." -ForegroundColor Cyan
    Read-Host "Press Enter to close this window (the site keeps running)"
    exit 0
}

Write-Host ""
Write-Host "Opening the public link. Your address is on the 'Forwarding' line below." -ForegroundColor Cyan
Write-Host "Students sign up / log in with name + school + password. Staff use staff_password.txt." -ForegroundColor Cyan
Write-Host "Close this window to take the link down." -ForegroundColor Cyan
Write-Host ""
& $ngrok http $port
# Only reached if ngrok stops or fails: keep the window open so the message can be read.
Write-Host ""
Write-Host "ngrok stopped (see the message above). The public link is down." -ForegroundColor Yellow
Read-Host "Press Enter to close"
