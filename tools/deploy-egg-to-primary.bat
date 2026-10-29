@echo off
rem Deploy SummerlinShellTool 0.5.0 (Egg) to the PRIMARY server (25566) ONLY after the in-game approval. Refuses while it runs or after an unclean stop; backs up plugin data and worlds first.
"C:\BTE-Migration\venv\Scripts\python.exe" "C:\BTE-Migration\repo\Moria-BuildTheEarthTool\tools\deploy_egg_primary.py" deploy
pause
