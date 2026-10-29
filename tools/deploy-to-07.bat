@echo off
rem Install the current SummerlinShellTool build (0.4.2) on the TEST server 07 (refuses while it runs or after an unclean stop; pilot and main servers are never targeted).
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_test_server.py" deploy "C:\BTE-Migration\07-shelltool-test-server"
pause
