# Bench notes — RTB2004, FW 03.000 (2026-11-23)

Instrument: R&S RTB2004, serial …/101941, firmware 03.000 (2024-10-15).
Options: K1 (I²C/SPI), K2 (UART), K3 (CAN/LIN), K15 (history/segmented memory),
B1 (MSO, 16 logic channels), B6 (generator), B243 (bandwidth upgrade), K36 (Bode).
Setup: LAN, raw SCPI on port 5025. Aux Out (generator) through a 50 ohm feed-through terminator into CH1
(the inputs are 1 Mohm only, so the generator load is set to R50). Pattern generator P0..P3 into the logic
probe D0..D3, logic threshold USER 0.5 V (the pattern outputs are about 1 V).
The instrument also announces itself over mDNS as `_scpi-raw._tcp` ("Rohde&Schwarz RTB2004 - <serial>").

## Results

| Area | Result | Consequence for the stack |
|---|---|---|
| Command tree | `SYSTem:TREE?` = 3202 headers, `;`-separated short forms, suffixes expanded, ~16 s | Stored in `data/firmware/RTB2004-03.000.tree`; 2337 match the manual, 865 are undocumented (→ 415 generalised registry entries) |
| Tree completeness | Not complete: `HCOP:DATA?`, bus `FRAMe<n>` queries work but are absent | Tree absence is not treated as "unsupported" |
| Manual vs firmware | `TRIG:A:HYST` documented but `-113 Undefined header` | `overrides.json` → `absent_in`, refused before sending |
| Rejected queries | **No response at all** (timeout), error in queue | Every query is sent as `X?;*OPC?`: a lone `1` means rejected → error read in ~2 ms |
| Batching | `A?;:B?;:C?;*OPC?` works; rejected queries drop out of the reply | Batch then re-run one-by-one to locate failures; positions preserved |
| Latency | ~0.5–2 ms per simple query | Batching is a convenience, not a necessity |
| Settings snapshot | `SYST:SET?` → ~4.9 kB block; `SYST:SET <block>` restores in ~1 s (not FORMat) | Basis for capture reproducibility and "leave the scope as you found it" |
| Loopback measure | Gen 1 kHz / 1 Vpp sine → MEAS FREQ 1.00103 kHz, PEAK 1.036 V, RMS 0.3453 V | Measurement path verified |
| Waveform transfer | DEF 130 kSa; MAX 6 MSa: REAL,32 24 MB in 1.35 s, UINT,8 0.55 s, UINT,16 (DMAX) 0.65 s | Store as UINT,16 (10-bit ADC → lossless, half of float); volts = YOR + YINC·code verified |
| Single acquisition | `SING;*OPC?` 164 ms | `capture` primitive = SING + *OPC? + harvest |
| Screenshot | PNG 1280×824, ~44 kB, 0.73 s; fully legible | Good enough for Claude to read the display directly |
| History | 50× RUNSingle at 10 kSa → only 40 segments available; out-of-range `HIST:CURR` clamps silently | Always read `ACQ:AVA?` and `HIST:STAR?/STOP?` |
| Timestamps | `TSR:ALL?` (relative s), `TSAB:ALL?` (h,m,s triples), `TSD:ALL?` (y,m,d triples) | Per-segment wall-clock times available for the store |
| Bode (K36) | 100 Hz–1 MHz, 10 pts/dec → 41 points in 6.2 s; FREQ/GAIN/PHAS arrays in ASC (4 digits) or REAL | Use REAL; poll `BPL:STAT?` until STOP |
| Device modes | Bode leaves `DEV:MODE` = `BPL` (not in manual); bus decode then fails `-221 Settings conflict`; leaving BPL turns generator off | Subsystems must own `DEV:MODE` transitions |
| Bus decode | Queries on a bus of another TYPE → `-200`, no response | Decode harvester checks `BUS<b>:TYPE?` first |
| UART decode | Square wave decoded as 0xF0 (correct LSB-first), status `OK`/`INS` | Read path verified; throughput with real traffic still to measure |
| Measurement stats | `MEAS:STAT:VAL:ALL?` ≈ 1000 values; `9.91E+37` = no value | Treat 9.91E+37 as NaN everywhere |

