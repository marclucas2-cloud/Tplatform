@echo off
REM Pairs Trading MU/AMAT — check quotidien avant ouverture US
cd /d C:\Users\barqu\trading-platform
"C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_pairs.py >> logs\pairs_cron.log 2>&1
echo [%date% %time%] Pairs check execute >> logs\pairs_cron.log
