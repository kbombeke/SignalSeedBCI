@echo off
rem Stream the BioSemi ActiveTwo to the network over LSL.
rem Edit the options below to match your cap. Close ActiView first (only one program can own the device).
rem   --channels 32       number of electrode channels (A1..A32; 64 = A1..B32)
rem   --montage biosemi32 label channels with 10-20 names (remove to keep A1.. labels)
rem   --lib "C:\path\Labview_DLL.dll"  if the driver is not in the default BioSemi location
rem Keep this window open while measuring: it shows the amplifier status (speed mode,
rem sampling rate, CMS out of range / battery warnings) and the effective rate.
cd /d "%~dp0\.."

rem BioSemi's driver: BIOSEMI_DRIVER if set, else the default BioSemi location, else a copy
rem shipped with OpenViBE.
set "LIB="
if not defined BIOSEMI_DRIVER if not exist "C:\Program Files\BioSemi\Developers_kit\C-code\Labview_DLL.dll" (
  if exist "C:\Program Files\openvibe-3.7.0-64bit\bin\Labview_DLL.dll" set "LIB=C:\Program Files\openvibe-3.7.0-64bit\bin\Labview_DLL.dll"
)
if defined LIB (
  .venv\Scripts\signalseedbci bridge biosemi --channels 32 --lib "%LIB%" %*
) else (
  .venv\Scripts\signalseedbci bridge biosemi --channels 32 %*
)
pause
