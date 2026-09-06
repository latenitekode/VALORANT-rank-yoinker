# Changed files

This list is relative to the original VRY source supplied in the handoff package.

| File | Why it changed |
| --- | --- |
| `.github/workflows/build.yml` | Harden the official Windows build path around Python 3.14.5, run regression tests before freezing, verify required release files, keep manual dispatch, and run CI on the final working branch/PRs. |
| `INSTALL.bat` | Remove the stale Python 3.10–3.11 gate and make the source/developer installer explicitly target Python 3.14.x. |
| `START.bat` | Label it as a source/developer launcher so users do not confuse it with the frozen release. |
| `README.md` | Point release/source/build links to this fork and document the maintained frozen/source paths. |
| `FORK-NOTES.md` | Summarize the reliability-focused fork changes and validation limits. |
| `STATIX-BACKBONE-NOTES.md` | Document the final live-state/request-backbone behavior and live acceptance checklist. |
| `TEST-REPORT.md` | Record automated regression results and the remaining real-client test requirement. |
| `docs/app.js` | Point the bundled release counter at this fork instead of upstream. |
| `docs/index.html` | Point the bundled Download button at this fork while retaining upstream attribution/license links. |
| `main.py` | Integrate the live backbone, conservative concurrent per-player fetching, PREGAME→INGAME data reuse, optional-data fallbacks, and asynchronous result handling without redesigning the table/UI. |
| `src/live_backbone.py` | Add authoritative/sticky state transition logic, bounded match discovery retries, stale-match protection, and reverse-flicker validation. |
| `src/websocket.py` | Keep websocket as the fast wake-up signal while adding a light local-presence fallback and robust duplicate-presence handling. |
| `src/requestsV.py` | Add bounded timeouts/retries, synchronized auth refresh, per-thread keep-alive sessions, shared rate-limit backoff, safe update extraction, and fork-owned update/release endpoints. |
| `src/presences.py` | Make local/private presence selection defensive and avoid cached presence becoming state authority. |
| `src/states/menu.py` | Make optional party/presence metadata defensive so it cannot crash/block the table. |
| `src/Loadouts.py` | Cache/warm static cosmetic metadata, add single-flight protection, and degrade optional cosmetic failures instead of blocking the core table. |
| `src/names.py` | Use bounded request handling and safe fallback names. |
| `src/rank.py` | Add thread-safe response caching so current/previous rank lookups can reuse one Riot response safely. |
| `src/player_stats.py` | Add thread-safe caches and single-flight recent match-detail fetching. |
| `src/content.py` | Bound static content requests and improve defensive network behavior. |
| `src/account_manager/account_auth.py` | Add finite request timeouts to authentication/account requests. |
| `src/config.py` | Keep configuration loading compatible while making the touched path safer for concurrent/frozen runtime use. |
| `updatescript.bat` | Use explicit source/destination arguments and robust copy exit handling for the fork updater. |
| `tests/test_live_backbone.py` | Regression coverage for early 404s, sticky state, duplicate presence, stale match objects, rate-limit/network uncertainty, and phase flicker. |
| `tests/test_runtime_caches.py` | Regression coverage for rank cache reuse and concurrent match-detail single-flight behavior. |

Unchanged by design include `src/table.py`, the VRY table columns/appearance, project assets, core configuration UX, browser/mobile viewer interface, chat interface, and Discord RPC interface.
