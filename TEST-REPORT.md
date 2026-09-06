# VRY fork regression test report

## Automated validation

Command:

```text
python -m unittest discover -s tests -v
```

Result: **11/11 tests passed**.

Covered regressions:

- early PREGAME membership 404 followed by successful discovery;
- one explicit 404 does not clear a confirmed live match;
- repeated explicit absence allows a real transition back to MENUS;
- rate-limit/network uncertainty preserves positive live state;
- stale match details are never used without current membership;
- suspicious INGAME -> PREGAME reverse flicker is rejected while core membership remains active;
- duplicate/League presence rows do not beat a valid VALORANT loop row;
- cached private presence is not used as current state authority;
- rank response cache reuse;
- recent match-detail single-flight behavior under concurrent callers;
- fork release-version parsing.

`python -m compileall` / `py_compile` checks also completed successfully for the changed Python modules.

## CI packaging validation

`.github/workflows/build.yml` is configured to run the regression suite on `windows-latest`, install Python 3.14.5, build with `cx_Freeze`, copy `docs/`, and fail the build if any of the following are missing:

- `vry.exe`
- `python314.dll`
- `lib/`
- `docs/`
- `configurator.bat`
- `updatescript.bat`

## Remaining live-client validation

This environment cannot run the Riot Client / VALORANT, so the final real-client acceptance sequence still needs one live pass:

`MENUS -> PREGAME -> INGAME -> MENUS`

That pass should also verify custom game behavior, reconnect behavior, browser/mobile loadout rendering, game chat (if enabled), Discord RPC (if enabled), and behavior during an intentionally degraded/rate-limited connection.
