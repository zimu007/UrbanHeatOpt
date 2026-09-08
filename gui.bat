@echo off
rem Backward-compatible GUI shortcut; environment selection is centralized.
call "%~dp0RUN_URBANHEATOPT.cmd" --gui %*
exit /b %ERRORLEVEL%
