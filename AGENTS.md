# AGENTS.md: working rules for agents on Moria (read first)

## State (verified 2026-10-29)
- **Released:** v0.5.0 (GitHub "Latest", tag v0.5.0, commit a245188): Snowball (shells, roads, automatic overpasses) + Egg (terrain, vegetation, agriculture).
- **Installed live:** v0.5.0 (sha256 c29977b6...8a8c) on the PRIMARY server `localhost:25566` (`C:\BTE-Migration\06-summerlin-pilot-server`), with BlueMap 3.11.
  - **Never** stop, write or deploy to 25566 without the user's explicit approval.
  - **Never** touch the legacy server 25565 (`03-server`).
- **In development:** v0.6, the **Facade** tool (`/facadetool`): design packages and styles compiled into deterministic facade plans. Contract: `docs/facade/ARCHITECTURE.md` (file ownership in its section 10).
- **Test servers:** 07-12 are earlier copies. **Do not modify them.** v0.6 testing uses a NEW copy: 13 (`C:\BTE-Migration\13-facade-test`, port 25573), written only by the coordinator or the agent it names.

## Hard rules
1. **One writer per test world.** Only the coordinator, or the agent it names, starts the test server or writes its world. Everyone else is read-only on all worlds.
2. **Never write the live world, the legacy world, the purchased source ZIP, or the NYC reference.**
3. **Plans, not commands.** Generators produce block plans (ops). Only the plugin's protected writer places blocks, recording exact before/after states in the ledger. No WorldEdit pastes, no `/fill`, no clearing commands.
4. **No AI or API call at runtime** (per throw or per facade command). Runtime generation is deterministic code over cached data.
5. **Downloads:** bounded and cached under `C:\BTE-Migration\shelltool\cache` (or a sub-cache named in the architecture note), with timeouts and recorded provenance (URL, date, product version, resolution, licence).
6. **Never commit:** worlds, PDFs, imagery/rasters, large caches, ledgers, player UUIDs or names, emails. Test harnesses with UUIDs stay in `C:\BTE-Migration\scripts` (not in the repo).
7. **Python:** `C:\BTE-Migration\venv\Scripts\python.exe` (3.14). Java: `C:\BTE-Migration\runtimes\jdk-17.0.20.1+1`. Build against `07-shelltool-test-server\libraries` jars plus WorldEdit 7.2.20 if needed. Paper 1.18.2 #388. **Do not upgrade Minecraft, Paper or WorldEdit. FAWE is evaluation-only on a disposable copy, and only for a measured bottleneck (docs/facade/ARCHITECTURE.md).**
8. **File ownership:** v0.6: `docs/facade/ARCHITECTURE.md` section 10 (v0.5: `docs/egg/ARCHITECTURE.md` section 9). Edit only the files you own. Ask the coordinator for cross-cutting changes.
9. **Report honestly:** cite guide page numbers for construction rules, label estimates and inferences, and never treat missing data as empty land.
