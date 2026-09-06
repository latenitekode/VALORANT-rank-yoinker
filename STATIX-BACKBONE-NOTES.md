# VRY — STATIX-style live backbone test fork

This fork keeps the existing VALORANT Rank Yoinker UI, configuration, browser viewer,
chat, stats, loadouts, and project identity. The changes are limited to the runtime/data
path that decides *when* live state changed and *how quickly* the existing VRY table is
fed its data.

## What changed

- VRY's websocket remains the fastest state-change signal, but is no longer the only
  signal. While waiting, the local Riot presence is checked once per second so a dropped
  or coalesced websocket event cannot leave VRY stuck in the previous phase.
- Presence selection now handles duplicate Riot VALORANT presence rows and prefers a
  recognized `MENUS`, `PREGAME`, or `INGAME` row.
- Suspicious `PREGAME/INGAME -> MENUS` transitions are verified before the live table is
  torn down.
- After Riot announces `PREGAME` or `INGAME`, VRY performs a bounded short retry loop for
  the GLZ player/match object. An early 400/404 no longer makes VRY wait for the next
  websocket transition before trying again.
- The redundant second active-player lookup at Core Game entry was removed.
- Per-player rank + optional recent-competitive-stat work is fetched concurrently with a
  conservative worker cap.
- Pregame per-player data is retained across the transition to Core Game and reused where
  possible.
- Player-presence waiting is now best-effort and bounded instead of sleeping one second
  for every missing PUUID.
- Static valorant-api loadout metadata is warmed in the background and cached instead of
  being downloaded/reparsed repeatedly at match start.
- Loadout and name failures are treated as optional-data failures rather than reasons to
  indefinitely block the scoreboard.
- Riot HTTP calls now have bounded timeouts/retries instead of recursive retry/sleep
  behavior. Per-worker HTTP sessions reuse keep-alive connections.

## What intentionally did NOT change

- `src/table.py`
- `src/config.py`
- `src/configurator.py`
- VRY table columns/appearance
- user-facing controls and prompts
- rank/skin/stat feature selection
- browser/mobile viewer interface
- game-chat / Discord RPC interface
- original project name, assets, and ISC license

## Live test checklist

The most important real-client test is:

1. Menus -> Agent Select: VRY should populate as before, ideally faster.
2. Agent Select -> loading/Core Game: the in-game table should begin loading as soon as
   Riot exposes Core Game instead of waiting deep into round 1.
3. One temporary Riot 400/404 during an active game should not tear the state down.
4. Real match end should still return to Menus normally.
5. Confirm loadouts/browser viewer/chat still behave exactly as upstream VRY.

This package was syntax-checked and exercised with mocked state-transition regressions,
but a real Riot session is required to validate end-to-end timing.
