@echo off
setlocal
call "%~dp0skills\timing-studio\scripts\run.cmd" launch --session "%~dp0data\sessions\demo"
if errorlevel 1 pause
endlocal
