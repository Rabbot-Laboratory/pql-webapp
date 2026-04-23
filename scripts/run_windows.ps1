param(
    [switch]$Demo
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$detectScript = Join-Path $scriptDir "detect_windows_ports.py"

if ($Demo) {
    python $detectScript launch --demo
} else {
    python $detectScript launch
}
