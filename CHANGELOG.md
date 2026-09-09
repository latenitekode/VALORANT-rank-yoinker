# Changelog

All notable changes to this fork are documented here.

## 3.0.2

### Fixed
- Fixed the local Player Inventories tracker returning HTTP 404 in frozen Windows builds.
- The tracker server now resolves `docs/` from the directory beside `vry.exe` first and verifies that `matchLoadouts.html` exists before serving it.
- If the bundled tracker files cannot be found, the app falls back to the hosted tracker instead of opening a broken localhost URL.
- A tracker HTTP-server failure no longer gets reported as a websocket/firewall port failure.

### Tests
- Added regression coverage for the cx_Freeze layout and a real localhost HTTP request to `matchLoadouts.html`.

## 3.0.1

### Fixed
- Restored Ctrl+Click browser opening in Windows Terminal by using a localhost HTTP tracker URL instead of an OSC-8 `file://` target.

### Release tooling
- Added automatic GitHub Releases after successful `main` builds when the application version has been bumped.

## 3.00

- Initial VRY STATIX Edition release of this fork.
- Reliability improvements for live-match detection, Riot requests, player-data loading, caching, and the frozen Windows build.
