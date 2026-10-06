# Run the migration quality gate from any working directory.
$ErrorActionPreference = 'Stop'

function Invoke-Check {
    param (
        [string]$Command,
        [string[]]$Arguments
    )

    Write-Host "> $Command $($Arguments -join ' ')"
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Invoke-Check uv @('run', '--locked', 'pyink', '--check', 'backend', 'tests')
    Invoke-Check uv @('run', '--locked', 'pylint', '--rcfile=config/google.pylintrc', 'backend')
    Invoke-Check uv @('run', '--locked', 'pytest', '-q')
    Invoke-Check npm @('run', 'lint')
}
catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
exit 0
