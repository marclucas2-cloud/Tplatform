@echo off
REM VRP Rotation SVXY/SPY/TLT — rebalancement mensuel
cd /d C:\Users\barqu\trading-platform
"C:\Users\barqu\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\paper_vrp.py --force >> logs\vrp_cron.log 2>&1
echo [%date% %time%] VRP rebalance execute >> logs\vrp_cron.log
