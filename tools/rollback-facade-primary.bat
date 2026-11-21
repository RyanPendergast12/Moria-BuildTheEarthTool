@echo off
rem Roll the primary server 25566 back to the JAR + config it had before 0.6.0. Stop the server first.
"C:\BTE-Migration\venv\Scripts\python.exe" "%~dp0deploy_facade_primary.py" rollback
pause
