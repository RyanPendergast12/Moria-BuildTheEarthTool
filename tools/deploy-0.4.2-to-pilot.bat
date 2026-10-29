@echo off
rem Deploy SummerlinShellTool 0.4.2 to the regular PILOT server (06). Refuses while it runs or after an unclean stop; backs up plugin data and worlds first.
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_pilot.py" deploy
pause
