# rs-scope-mcp architecture

A stack of layers, each complete on its own, each only talking to the one below.
The bottom layers are exhaustive (every documented SCPI command); the top layers are
selective (the workflows that matter for ham radio, hardware reverse engineering and
embedded/drone development).

```
 L7  Skills            ham-bench · reverse-engineering · embedded/drone      (intent → plan)
 L6  MCP tools         grouped task tools + scpi escape hatch                (server.py)
 L5  Context engine    summaries, drill-down queries, token budgets          (context.py)
 L4  Data store        SQLite catalog + raw sample files, per task session   (store.py)
 L3  Subsystems        typed drivers: capture, waveform, logic, decode, bode (subsys/*.py)
 L2  Instrument        validation, error queue, *OPC?, batching, guards      (instrument.py) ✅
 L1  Registry          958 headers from the manual, resolve/validate/search  (registry.py)   ✅
 L0  Transport         raw SCPI socket :5025, IEEE 488.2 blocks              (transport.py)  ✅
     Simulator         registry-driven fake RTB2 for tests/CI/demos          (sim.py)        ✅
```

## L0 Transport (done)
Raw TCP to the RTB2 "IP Port" (5025), no VISA. Newline-terminated ASCII, definite-length
block data (`#<n><len><bytes>`), timeouts, thread lock. VXI-11 (port 1024) is not needed.

## L1 Registry (done)
`data/commands.json` is generated from chapter 16 of the user manual by
`scripts/extract_scpi.py` using the PDF's typesetting (bold headings at the text margin,
labelled fields, value column). Completeness is checked against the per-section command
indexes in the manual: **938/938 indexed commands found, plus 20 commands the indexes omit
→ 748 command blocks, 958 headers incl. aliases**. Each entry: header + aliases, kind
(query/set/set_query/event), suffix ranges, parameters (enum incl. expanded `CH1..4`/`D0..15`,
range, *RST, unit, increment), return values, examples, section, manual page.

