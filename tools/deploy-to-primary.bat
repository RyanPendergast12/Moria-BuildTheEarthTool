@echo off
rem Deploy the current SummerlinShellTool release (0.4.3) to the PRIMARY server (25566, folder 06-summerlin-pilot-server). Refuses while it runs or after an unclean stop; backs up plugin data and worlds first.
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_pilot.py" deploy
pause