## Round 2: logic probe and pattern generator

| Area | Result | Consequence for the stack |
|---|---|---|
| Pattern generator serial modes | Fixed demo traffic, ~1 V: I2C writes `*idn?` to 0x20 and reads `Rohde&Schwarz - RTB2004`; SPI sends `Rohde&Schwarz`, `RTB2004`; UART `*idn?\n` at 115200 Bd (measured from an 8.68 us bit time), cleanest as 9N1 | Free, repeatable bus traffic for tests and demos without a DUT |
| Logic threshold | `LOG<p>:THR` takes TTL/ECL/CMOS/USER, `LOG<p>:THR:UDL<n>` the volts; the manual merges them into one command | Fixed in `overrides.json` (split + bench command) |
| Manual case typos | `FRAME<n>`, `DISPLAY`, `CLOCK`, `START`, `ENABLE` printed in capitals; firmware short forms are the first 4 letters | Extractor repairs them (`fix_case`) |
| Decode harvest | I2C 6 fields/frame ~230 frames/s, SPI 5 fields/frame ~180 frames/s, UART 3 fields ~215 frames/s | Field-by-field harvesting is fast enough for typical captures |
| Wrong bus type | I2C query on SSPI bus: -200, no response. SPI query on UART bus: empty data, no error | Decode layer must check `BUS<b>:TYPE?` itself |
| History export | `BUS<b>:HISTory:EXPort` writes only the segment timestamp CSV, no decode table; `MMEM:DATA?` downloads it in ~1 ms | Decodes are harvested per segment with FRAMe queries |
| Per-segment decode | `BUS<b>:HISTory:CURRent <k>` re-decodes that segment in ~190–240 ms; short segment records undersample fast buses (SPI garbled at 10 kSa / 2.4 ms) | Check sample rate vs bit rate before trusting segment decodes |
| Segment count | `ACQ:NSIN:COUN` max depends on record length (16 here; 40 and 100 rejected) | Read the limit, do not assume |
| Logic transfer | `DIG0:DATA?` 7.5 MSa UINT,8 in 0.65 s; `LOG1:DATA?` pod (bit n = Dn) 7.5 MSa UINT,8 in 0.9 s; with UINT,16 the pod value sits in the high byte | Store logic as pod bytes (8 channels per byte) |
| Generator load | `WGEN:OUTP:LOAD` is amplitude compensation, not a relay: 1 Vpp set → 1.030 Vpp at the 50 ohm terminator with R50 | Field Guide wiring note; Bode leaves it at R50 |

## Round 3: task tools end to end (2026-11-23)

Run through the MCP tool functions against the RTB2004, then the original setup restored with `setup restore`.

| Tool | Result |
|---|---|
| `capture` CH1 + POD1 + BUS1 | 4.3 s total: SINGle 0.39 s, CH1 7.5 MSa (999.9 Hz, 1.039 Vpp), D0-D7 7.5 MSa with edge statistics, both I2C frames (`*idn?`, `Rohde&Schwarz - RTB2004`) |
| `data` frames / edges / series | Frame table, an edge window, and a 6-row min/max view of 7.5 MSa, all from the local store |
| `measure` | FREQ 1000.6 Hz, PEAK 1.032 V, RMS 0.351 V |
| `spectrum` 0-200 kHz | Odd harmonics of the 10 kHz square wave, RBW 1 kHz, noise floor -50 dBV |
| `bode` 100 Hz-1 MHz | 41 points in 6.8 s, flat 0 dB (loopback), load R50 |
| `history` | 9 segments available, 8 harvested with CH1 and I2C each; SQL shows 2/2 OK frames in every segment |
| `setup` save / restore | Restored without errors; generator off, pattern generator back to its original state |

## Round 4: demo mode and mask testing (2026-11-23)

