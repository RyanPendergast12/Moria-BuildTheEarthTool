@echo off
rem Deploy the APPROVED SummerlinShellTool 0.7.1 (parking lots + Facade) to the primary server 25566. Stop the server first.
"C:\BTE-Migration\venv\Scripts\python.exe" "%~dp0deploy_v07_primary.py" deploy
pause
