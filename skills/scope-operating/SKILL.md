---
name: scope-operating
description: >
  Operating discipline for driving a Rohde & Schwarz RTB2 / RTB2000 oscilloscope through
  rs-scope-mcp: leave the scope as you found it, stay safe with generator outputs and probes,
  capture first and analyze from the stored data, pick sample rates and triggers that give
  trustworthy numbers, and report results with values and capture ids. Use for every task that
  touches the oscilloscope (measuring, decoding, FFT, Bode, mask tests, segmented memory,
  screenshots), and before the ham-bench, bus-reverse-engineering and embedded-bringup skills.
  Bench-verified on an RTB2004 with firmware 03.000.
---

# Operating the RTB2 with rs-scope-mcp

## The four rules

1. **Leave the scope as you found it.** Start every task with `setup(action="save")` and keep the
   capture id. Finish with `setup(action="restore", capture_id=..., confirm=true)` unless the
   operator wants to keep the new setup. Restoring also switches the generators off first.
2. **Capture once, analyze many times.** `capture` stores the full record locally and returns a
   summary. Answer follow-up questions with `data` on the stored capture, not with new
   acquisitions, unless the signal has changed.
3. **Numbers, not impressions.** Report measured values with units and the capture id they came
   from. Take a `screenshot` when the operator wants to see the screen, or to show a result; do not
   read values off a screenshot when a measurement or the stored data can give them.
4. **Guarded means ask.** Commands that drive signals into a circuit (generator and pattern
   generator outputs, Bode sweeps, the demo), `*RST`, self-alignment, file deletion, network
   settings and setup restores need `confirm=true`. Only send it after the operator has said the
   wiring is right for it. Never enable an output into an unknown circuit.

## Start of a session

```
connection(action="connect", host="<scope ip>")     # once; identity and options come back
session(action="start", goal="<what the operator wants to find out>")
setup(action="save", note="before <task>")
```

`connection` lists the installed options. Check them before promising a feature: B1 logic probe
(D0..D15), B6 generator (Aux Out and pattern generator P0..P3), K1 I2C/SPI, K2 UART, K3 CAN/LIN,
K15 history and segmented memory, K36 Bode plot.

**One client at a time.** The RTB2 serves a single LAN client. If the connection hangs, another
program (a web browser on the scope's page, another script, a demo) holds it. While the `demo` tool
runs, other tools refuse; stop it first.

## Safety at the bench

- **Inputs are 1 Mohm only.** For 50 ohm systems use a feed-through terminator on the input and
  tell the generator: `WGEN:OUTP:LOAD R50`. With the terminator and `HIGHz`, amplitudes read double
  what was set; without a terminator and `R50`, half.
- **Probe ratings first.** Ask what is being probed when the voltage is unknown. Mains, motor
  drives, switch-mode primaries and tube equipment need a differential probe, not the ground clip.
- **The ground clip is ground.** It connects to protective earth through the scope. Clipping it to
  a non-ground node of a mains-connected circuit shorts that node.
- **Transmitters never go straight in.** RF power goes through a dummy load and attenuators sized
  for it (see ham-bench). The input limit is a few volts at 1 Mohm.

## The capture loop

1. **Set up** channels, timebase and trigger with `scpi` (validated; batches are all-or-nothing):
   `CHAN1:SCAL`, `CHAN1:OFFS`, `TIM:SCAL`, `TRIG:A:SOUR`, `TRIG:A:LEV1`, `TRIG:A:MODE NORM|AUTO`.
   Use `scpi_search` and `scpi_describe` to find commands; `scpi_describe` also shows bench notes.
2. **Acquire and harvest** in one call:
   `capture(sources=["CH1","POD1","BUS1"], note="...")`.
   Sources: `CH1`..`CH4` (volts), `POD1` (D0-D7) / `POD2` (D8-D15) logic, `BUS1` / `BUS2` decodes.
   `acquire="single"` (default) takes one fresh acquisition at full memory.
3. **Read the summary**: levels, Vpp, RMS, frequency, clipping, logic edges per line with pulse
   widths, frame counts, statuses, addresses, ASCII.
4. **Drill down** only where the question needs it:
   - `data(action="series", capture_id, source="CH1", t_start, t_end, max_points=400)`
   - `data(action="edges", capture_id, source="D0-D7", t_start, t_end)`
   - `data(action="frames", capture_id, bus=1, status="not_ok" | contains="..." | address=...)`
   - `data(action="envelope", ...)` for modulated or keyed carriers
   - `data(action="identify", ...)` to guess what unknown logic lines carry
   - `data(action="sql", sql="select ...")` across captures of the session
5. **Report**, then restore the setup.

## Getting trustworthy numbers

| Situation | Do this |
|---|---|
| Timing or edges | Sample rate at least 5x the signal bandwidth; check `sample_rate` in the capture summary |
| Bus decode | Sample rate at least 10x the bit rate. Segmented memory uses short records and can undersample fast buses; the decode tool warns |
| Noisy signal triggers on the wrong slope | `TRIG:A:EDGE:FILT:HFR ON` (5 kHz low-pass in the trigger path) or `NREJ ON` |
| Rare event | `TRIG:A:MODE NORM` so the screen only updates on a trigger, then segmented memory (`history`) |
| Clipped trace | The summary says `clipping`; increase `CHANn:SCAL` before trusting Vpp or RMS |
| "No value" | Measurements return `None` (the scope's 9.91E+37): the signal is not suitable for that measurement on this screen |
| FFT | `spectrum(...)` handles the scope's coupled span/RBW/timebase; read `rbw_hz` and keep it under the spacing you need to resolve |

## Application modes are exclusive

FFT, Bode plot, XY and mask test each take over the screen (`DEVice:MODE`). In Bode mode, bus
decode and many settings fail with `-221 Settings conflict`. `capture` and `history` switch back to
YT when they need decodes; when a command fails with -221, check the mode first.

The Bode application changes the memory mode (to a 75 kSa limit), the channel scale and coupling,
and the generator load. rs-scope-mcp saves the setup before every `bode` sweep and restores it when
the scope leaves Bode mode, so measurements afterwards run on the setup from before the sweep.

## Pass/fail and rare faults

- `mask(action="create", source="CH1", x_margin_div=0.05, y_margin_div=0.2)` learns a tolerance
  envelope from the signal on screen and self-checks it against that signal; a `warning` means the
  mask does not fit (fix trigger or timebase and create it again). Show the known-good signal first,
  and never learn a mask from the signal you are about to judge.
- `mask(action="test", acquisitions=1000, keep_failed=true)` counts failures; with `keep_failed`
  only failing acquisitions stay in segmented memory.
- `history(action="harvest", sources=["CH1","BUS1"])` walks the stored segments and returns one
  row per segment, so the failing ones stand out.

## When something fails

| Symptom | Likely cause |
|---|---|
| `validation` error | Wrong header or suffix; `scpi_search` for the right one |
| `instrument` error `-113` | Command not in this firmware (see `scpi_describe` notes) |
| `-200` on a decode query | `BUS<b>:TYPE` is a different protocol |
| `-221 Settings conflict` | Another application mode is active, or a mask test needs re-learning (the mask tool retries) |
| `-222 Data out of range` | Value outside what the current settings allow (FFT span, segment count) |
| Timeout | Another client holds the connection, or the scope is busy (self-alignment, long sweep) |

## Reporting

Lead with the answer and its numbers: "CH1 is 999.9 Hz, 1.04 Vpp; 0 of 1000 acquisitions hit the
mask (capture 20261123-112426-fc7a3c)." Say what was not checked. Offer a screenshot when it helps
the operator see it.
