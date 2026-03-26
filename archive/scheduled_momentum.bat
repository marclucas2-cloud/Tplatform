@echo off
REM Momentum Rotation — rebalancement mensuel automatique
REM Planifie via Task Scheduler le 1er de chaque mois a 15:30 UTC (ouverture US)

cd /d C:\Users\barqu\trading-platform
"C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_momentum.py --force >> logs\momentum_cron.log 2>&1
echo [%date% %time%] Rebalancement execute >> logs\momentum_cron.log
