@echo off
rem Deploy the APPROVED SummerlinShellTool 0.6.0 (Facade) to the primary server 25566. Stop the server first.
"C:\BTE-Migration\venv\Scripts\python.exe" "%~dp0deploy_facade_primary.py" deploy
pause
