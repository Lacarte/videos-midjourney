@echo on
SET current=%~dp0
echo %current%
rmdir /s /q .venv
python -m venv .venv
cd .venv\Scripts
pip.exe install -r ..\..\requirements.txt
echo "Done setup"
pause > nul