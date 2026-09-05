[CmdletBinding()]
param(
    [string]$RunName = 'COMPACT_FULLSEASON_UNLIMITED_V1',
    [string]$CaseJson = '',
    [string]$PythonExe = '',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Workspace = Split-Path -Parent $Repo
$EnvRoot = Join-Path $Workspace '.conda_envs\urbanheatopt_env'
$Python = if ($PythonExe) { $PythonExe } else { Join-Path $EnvRoot 'python.exe' }
$Driver = Join-Path $Repo 'scripts\run_compact_fullseason.py'
$RunRoot = Join-Path $Repo "runs\road_joint_v2\$RunName"
$AsciiTemp = Join-Path $Workspace '.tmp\compact_fullseason_runtime'

foreach ($required in @($Python, $Driver)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
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

$HighsVersion = (& $Python -c "import highspy; print(highspy.Highs().version())").Trim()
if ($LASTEXITCODE -ne 0 -or $HighsVersion -ne '1.15.1') {
    throw "Expected HighsPy 1.15.1, found $HighsVersion"
}

$PlanPath = Join-Path $RunRoot 'compact_task_plan.json'
if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {
    $PrepareArgs = @(
        $Driver,
        '--run-root', $RunRoot,
        '--action', 'prepare',
        '--enable-tes-upgrade'
    )
    if ($CaseJson) {
        $ResolvedCase = (Resolve-Path -LiteralPath $CaseJson).Path
        $PrepareArgs += @('--case-json', $ResolvedCase)
    }
    & $Python @PrepareArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Compact plan preparation failed with exit code $LASTEXITCODE"
    }
}

& $Python -c "import json,sys; sys.path.insert(0,sys.argv[2]); from competition.road_joint_v2.compact_tasks import verify_compact_plan; p=verify_compact_plan(sys.argv[1]); print(json.dumps({'tasks':len(p['tasks']),'buildings':p['building_count'],'hours':p['hour_count'],'sites':p['site_count'],'threads':p['solver']['threads_per_task'],'parallel':p['solver']['max_parallel_tasks'],'resource_limits':p['resource_limits'],'git_sha':p['git_sha']},ensure_ascii=False))" $RunRoot $Repo
if ($LASTEXITCODE -ne 0) {
    throw 'Compact task plan, source hash, or case hash verification failed.'
}

Write-Host 'UrbanHeatOpt compact full-season run' -ForegroundColor Cyan
Write-Host "  Repository : $Repo"
Write-Host "  Run root   : $RunRoot"
Write-Host "  HiGHS      : $HighsVersion"
Write-Host '  Horizon    : 62 buildings x 2160 hours'
Write-Host '  Search     : 5 fixed-root shortest-path trees'
Write-Host '  Parallel   : 4 tasks x 4 threads'
Write-Host '  Time limits: disabled for run, scan, TES gate, solver, and replay'
Write-Host '  Memory     : no limit and no external memory guard'
Write-Host '  Storage    : no-TES baseline first; TES uses a validation gate without a timer'
Write-Host '  Run output : local only; this launcher never performs a Git push'

if ($PreflightOnly) {
    Write-Host 'Preflight passed. No optimization worker was started.' -ForegroundColor Green
    exit 0
}

Set-Location -LiteralPath $Repo
& $Python $Driver --run-root $RunRoot --action run
if ($LASTEXITCODE -ne 0) {
    throw "Compact driver exited with code $LASTEXITCODE. Inspect run_status.json and driver_logs."
}

Write-Host "Compact result completed: $(Join-Path $RunRoot 'compact_pareto_frontiers.json')" -ForegroundColor Green
