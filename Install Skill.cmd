@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-skill.ps1" %*
if errorlevel 1 (
  pause
) else (
  echo Skill installed. Open Codex and use $timing-studio.
  pause
)
endlocal
