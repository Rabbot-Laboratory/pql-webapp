param(
    [switch]$Demo,
    [string]$PortName = ""
)

$singleLegRoot = Split-Path -Parent $PSScriptRoot
Push-Location $singleLegRoot
try {
    $env:SINGLE_LEG_EMULATE_DEVICES = if ($Demo) { "true" } else { "false" }
    if ($PortName) {
        $env:SINGLE_LEG_PORT_NAME = $PortName
    }
    python -m uvicorn single_leg_server.app:app --app-dir backend --host 0.0.0.0 --port 8100
}
finally {
    Pop-Location
}

