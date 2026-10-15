# Install SignalSeedBCI (the signalseedbci command) on a Windows laptop: the bridge next to the
# amplifier, and/or the editor and pipelines.
# Run in PowerShell from the SignalSeedBCI folder:   powershell -ExecutionPolicy Bypass -File windows\install.ps1
#
#   -BridgeOnly    minimal install for the bridge (no pyRiemann / XDF support)
#   -AllowPublic   also open the LSL ports on networks Windows calls "Public". A direct Ethernet
#                  cable to another computer usually counts as Public ("Unidentified network").
param(
    [switch]$BridgeOnly,
    [switch]$AllowPublic
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv venv --python 3.12 .venv
if ($BridgeOnly) {
    uv pip install --python .venv -e .
} else {
    # Riemannian classifiers (ts_lr, mdm, xdawn_ts_lr) and XDF recordings
    uv pip install --python .venv -e ".[riemann,xdf]"
}

# LSL uses UDP 16571 (discovery) and TCP/UDP 16572-16604 (data). Allow them on private networks.
$profiles = if ($AllowPublic) { "Private,Public" } else { "Private" }
try {
    Get-NetFirewallRule -DisplayName "LSL (signalseedbci)*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "LSL (signalseedbci)" -Direction Inbound -Protocol UDP -LocalPort 16571-16604 -Profile $profiles -Action Allow -ErrorAction Stop | Out-Null
    New-NetFirewallRule -DisplayName "LSL (signalseedbci) TCP" -Direction Inbound -Protocol TCP -LocalPort 16572-16604 -Profile $profiles -Action Allow -ErrorAction Stop | Out-Null
    Write-Host "Firewall rules added ($profiles networks)."
} catch {
    Write-Host "Could not add firewall rules (run as Administrator to add them, or allow Python when Windows asks)."
}

Write-Host ""
Write-Host "Done."
Write-Host "  Editor:                              windows\start-editor.cmd"
Write-Host "  Bridge test without hardware:        .venv\Scripts\signalseedbci bridge biosemi --fake"
Write-Host "  Bridge with the ActiveTwo (ActiView closed):  windows\start-bridge.cmd"
