[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Join-Path $Workspace 'UrbanHeatOpt'
$EnvRoot = Join-Path $Workspace '.conda_envs\urbanheatopt_env'
$Python = Join-Path $EnvRoot 'python.exe'
$Recovery = Join-Path $Workspace 'recover_compact_r3.py'
$RunRoot = Join-Path $Repo 'runs\road_joint_v2\COMPACT_FULLSEASON_V1_20260903_R3'
$AsciiTemp = Join-Path $Workspace '.tmp\compact_fullseason_runtime'

foreach ($required in @($Python, $Recovery, (Join-Path $RunRoot 'compact_task_plan.json'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required recovery file is missing: $required"
    }
}

New-Item -ItemType Directory -Force -Path $AsciiTemp | Out-Null
$env:CONDA_PREFIX = $EnvRoot
$env:GDAL_DATA = Join-Path $EnvRoot 'Library\share\gdal'
$env:PROJ_DATA = Join-Path $EnvRoot 'Library\share\proj'
$env:PROJ_LIB = $env:PROJ_DATA
$env:TEMP = $AsciiTemp
$env:TMP = $AsciiTemp
$env:TMPDIR = $AsciiTemp
$env:PATH = "$EnvRoot;$EnvRoot\Library\bin;$EnvRoot\Scripts;$env:PATH"

Set-Location -LiteralPath $Repo
& $Python $Recovery --run-root $RunRoot
if ($LASTEXITCODE -ne 0) {
    throw "R3 recovery exited with code $LASTEXITCODE. Inspect recovery_control.json and recovery_driver_logs."
}

Write-Host "R3 recovery completed: $(Join-Path $RunRoot 'completion_manifest.json')" -ForegroundColor Green
