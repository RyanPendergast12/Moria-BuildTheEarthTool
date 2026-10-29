# AGENTS.md: working rules for agents on Moria (read first)

## State (verified 2026-10-29)
- **Released:** v0.4.3 (GitHub "Latest"): Snowball tool with shells, roads and automatic overpasses.
- **Installed live:** v0.4.3 on the PRIMARY server `localhost:25566` (`C:\BTE-Migration\06-summerlin-pilot-server`), with BlueMap 3.11.
  - **Never** stop, write or deploy to 25566 without the user's explicit approval.
  - **Never** touch the legacy server 25565 (`03-server`).
- **In development:** v0.5, the **Egg** tool. Design and contracts: `docs/egg/ARCHITECTURE.md`. Sources: `docs/egg/SOURCE-INVENTORY.md`.
- **Test servers:** 07–10 are earlier reviewed copies. **Do not modify them.** v0.5 testing uses a NEW copy: 11 (`C:\BTE-Migration\11-egg-test`, port 25571).

## Hard rules
1. **One writer per test world.** Only the coordinator, or the agent it names, starts the test server or writes its world. Everyone else is read-only on all worlds.
2. **Never write the live world, the legacy world, the purchased source ZIP, or the NYC reference.**
3. **Plans, not commands.** Generators produce block plans (ops). Only the plugin's protected writer places blocks, recording exact before/after states in the ledger. No WorldEdit pastes, no `/fill`, no clearing commands.
4. **No AI or API call per throw.** Runtime generation is deterministic code over cached data.
5. **Downloads:** bounded and cached under `C:\BTE-Migration\shelltool\cache` (or a sub-cache named in the architecture note), with timeouts and recorded provenance (URL, date, product version, resolution, licence).
6. **Never commit:** worlds, PDFs, imagery/rasters, large caches, ledgers, player UUIDs or names, emails. Test harnesses with UUIDs stay in `C:\BTE-Migration\scripts` (not in the repo).
7. **Python:** `C:\BTE-Migration\venv\Scripts\python.exe` (3.14). Java: `C:\BTE-Migration\runtimes\jdk-17.0.20.1+1`. Build against `07-shelltool-test-server\libraries` jars plus WorldEdit 7.2.20 if needed. Paper 1.18.2 #388. **Do not upgrade Minecraft, Paper or WorldEdit, and do not install FAWE.**
8. **File ownership:** see `docs/egg/ARCHITECTURE.md` §9. Edit only the files you own. Ask the coordinator for cross-cutting changes.
9. **Report honestly:** cite guide page numbers for construction rules, label estimates and inferences, and never treat missing data as empty land.
