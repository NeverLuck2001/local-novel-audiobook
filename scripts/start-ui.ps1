param([int]$Port = 7860, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Arguments = @((Join-Path $ProjectRoot "ui.py"), "--port", $Port)
if ($NoBrowser) { $Arguments += "--no-browser" }
& python @Arguments
exit $LASTEXITCODE
