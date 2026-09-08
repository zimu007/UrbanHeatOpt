@echo off
setlocal
rem UrbanHeatOpt GUI launcher. Prefer the active Python; otherwise use the
rem named project environment through a conda executable discoverable on PATH.
rem Usage: gui.bat [--demo] [--screenshot DIR]
cd /d %~dp0
if defined CONDA_PREFIX (
    python run_gui.py %*
) else (
    where conda >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Please activate urbanheatopt_env or add conda to PATH.
        exit /b 1
    )
    conda run --no-capture-output -n urbanheatopt_env python run_gui.py %*
)
