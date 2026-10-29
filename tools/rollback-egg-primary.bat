@echo off
rem Roll the PRIMARY server back to the JAR and config in the newest pre-0.5.0 backup (ledger kept).
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_egg_primary.py" rollback
pause
