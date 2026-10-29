@echo off
rem Roll the TEST server 07 back to the previously installed SummerlinShellTool (ledger kept).
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_test_server.py" rollback "C:\BTE-Migration\07-shelltool-test-server"
pause
