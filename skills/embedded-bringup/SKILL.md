---
name: embedded-bringup
description: >
  Board bring-up and debugging for microcontroller, IoT, robotics and drone electronics with an
  R&S RTB2 / RTB2000 through rs-scope-mcp: power rails and ripple under load, power-up sequencing
  and reset timing, clocks, PWM, servo and ESC timing, sensor buses (I2C/SPI health and timing),
  GPS and telemetry UARTs, intermittent faults with mask tests and segmented memory, and comparing
  a new board revision against the last one. Use when the operator is bringing up a board,
  chasing a glitch, or validating a revision. Read scope-operating first; for undocumented
  interfaces use bus-reverse-engineering.
---

# Embedded and drone bring-up

Work through the board in the order it powers up: rails, reset and clocks, then the interfaces.
Use one `session` per board and revision (`session(action="start", goal="rev C bring-up")`), and
give every capture a `note`, so revisions can be compared later.

## Power rails

**DC level and ripple.** 10x probe with the short ground spring at the rail's own capacitor.
```
scpi(["CHAN1:COUP ACL", "CHAN1:BAND B20", "CHAN1:SCAL 0.02", "TIM:SCAL 5e-6", "TRIG:A:MODE AUTO"])
capture(sources=["CH1"], note="3V3 ripple, idle")
```
- Report ripple Vpp and its frequency (the regulator's switching frequency shows up as
  `frequency_hz`), and any spikes at the switching edges.
- Then the DC level with `CHAN1:COUP DCL` and a coarser scale, or `measure(types=["MEAN"])`.
- The 20 MHz limit (`B20`) removes probe pick-up that is not on the rail; say when it was on.

**Under load.** Repeat with the motors spinning, radios transmitting, LEDs on. Compare the two
captures: `data(action="sql", sql="select note, json_extract(stats,'$.vpp') from series s join
captures c on c.id=s.capture_id where c.session_id='<session>'")`.

## Power-up sequence and reset

1. One channel per rail (CH1..CH3) plus reset on CH4 or a logic line. Trigger on the first rail:
   `TRIG:A:SOUR CH1`, rising, level 10 % of the rail, `TRIG:A:MODE NORM`, `TIM:SCAL 2e-3`.
2. Ask the operator to power the board on, with `capture(sources=["CH1","CH2","CH3","CH4"])` armed
   (single acquisition).
3. Delays between rails: `measure(source="CH1", types=["DEL"])` after
   `scpi(["MEAS1:SOUR CH1,CH2"])`, or read edges from `data(action="series", ...)` windows.
4. Check reset releases after the rails are in regulation, and how long the rail takes to rise
   (`RTIM`). Brown-out problems show as a rail that dips during the first current surge.

## Clocks

- Crystal oscillators load easily: use a 10x probe, prefer the buffered clock output (MCO, CLKOUT)
  when the MCU has one. `measure(types=["FREQ","PEAK"])`; compare frequency with the nominal value
  in ppm.
- A clock on a logic line: `capture(sources=["POD1"])` then `data(action="identify", ...)` gives
  frequency and duty.

## PWM, servos and ESCs

- `measure(source="D0", types=["FREQ","PPW","PDCY"])` gives frequency, high time and duty of a logic
  line directly (bench: 50.0 Hz, 1.60 ms, 8.0 % on the RTB2 pattern generator).
- `data(action="identify", ...)` on a pod capture flags 1-2 ms pulses at 40-500 Hz as servo/ESC
  style. Capture at least 5 periods (50 Hz: `TIM:SCAL 1e-2`).
- Several motor outputs: capture POD1 and compare `median_pulse_s` per line in the summary.
- DShot: 16-bit bursts at 150/300/600 kbit/s; bit period 6.67/3.33/1.67 us, a 1 is about 75 % high,
  a 0 about 37 %. Read bit widths from `data(action="edges", ...)`.

## Sensor buses

- Decode and check health: set up I2C or SPI (see bus-reverse-engineering), `capture(["BUS1"])`,
  then `data(action="frames", bus=1, status="not_ok")`. NACKs on a device that should answer mean
  wrong address, device in reset, or a bus problem.
- **I2C timing:** put SDA or SCL on an analog channel and `measure(types=["RTIM"])`. The rise time
  limit is 1000 ns at 100 kHz and 300 ns at 400 kHz; slower means the pull-ups are too weak for the
  bus capacitance.
- Clock frequency: the decoded bus summary or `identify` on the pod gives it.

## GPS, telemetry and RC links

| Link | Settings |
|---|---|
| GPS NMEA | UART 9600, 38400 or 115200 Bd, 8N1; text starting `$G` |
| MAVLink | UART 57600 or 115200 Bd, 8N1; frames start 0xFE (v1) or 0xFD (v2) |
| SBUS | UART 100000 Bd, 8 data, even parity, 2 stop bits, inverted: `BUS1:UART:POL IDLL`, `PAR EVEN`, `SBIT B2` |
| CRSF / ELRS | UART 420000 Bd, 8N1 |

Harvest with `capture(["BUS1"])` and look at `ascii` in the summary for text protocols, or the
hex bytes for binary ones.

## Intermittent faults

1. **Catch the bad one with a mask.** Show a known-good signal, `mask(action="create",
   source="CH1", x_margin_div=0.05, y_margin_div=0.2)`, then `mask(action="test",
   acquisitions=5000, keep_failed=true)`. Only failing acquisitions stay in segmented memory.
2. **Look at the failures:** `history(action="harvest", sources=["CH1","BUS1"])`; each row is one
   failing acquisition.
3. **Glitches and runts:** trigger on them directly: `TRIG:A:TYPE RUNT` with
   `TRIG:A:LEV1:RUNT:LOW` / `UPP`, or `TRIG:A:TYPE WIDT` for pulses shorter than they should be,
   with `TRIG:A:MODE NORM`.
4. **Noisy signals:** `TRIG:A:EDGE:FILT:HFR ON` keeps noise from triggering on the wrong slope.

## Comparing board revisions

- Save the setup once (`setup(action="save", note="bring-up setup")`) and restore it before
  measuring each revision, so the settings are identical.
- Capture the same test points with the revision in the `note`.
- Compare with SQL over the series stats (Vpp, RMS, frequency) and frame counts, and report only
  what changed beyond normal variation.

## Report

Per test point: what was measured, the value, the limit or expectation, pass or fail, and the
capture id. List what still needs checking.
