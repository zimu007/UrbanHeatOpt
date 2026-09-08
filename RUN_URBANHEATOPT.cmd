@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem One portable entry for validation, preparation, solving, tests and GUI.
rem The environment prefix can be overridden explicitly for another computer:
rem   set URBANHEATOPT_ENV_PREFIX=D:\path\to\urbanheatopt_env

set "UHO_ENV_PREFIX="
if defined URBANHEATOPT_ENV_PREFIX if exist "%URBANHEATOPT_ENV_PREFIX%\python.exe" set "UHO_ENV_PREFIX=%URBANHEATOPT_ENV_PREFIX%"
if not defined UHO_ENV_PREFIX if exist "%~dp0.conda_envs\urbanheatopt_env\python.exe" set "UHO_ENV_PREFIX=%~dp0.conda_envs\urbanheatopt_env"
if not defined UHO_ENV_PREFIX if exist "%~dp0..\.conda_envs\urbanheatopt_env\python.exe" set "UHO_ENV_PREFIX=%~dp0..\.conda_envs\urbanheatopt_env"

if not defined UHO_ENV_PREFIX (
    echo ERROR: UrbanHeatOpt pinned Conda environment was not found.
    echo Set URBANHEATOPT_ENV_PREFIX to the environment directory created from environment.yml.
    exit /b 2
)

set "UHO_CONDA_EXE="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "UHO_CONDA_EXE=%CONDA_EXE%"
if not defined UHO_CONDA_EXE if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "UHO_CONDA_EXE=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined UHO_CONDA_EXE if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "UHO_CONDA_EXE=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined UHO_CONDA_EXE if exist "D:\Miniforge\Scripts\conda.exe" set "UHO_CONDA_EXE=D:\Miniforge\Scripts\conda.exe"

if not defined UHO_CONDA_EXE (
    for /f "delims=" %%C in ('where conda.exe 2^>nul') do if not defined UHO_CONDA_EXE set "UHO_CONDA_EXE=%%C"
)
if not defined UHO_CONDA_EXE (
    echo ERROR: conda.exe was not found. Install Miniforge/Anaconda or set CONDA_EXE.
    exit /b 2
)

echo [UrbanHeatOpt] environment=%UHO_ENV_PREFIX%
"%UHO_CONDA_EXE%" run --no-capture-output -p "%UHO_ENV_PREFIX%" python tools\check_environment.py
if errorlevel 1 (
    echo ERROR: The pinned environment check failed. The requested command was not started.
    exit /b 2
)

if /I "%~1"=="--check" exit /b 0

if /I "%~1"=="--pytest" (
    shift
    "%UHO_CONDA_EXE%" run --no-capture-output -p "%UHO_ENV_PREFIX%" python -m pytest %*
    exit /b %ERRORLEVEL%
)

if /I "%~1"=="--gui" (
    shift
    "%UHO_CONDA_EXE%" run --no-capture-output -p "%UHO_ENV_PREFIX%" python run_gui.py %*
    exit /b %ERRORLEVEL%
)

"%UHO_CONDA_EXE%" run --no-capture-output -p "%UHO_ENV_PREFIX%" python run.py %*
exit /b %ERRORLEVEL%
