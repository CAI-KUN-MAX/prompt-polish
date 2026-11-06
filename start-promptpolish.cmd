@echo off
rem Launch PromptPolish without a console window.
setlocal
cd /d "%~dp0"
start "" pythonw.exe "%~dp0prompt_polish.pyw"
if errorlevel 1 (
  echo Failed to start PromptPolish. Make sure pythonw.exe is on PATH.
  pause
)
endlocal
