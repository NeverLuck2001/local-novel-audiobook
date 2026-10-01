param([string]$InputPath = 'books', [string]$Config = 'config.yaml', [switch]$RetryFailed)
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $taskRoot
$taskArgs = @('audiobook.py', $InputPath, '--config', $Config)
if ($RetryFailed) { $taskArgs += '--retry-failed' }
& (Join-Path $taskRoot '.venv\Scripts\python.exe') @taskArgs
exit $LASTEXITCODE
