# Install signalseeder (the bciflow command) on the Windows acquisition laptop.
# Run in PowerShell from the signalseeder folder:   powershell -ExecutionPolicy Bypass -File windows\install.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv venv --python 3.12 .venv
uv pip install --python .venv -e .

# LSL uses UDP 16571 (discovery) and TCP/UDP 16572-16604 (data). Allow them on private networks.
try {
    New-NetFirewallRule -DisplayName "LSL (bciflow)" -Direction Inbound -Protocol UDP -LocalPort 16571-16604 -Profile Private -Action Allow -ErrorAction Stop | Out-Null
    New-NetFirewallRule -DisplayName "LSL (bciflow) TCP" -Direction Inbound -Protocol TCP -LocalPort 16572-16604 -Profile Private -Action Allow -ErrorAction Stop | Out-Null
    Write-Host "Firewall rules added (private networks)."
} catch {
    Write-Host "Could not add firewall rules (run as Administrator to add them, or allow Python when Windows asks)."
}

Write-Host ""
Write-Host "Done. Test without hardware:   .venv\Scripts\bciflow bridge biosemi --fake"
Write-Host "With the ActiveTwo (ActiView closed):  windows\start-bridge.cmd"
