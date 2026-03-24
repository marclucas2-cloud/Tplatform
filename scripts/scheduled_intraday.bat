@echo off
REM Paper Portfolio — execution intraday (5 strategies validees)
REM Tourne toutes les 5 min pendant les heures de marche US (15:35-22:00 Paris)
REM Strategies : ORB 5-Min, OpEx Gamma Pin, Earnings Drift, Day-of-Week, ML Cluster

cd /d C:\Users\barqu\trading-platform

REM Creer le dossier logs s'il n'existe pas
if not exist logs mkdir logs

echo [%date% %time%] Execution intraday >> logs\intraday_cron.log
"C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_portfolio.py --intraday >> logs\intraday_cron.log 2>&1