Resolution compiles each header into a regex (short/long forms, optional `[:NODE]`, numeric
suffixes), so `bus2:uart:rx:fram10:word3:val?` resolves to
`BUS<b>:UART:FRAMe<n>:WORD<o>:VALue?` with `{b:2, n:10, o:3}`.
Validation: unknown header, suffix out of range, query/set misuse → **error**;
enum/range mismatch → **warning** (the scope's error queue is the final authority).

Bench cross-check: `rs-scope tree-diff` compares the registry with the firmware's own
`SYSTem:TREE?` list.

## L2 Instrument (done)
`*IDN?`/`*OPT?` on connect; every command validated before sending; `SYSTem:ERRor?` drained
after writes so failures are never silent; `*OPC?` waits; `query_many` joins queries with
`;:` (16 per round trip, falls back to one-by-one on mismatch).

**Safety gate** (needs `confirm`): generator output on, pattern generator on/manual pins,
Bode sweep start (drives the generator), `*RST`/`SYST:PRES`, `*CAL?`/`CAL`, file
delete/move/overwrite, `SYST:COMM:*` (can drop the connection), firmware update, date/time/name,
education-mode lock. Batches are checked as a whole: nothing is sent if any line is blocked.

## L3 Subsystems (next)
Typed drivers on top of the registry, each returning plain Python data:

| module | does |
|---|---|
| `acquire` | run/stop/single + `*OPC?`, record length, sample rate, segmented mode, `ACQ:AVA?` |
| `setup` | channels, probes, timebase, trigger (all types), snapshot/restore of the full setup (`MMEM:STOR:STAT` → `MMEM:DATA?`) |
| `waveform` | analog/math/ref/envelope fetch; FORMat + BORDer handling; UINT→volts via XOR/XINC/YOR/YINC/YRES; `POINts DEF/MAX/DMAX` (MAX requires STOP) |
| `logic` | D0–D15 / pods; run-length edge extraction (never ship raw bits upward) |
| `decode` | per-protocol frame harvesters (I²C, SPI, SSPI, UART, CAN, LIN, parallel): batched field reads, typed frame records |
| `history` | iterate segments (`*:HISTory:CURRent`), timestamps (`TSR/TSAB/TSD:ALL?`), export + download |
| `measure` | auto measurements incl. statistics, cursors, quick meas, DVM, counter |
| `spectrum` | FFT setup and arrays |
| `bode` | K36 sweep setup, amplitude profile, freq/gain/phase arrays, markers |
| `mask` | mask setup, test counts, violation segments |
| `generator` | B6 function/arbitrary/pattern generator (guarded) |
| `files` | MMEM catalog, download/upload, screenshot |

## L4 Data store (next)
Everything harvested lands locally first; Claude only sees what it asks for.

```
~/rs-scope-data/
  rtb2.sqlite                      catalog (stdlib sqlite3)
  sessions/<session-id>/
    captures/<capture-id>/
      setup.set                    full instrument state (reproducible)
      CH1.f32  D0-D15.u16 ...      raw samples (memory-mappable)
      decode-BUS1.parquet|csv      frame table
      screen.png
```

Catalog tables: `sessions` (goal/task, tags, started), `captures` (time, trigger, timebase,
sample rate, record length, segment, idn, notes), `series` (capture, source, kind
analog|logic|math|fft|bode, n, x0, dx, unit, stats json, file), `frames` (capture, bus,
protocol, segment, index, start, stop, status, address/id, data hex, ascii, extras json),
`measurements` (capture, type, source, value, avg/min/max/stddev, count), `screenshots`,
`notes`.

## L5 Context engine (next)
The rule: **summaries up, details on request, always with counts and pointers.**
* Every harvest returns a compact summary (≈ ≤2 k tokens): per series min/max/mean/RMS,
  dominant frequency, edge count, clipping flags; per bus frame count, error frames,
  address/ID histogram, first/last frames; per Bode sweep −3 dB points, peak, phase at fc.
* Drill-down tools: `frames(filter, limit)`, `series_slice(t0, t1, max_points)` (min/max
  decimation), `edges(source, window)`, `compare(capture_a, capture_b)`,
  `store_query(sql)` (read-only, row-limited), `find(anomaly)`.
* Sessions tag data with the task's goal so later prompts retrieve by task, not by time.

## L6 MCP tools
Foundation (done): `connection`, `scpi_search`, `scpi_describe`, `scpi`, `scpi_block`,
`screenshot`. Task groups (next): `setup`, `capture`, `waveform`, `logic`, `decode`,
`measure`, `spectrum`, `bode`, `history`, `generator`, `files`, `store`.

## L7 Skills (later)
* **Ham bench:** CW keying shape, SSB two-tone, AM trapezoid, filter Bode (crystal/IF/audio),
  harmonics sanity check (always via attenuator/dummy load).
* **Reverse engineering:** identify an unknown bus from logic edges (baud/bit-time, idle
  level, framing → protocol guess), I²C device census, SPI flash command capture, pin mapping.
* **Embedded / drone:** UART/GPS/MAVLink, SBUS (inverted UART 100 k 8E2), PWM/DShot timing,
  I²C IMU traffic, rail ripple under motor load, intermittent faults via segmented memory.

## Firmware overlay and overrides
`data/firmware/<model>-<fw>.tree` (from `rs-scope tree-diff --save`) is overlaid on the manual
registry at load: documented commands are tagged with the firmware versions that list them,
and every undocumented firmware header becomes an `undocumented` entry (suffixes generalised,
parameters not validated) so the foundation never blocks a real command.
`data/overrides.json` holds bench-verified corrections: extra enum values (e.g. `DEV:MODE BPL`),
behaviour notes surfaced by `scpi_describe`, and `absent_in` firmware versions (refused before
sending).

## Bench validation
Done on an RTB2004 / FW 03.000 — see [BENCH-NOTES.md](BENCH-NOTES.md). Key consequences:
rejected queries are silent (hence the `*OPC?` sentinel), application modes are exclusive
(`DEV:MODE`), decode queries need the matching `BUS<b>:TYPE`, history may hold fewer segments
than requested, and UINT,16 is the lossless storage format.
