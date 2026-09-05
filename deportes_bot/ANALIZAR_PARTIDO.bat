@echo off
title Analizar Partido - dashboard
cd /d "%~dp0"

echo ==========================================================
echo  ANALIZAR PARTIDO
echo  Creado por Diego Aleman
echo ----------------------------------------------------------
echo  Cuotas reales de 1xbet + Ecuabet, forma/tabla/H2H/corners-
echo  tarjetas-tiros de Sofascore. Solo muestra datos - no aposta
echo  nada solo. Se abre solo en el navegador (~30s la 1ra carga).
echo ==========================================================
echo.

if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe -m streamlit run dashboard_partido.py
) else (
    streamlit run dashboard_partido.py
)

pause
