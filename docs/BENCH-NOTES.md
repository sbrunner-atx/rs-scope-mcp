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
