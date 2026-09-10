@echo off
title Analizar Partido - dashboard
cd /d "%~dp0"

echo ==========================================================
echo  DASHBOARD APUESTAS
echo  Creado por Diego Aleman
echo ----------------------------------------------------------
echo  Pestana 1 - Analizar Partido: cuotas 1xbet+Ecuabet + stats
echo    de Sofascore (forma/tabla/H2H/corners/tarjetas/tiros).
echo  Pestana 2 - Combinada PrimaTips: tips O menor a 1.3 de
echo    primatips.com cruzados con Ecuabet, cuota combinada total.
echo  Solo muestra datos - no aposta nada solo.
echo ==========================================================
echo.

if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe -m streamlit run dashboard_partido.py
) else (
    streamlit run dashboard_partido.py
)

pause
