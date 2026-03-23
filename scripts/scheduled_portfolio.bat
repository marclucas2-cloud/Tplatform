@echo off
REM Paper Portfolio — execution quotidienne unifiee (3 strategies)
REM Le 1er du mois : rebalancement momentum + VRP (--force)
REM Les autres jours : check pairs trading uniquement
REM Heure : 15:25 UTC (avant ouverture US 15:30)

cd /d C:\Users\barqu\trading-platform

REM Detecter si c'est le 1er du mois
set "day=%date:~0,2%"
if "%day%"=="01" (
    echo [%date% %time%] Execution mensuelle (force) >> logs\portfolio_cron.log
    "C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_portfolio.py --force >> logs\portfolio_cron.log 2>&1
) else (
    echo [%date% %time%] Execution quotidienne >> logs\portfolio_cron.log
    "C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_portfolio.py >> logs\portfolio_cron.log 2>&1
)
