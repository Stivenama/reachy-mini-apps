@echo off
title Asistente de Clase (no cerrar esta ventana)
cd /d "%~dp0"
echo Iniciando Asistente de Clase...
echo Se abrira el navegador en unos segundos. No cierres esta ventana mientras uses la app.
start "" cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8100"
"%~dp0.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8100
echo.
echo La app se detuvo.
pause
