@echo off
rem Roll the PILOT server back to the SummerlinShellTool JAR and config in the newest pre-0.4.2 backup (ledger kept).
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_pilot.py" rollback
pause
