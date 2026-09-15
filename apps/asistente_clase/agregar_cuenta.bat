@echo off
title Agregar cuenta - Asistente de Clase
cd /d "%~dp0"
echo Recuerda: agrega antes el correo como "usuario de prueba" en la pantalla OAuth de Google Cloud.
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0agregar_cuenta.py"
echo.
pause
