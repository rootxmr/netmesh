@echo off
cd /d "%~dp0"
if not exist runs mkdir runs
python netmesh.py serve --host %HOST% --port %PORT%

