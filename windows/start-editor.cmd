@echo off
rem Start the SignalSeedBCI editor in the browser (http://localhost:8765).
rem Keep this window open while you work; close it or press Ctrl+C to quit.
rem Options are passed on, e.g.:  start-editor.cmd --port 8766
cd /d "%~dp0\.."
.venv\Scripts\signalseedbci gui %*
pause
