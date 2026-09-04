@echo off
setlocal
cd /d "%~dp0"
if "%GITHUB_TOKEN%"=="" (
  echo GITHUB_TOKEN is not set.
  echo See SETUP.md.
  pause
  exit /b 1
)
if "%GITHUB_REPO%"=="" (
  echo GITHUB_REPO is not set. Example: yourusername/anzca-anki-dashboard
  pause
  exit /b 1
)
python sync_anki.py
pause
