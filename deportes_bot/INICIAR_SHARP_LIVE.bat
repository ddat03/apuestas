@echo off
title SHARP LIVE - test forward +EV (ancla Pinnacle)
cd /d "%~dp0"

echo ==========================================================
echo  SHARP LIVE - mide CLV vs Pinnacle en papel
echo  Creado por Diego Aleman
echo ----------------------------------------------------------
echo  100%% papel - ninguna apuesta real.
echo  Corre en bucle (cada 12h). Ctrl+C para detener.
echo  Ver estado sin correr un ciclo:  python sharp_live.py --resumen
echo ==========================================================
echo.

if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe sharp_live.py --loop
) else (
    python sharp_live.py --loop
)

pause
