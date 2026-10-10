@echo off
rem Stream the BioSemi ActiveTwo to the network over LSL.
rem Edit the options below to match your cap. Close ActiView first (only one program can own the device).
rem   --channels 64       number of electrode channels (A1..B32)
rem   --montage biosemi64 label channels with 10-20 names (remove to keep A1.. labels)
rem   --lib "C:\path\Labview_DLL.dll"  if the driver is not in the default BioSemi location
cd /d "%~dp0\.."
.venv\Scripts\signalseedbci bridge biosemi --channels 64 %*
pause
