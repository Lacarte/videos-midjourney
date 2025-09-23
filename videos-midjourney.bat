@echo off
pushd %~dp0
:: Check if venv exists
IF EXIST .venv (
    :: Use the venv's python but run from project directory
    .venv\Scripts\python.exe videos-midjourney.py
) ELSE (
    :: Run your script using the system's python
    python videos-midjourney.py
)
pause