@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual e instalando dependencias...
    python -m venv .venv || (echo No se encontro Python. Instala Python 3.10 o superior desde python.org & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo Error instalando dependencias & pause & exit /b 1)
)
start "" ".venv\Scripts\pythonw.exe" app.py
