# Capture Mono — debugging notes

Detail-heavy notes for working in `capture_mono.py`. Lifted out of `CLAUDE.md`
so the auto-loaded project guidance stays focused on the rest of the codebase;
read this file when you're actively debugging the Frida agent, Mono memory
reads, or mid-run snapshot pickup gaps.

## Mid-run pickup gaps (Hero / UnlockedSlots / Prestige / Level)

`NetMessageGameStateSync` and `NetMessageRunInitialized` are the only messages
carrying a full `PlayerSnapshotDTO`; they fire at run init / reconnect /
certain transitions. The mid-run `NetMessageGameSim` / `CombatSim` `Player`
field resolves to `SimUpdatePlayer` — a per-tick delta with only
`CombatantId` + an `Attributes` dict for attrs that *changed this tick*.
First deltas usually carry `{Gold, Health, HealthMax}` (cached in
`_lastGoodAttrs`); Prestige/Level rarely tick. Recovery is automatic on the
next full `GameStateSync`. Grep `logs/coach_*.log` for `player-class fields`
and `fast-PlayerAttributes` when debugging similar gaps.

## Frida agent layout

- Frida agent source lives in `capture_mono_agent.js`; `capture_mono.py`
  loads it at import time into `FRIDA_MONO_AGENT` and substitutes a few
  Python-injected constants before handing it to Frida.
- Hook source must contain `"dynamic-data"` for Python-side
  `_merge_partial_snapshot` to carry forward player attrs.
- Dict layout cache:
  `entriesOff=24, countOff=64, entrySize=16, hashOff=0, keyOff=8, valueOff=12, headerAdj=16`.
  Field offsets from `getFields()` include the 16-byte MonoObject header;
  subtracted for value-type array entries.

## Fast vs. safe paths

`FAST_GAMESIM_PATH = true` enables direct memory reads (~39 ms median hook
latency) via `readGameSimFast`, `_fastReadPlayerAttrs` with cached dict
layout, `_directReadMonoString` (UTF-16 direct read), content-hash
SelectionSet cache, vtable→klass double-deref, and hint-trusting in
`getSnapshotMatches`. Set it to `false` to revert to the safer
NativeFunction path if a game update breaks the fast reader.

## Known fragility

- `fast_dict_fail` ~41% — managed dict is genuinely mid-update when the
  hook fires. JS-side `_lastGoodAttrs` cache covers gaps (Gold missing = 0%).
- `_directReadMonoString` auto-detects chars offset on first call
  (12 or 16, depending on Mono build).
- SelectionSet content-hash cache `selset_hits` may show 0 if no
  action-card states were seen this run; the cache only triggers in
  Choice/Loot/LevelUp states.