| Area | Result | Consequence |
|---|---|---|
| LAN clients | The RTB2 serves **one** SCPI client at a time; a second connection gets no answers | The MCP server owns the single connection; the demo runs on it in a background thread and other tools wait |
| Arbitrary waveform | Upload a CSV (`in s,C1 in V` header, time,value rows) with `MMEM:DATA`, then `WGEN:FUNC ARB` **before** `WGEN:ARB:FILE:NAME` / `OPEN` | Heartbeat scene; the order is in `overrides.json` |
| Mask test | Learn from a clean signal (`MASK:CHC`), margins in divisions, `MASK:TEST RUN`; counts via `MASK:COUN?` / `MASK:VCO?`; `MASK:CAPT FAILED` keeps only failures in history | `mask` tool (create, test, status, off) |
| Mask conflict | `MASK:TEST RUN` intermittently -221 after timebase changes; off / single / re-learn clears it | Automatic retry in `mask_start` |
| Trigger with noise | Edge trigger fires on the falling slope through noise; trace 180 degrees off the mask | `TRIG:A:EDGE:FILT:HFR ON` for noisy signals |
| Demo | 11 scenes, full pass in 115 s, setup restored, uploaded waveform deleted | `docs/DEMO.md`, screenshots in `docs/demo/` |

## Round 5: analysis for the skills (2026-11-23)

| Check | Truth | Result |
|---|---|---|
| `data envelope` on generator AM | set 80 % | 74.6-75.3 % for every window size: the generator's real depth, not the method |
| `data envelope` on 100 % square-wave AM | hard keying | rise and fall under one 20 us window; FFT shows sidebands every 400 Hz to +/-1.8 kHz |
| `data identify` on pattern-generator UART | 115200 Bd | UART, 115200 Bd measured and standard, idle high |
| `data identify` on I2C | SCL D0, SDA D1 | I2C, clock 301 kHz |
| `data identify` on SPI | CLK D0, MOSI D1, MISO D2, CS D3 | SPI, CS D3 active low, data D1/D2 (after detecting CS as the line that holds while the clock runs) |
| `data identify` on 50 Hz pattern, duty 7.5 % | servo | PWM 50 Hz, 1.6 ms (the pattern generator's duty step), flagged servo/ESC |
| `measure D0 FREQ, PPW, PDCY` | servo | 49.999 Hz, 1.600 ms, 8.0 % |

## Round 6: acceptance suite (2026-11-23)

`scripts/acceptance_test.py` drives the MCP server over stdio, as Claude Desktop does: 40 checks in five
groups (foundation, analog, logic, mask, demo) plus restore. Running the groups back to back found
state that one feature leaves for the next:

| Finding | Fix |
|---|---|
| Mode changes (`DEV:MODE`, `SPEC`, `BPL:ENAB`, `MASK:STAT`) take about 1 s; commands sent meanwhile fail with -221 | The instrument layer waits with `*OPC?` after them |
| The Bode application sets the memory to LIM (75 kSa) and keeps it; afterwards `ACQ:MEM AUT` is refused (-221), `MAN` works | `bode()` saves the complete setup first; leaving BPL mode (any way) restores it |
| `MASK:CHCopy` switches the acquisition to ENVelope; after Bode it could copy an empty envelope | `mask create` builds the envelope itself, returns to REFResh and self-checks the new mask against the reference signal |
| The old -221 recovery re-learned the mask from the signal under test, so a noisy signal passed | Never re-learn automatically; retry the start once, otherwise report |
| `capture` enabled a bus decoder only after acquiring, so that record was not decoded | Decoders are switched on before the acquisition |
| A stopped record may not contain the source (`measure` after a capture with the channel off) | `measure` takes one fresh single acquisition when stopped |
| History queries are rejected while acquiring | `history` stops first; `mask test` freezes stored failures |
| Short records starting mid-transfer fooled the idle-level test | Idle levels from the longest clock pause (clocked buses) or the level before the first edge |
| Unclocked parallel decode catches pins mid-transition (15 -> 8 -> 0) | Expected; use the clocked parallel decoder when a clock exists |
| The pattern generator's SPI pattern toggles chip select pseudo-randomly | Frames end mid-text with status INCL; correct decoder behavior |
