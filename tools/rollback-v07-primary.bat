@echo off
rem Roll the primary server 25566 back to the JAR + config it had before 0.8.0 (0.7.1). Stop the server first.
"C:\BTE-Migration\venv\Scripts\python.exe" "%~dp0deploy_v07_primary.py" rollback
pause
