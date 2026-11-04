param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromCaller
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = $null
if (Test-Path ".venv\Scripts\python.exe") {
    $pythonExe = ".venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = "py -3"
}

if (-not $pythonExe) {
    Write-Host "Python was not found."
    Write-Host "Install Python and ensure it is on PATH, or create .venv in this project."
    exit 1
}

if ($pythonExe -eq "py -3") {
    & py -3 scripts\vaulted_add\vaulted_add.py @ArgsFromCaller
} else {
    & $pythonExe scripts\vaulted_add\vaulted_add.py @ArgsFromCaller
}
