# Rohde & Schwarz RTB2 / RTB2000 SCPI API: A Field-Tested Reference

A complete, **bench-verified** reference to the SCPI remote-control interface of the Rohde & Schwarz RTB2 and RTB2000 oscilloscopes. It merges three sources: the command chapter of the user manual, the command list the firmware itself reports (`SYSTem:TREE?`), and behavior measured on a real instrument. Where the three disagree, this reference says so.

> **Tested against:** R&S RTB2004, firmware **03.000** (2024-10-15), all options installed (K1, K2, K3, K15, B1, B6, B243, K36), on 2026-11-23. Manual: user manual 1333.1611.02-13 (FW 3.xx). **749 documented command blocks (962 headers incl. aliases)**; firmware tree 03.000: 3,202 headers, of which **408 undocumented command families** are listed in Appendix A.

> **RTB2 and RTB2000 are the same instrument.** The RTB2 is the rebranded RTB2000: identical hardware, a different enclosure color scheme, and firmware that runs on either. Everything here applies to the RTB2002 and RTB2004 under both names. The firmware has moved ahead of the manual several times; when in doubt, ask the instrument (`SYSTem:TREE?`, or `rs-scope tree-diff`).

> **Independent project, not affiliated with or endorsed by Rohde & Schwarz.** R&S and RTB2000 are trademarks of Rohde & Schwarz GmbH & Co. KG. This reference is maintained alongside [`rs-scope-mcp`](https://github.com/sbrunner-atx/rs-scope-mcp). Corrections welcome.

## 1. Transport and connection

- **Raw SCPI socket:** TCP port **5025** (the instrument's *IP Port*). No VISA library is needed. VXI-11 is also offered on port 1024.
- **Discovery:** the instrument announces itself over mDNS as `_scpi-raw._tcp` (`Rohde&Schwarz RTB2004 - <serial>`). Setup: *Menu, Setup, Interface, Ethernet, Parameter*.
- **Framing:** commands and ASCII responses end with a newline. Binary data (waveforms, screenshots, files, settings) uses IEEE 488.2 definite-length blocks: `#<n><length><bytes>` then a newline.
- **Latency:** about 0.5 to 2 ms per simple query on a LAN. Many queries can share one line.

## 2. Command syntax

- Each node has a **long form** and a **short form** (the uppercase part): `CHANnel1:SCALe` = `CHAN1:SCAL`. Case does not matter.
- **Numeric suffixes** select instances: `CHANnel<m>` (1..4), `BUS<b>` (1..2), `MEASurement<m>` (1..8), `DIGital<m>` (0..15), frame and word indexes `<n>`, `<o>` (1..count). An omitted suffix means 1.
- **Optional nodes** are in brackets: `FORMat[:DATA]` accepts `FORM` and `FORM:DATA`.
- **Several commands per line:** separate with `;:`, for example `CHAN1:SCAL?;:TIM:SCAL?;*OPC?`. Responses come back `;`-separated in the same order.
- Type column below: **Q** query only, **S** set only, **S/Q** set and query, **E** event (no value), **U** undocumented (from the firmware tree).

## 3. Field-tested behavior (read this first)

These rules come from the bench, not the manual. They are built into `rs-scope-mcp`.

### 3.1 Talking to the instrument

- **One client at a time.** The RTB2 serves a single LAN client on port 5025. A second connection (the instrument's web page in a browser, another script) is accepted but gets no answers until the first one closes. Share one connection inside a program.
- **A rejected query sends no response at all.** An undefined header, a decode query on a bus of another type, or a value out of range simply produces silence until the client times out; the reason is in the error queue. Append `*OPC?` to every query (`X?;*OPC?`): the instrument still answers `1`, so a lone `1` means `X?` was rejected and `SYSTem:ERRor?` tells you why, within about 2 ms.
- **Read the error queue after writes.** Settings are rejected silently too (`-221 Settings conflict`, `-222 Data out of range`). Drain `SYSTem:ERRor?` until `0,"No error"`, or use `SYSTem:ERRor:ALL?`.
- **No value is 9.91E+37.** Measurements and statistics report `9.91E+37` when there is nothing to measure. Treat it as NaN.

### 3.2 Application modes

- **Applications are exclusive modes, and switching takes time.** `DEVice:MODE` switches between YT, XY, FFT, mask test and others. The Bode plot reads back `BPL`, which the manual does not list. A mode change (`DEVice:MODE`, `SPECtrum:STATe`, `BPLot:ENABle`, `MASK:STATe`, `DISPlay:MODE`) takes about one second: send `*OPC?` after it before the next command. In BPL mode, bus decode and many settings fail with `-221`.
- **The Bode application leaves changes behind.** Enabling it sets the memory to `ACQuire:MEMory LIMited` (75 kSa), changes channel scale and coupling, and sets the generator load to 50 ohm. Afterwards `ACQuire:MEMory AUTomatic` is refused with `-221`; only restoring a setup saved before the sweep (`SYSTem:SET`) brings full memory back. Leaving BPL switches the generator off.
- **FFT mode owns the timebase.** Enable `SPECtrum` first, then set start and stop. Span, resolution bandwidth, window length and sample rate are coupled, so the instrument adjusts them in steps; apply start/stop again until they settle (two or three rounds on FW 03.000).

### 3.3 Acquisition, measurements and history

- **Full memory needs a stopped acquisition.** `...:DATA:POINts MAX` and `DMAX` only take effect when stopped. Use `SINGle;*OPC?` first.
- **Measure on a fresh acquisition.** Automatic measurements on a stopped record can return the values of an older screen. Run `SINGle;*OPC?` or keep the scope running before reading `MEASurement<m>:RESult?`.
- **History queries need a stopped scope.** While acquiring, `HISTory` and `ACQuire:AVAilable?` queries are rejected (`-200`); send `STOP` first. The number of segments `ACQuire:NSINgle:COUNt` accepts depends on the record length (100 and 40 were refused with `-222` at full memory, 16 worked).
- **History holds what it holds.** Fifty single acquisitions at 10 kSa left 40 segments; read `ACQuire:AVAilable?`. Out-of-range `HISTory:CURRent` indexes are clamped without an error; the valid range is `HISTory:STARt?` to `HISTory:STOP?` (0 is the newest).

### 3.4 Decoding and logic

- **Decode queries need the matching bus type.** `BUS<b>:I2C:...`, `BUS<b>:UART:...` and the rest only answer when `BUS<b>:TYPE` is that protocol.
- **Enable the decoder before the acquisition.** `BUS<b>:STATe ON` must be set before `SINGle`; frames of an acquisition taken earlier are not decoded. Keep the sample rate at least ten times the bit rate.
- **Logic thresholds come in two parts.** `LOGic<p>:THReshold` takes the mode (`TTL`, `CMOS`, `ECL`, `USER`...), `LOGic<p>:THReshold:UDLevel<n>` the user level in volts. The manual merges them into one command.
- **The pattern generator's test buses.** `PGENerator:FUNCtion` I2C, SPI and UART patterns on P0..P3 are useful references. The UART pattern runs at 115200 Bd and decodes cleanly as 9 data bits, no parity. The SPI pattern's chip select is pseudo-random, so frames can start or end incomplete. An unclocked parallel decode shows one-sample values while lines change; use the clocked parallel bus when a clock exists.

### 3.5 Generator

- **The inputs are 1 Mohm only.** For 50 ohm signals use a feed-through terminator. `WGENerator:OUTPut:LOAD R50|HIGHz` does not change the 50 ohm source: it tells the generator what load to expect so the set amplitude appears there. Measured: 1 Vpp set with `R50` gives 1.030 Vpp at the terminator. By the same source impedance, `HIGHz` into a terminator reads half the set amplitude, and `R50` into an open 1 Mohm input reads double.
- **Arbitrary waveforms: function first, then the file.** Send `WGENerator:FUNCtion ARBitrary` before `WGENerator:ARBitrary:FILE:NAME` and `...:FILE:OPEN`; otherwise opening fails with `-200`. A CSV file has the header `in s,C1 in V`, then one `time,value` row per point.

### 3.6 Mask test

- **Learning a mask changes the acquisition.** `MASK:CHCopy` copies the channel into the mask and switches `ACQuire:TYPE` to `ENVelope`. Learn from an envelope of a few hundred milliseconds of the known-good signal, then check the new mask against that signal before testing anything else.
- **Noisy signals need a stable trigger.** With noise on the signal, an edge trigger can fire on the wrong slope and the mask appears shifted by half a period. `TRIGger:A:EDGE:FILTer:HFReject ON` fixes it.
- **`MASK:TEST RUN` sometimes answers `-221`.** Intermittently, after a mode change. `STOP`, `SINGle`, `RUN` and send it again; do not learn the mask again, because the signal on screen may be the one under test.

### 3.7 Setup and command tree

- **Snapshot and restore the whole setup.** `SYSTem:SET?` returns the complete setup as a block of about 5 kB; sending it back with `SYSTem:SET <block>` restores it in about one second. `FORMat` is not part of it.
- **The firmware command tree is useful but incomplete.** `SYSTem:TREE?` (about 60 kB, 16 s) lists 3,202 headers but omits working commands such as `HCOPy:DATA?` and the bus `FRAMe<n>` queries. 95 documented commands are not in the tree; most of them still work.

All of these are checked by `scripts/acceptance_test.py`, a 40-check suite that drives the installed connector on a real instrument. On FW 03.000 all 40 pass.

## 4. Data transfer

`FORMat[:DATA]` selects `ASCii`, `REAL,32` or `UINTeger,8|16|32`; `FORMat:BORDer` the byte order (default MSB first). For UINT data: **volts = YORigin + YINCrement × code** (`...:DATA:YORigin?`, `...:DATA:YINCrement?`), verified on FW 03.000. The ADC has 10 bits (`YRESolution? = 10`), so `UINT,16` is lossless at half the size of `REAL,32`.

| Transfer (CH1, LAN) | Samples | Bytes | Time |
|---|---:|---:|---:|
| ASCii, DEFault | 130,432 | 1.63 MB | 0.36 s |
| REAL,32, DEFault | 130,432 | 0.52 MB | 0.05 s |
| UINT,8, MAXimum | 6,000,070 | 6.0 MB | 0.55 s |
| UINT,16, DMAXimum | 5,999,872 | 12.0 MB | 0.65 s |
| REAL,32, MAXimum | 6,000,070 | 24.0 MB | 1.35 s |
| Screenshot PNG 1280 × 824 |  | 44 kB | 0.73 s |
| UART frames, 3 fields each (batched) | 13 frames |  | 0.06 s (224 frames/s) |
| Bode sweep 100 Hz to 1 MHz, 10 pts/decade | 41 points |  | 6.2 s |

## 5. Commands that act outside the screen

`rs-scope-mcp` requires an explicit confirmation before sending these, because they drive signals into a circuit, discard settings, delete files, or can drop the network connection:

- `*RST|SYSTem:PRESet`: resets every instrument setting.
- `*CAL?|CALibration`: runs self-alignment (several minutes, inputs must be disconnected).
- `WGENerator:OUTPut[:ENABle]` (when set to `ON|1`): drives the generator output into the circuit.
- `PGENerator:PATTern:STATe` (when set to `ON|1`): drives the pattern generator pins into the circuit.
- `PGENerator:MANual:STATe<s>`: drives a pattern generator pin into the circuit.
- `BPLot:(STATe|ENABle)` (when set to `ON|RUN|1`): a Bode sweep drives the generator output into the circuit.
- `MMEMory:(DELete|RDIRectory|MOVE)`: deletes or moves files on the instrument.
- `MMEMory:DATA`: overwrites a file on the instrument.
- `SYSTem:COMMunicate:`: changes interface/network settings and can drop this connection.
- `DIAGnostic:UPDate`: firmware update.
- `SYSTem:EDUCation`: education-mode lock settings.
- `SYSTem:(DATE|TIME|NAME)`: changes system identity/clock.

## 6. Command reference

Grouped by the manual's chapters. The last column is the manual page. "Bench" lines are verified behavior on FW 03.000.

### 6.1 Common commands

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `*CAL?` | Q |  | Performs a self-alignment of the instrument and then generates a status response. | 308 |
| `*CLS` | S |  | Clear status Sets the status byte (STB), the standard event register (ESR) and the EVENt part of the QUEStionable and the OPERation registers to zero. | 309 |
| `*ESE` | S/Q | `<Value>` range 0 to 255 | Event status enable Sets the event status enable register to the specified value. | 309 |
| `*ESR?` | Q | returns `<Contents>` range 0 to 255 | Event status read Returns the contents of the event status register in decimal form and then sets the reg- ister to zero. | 309 |
| `*IDN?` | Q | returns `<ID>` "Rohde&Schwarz,<device type>,<serial number>,<firmware ver- sion>" | Identification Returns the instrument identification. | 309 |
| `*OPC` | S/Q |  | Operation complete Sets bit 0 in the event status register when all preceding commands have been execu- ted. | 309 |
| `*OPT?` | Q | returns `<Options>` The query returns a list of options. | Option identification query Queries the options included in the instrument. | 310 |
| `*PSC` | S/Q | `<Action>` `0` · `1` | Power on status clear Determines whether the contents of the ENABle registers are preserved or reset when the instrument is switched on. | 310 |
| `*RST` | S |  | Reset Sets the instrument to a defined default status. | 310 |
| `*SRE` | S/Q | `<Contents>` range 0 to 255 | Service request enable Sets the service request enable register to the indicated value. | 310 |
| `*STB?` | Q |  | Status byte query Reads the contents of the status byte in decimal form. | 311 |
| `*TRG` | E |  | Trigger Triggers all actions waiting for a trigger event. | 311 |
| `*WAI` | E |  | Wait to continue Prevents servicing of the subsequent commands until all preceding commands have been executed and all signals have settled (see also command… | 311 |

### 6.2 Waveform setup

#### Automatic setup (16.4.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `AUToscale` | E |  | Performs an autoset process for analog channels: analyzes the enabled analog chan- nel signals, and adjusts the horizontal, vertical, and trigger settings to display… | 312 |

#### Starting and stopping acquisition (16.4.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `RUN`<br>`RUNContinous` | E |  | Starts the continuous acquisition. | 312 |
| `SINGle`<br>`RUNSingle` | E |  | Starts a defined number of acquisitions. | 312 |
| `ACQuire:NSINgle:COUNt` | S/Q | `<NSingleCount>` range 1 to maximum number that depends on the record length.; *RST **1** | Sets the number of waveforms acquired with RUNSingle.<br>Bench: The history may hold fewer segments than requested (50 requested -> 40 available at 10 kSa); read ACQuire:AVAilable?.<br>Bench: The maximum depends on the record length: 40 and 100 were rejected (-222) where 16 was the limit. | 312 |
| `STOP` | E |  | Stops the running acquistion. | 313 |
| `ACQuire:STATe` | S/Q | `<AcquisitionState>` `RUN` · `STOPping` · `COMPlete` · `BREak` | Sets or queries the acquisition state of the instrument. | 313 |

#### Vertical settings (16.4.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `CHANnel<m>:STATe` | S/Q | suffix `<m>` 1..4<br>`<State>` `ON` · `OFF` | Switches the channel signal on or off. | 314 |
| `CHANnel<m>:AON` | E |  | Switches all analog channels on. | 314 |
| `CHANnel<m>:AOFF` | E |  | Switches all analog channels off. | 314 |
| `CHANnel<m>:SCALe` | S/Q | suffix `<m>` 1..4<br>`<Scale>` range 1e-3 to 10 (without probe attenuation); unit V/div; *RST **5e-3** | Sets the vertical scale for the indicated channel. | 314 |
| `CHANnel<m>:RANGe` | S/Q | suffix `<m>` 1..4<br>`<Range>` range 8e-3 to 80 (without probe attenuation); unit V; *RST **40e-3** | Sets the voltage range across the all vertical divisions of the diagram. | 315 |
| `CHANnel<m>:POSition` | S/Q | suffix `<m>` 1..4<br>`<Position>` range -5 to 5; unit div; *RST **0** | Sets the vertical position of the waveform in divisions. | 315 |
| `CHANnel<m>:OFFSet` | S/Q | suffix `<m>` 1..4<br>`<Offset>` range Depend on vertical scale and probe attenuation.; unit V; *RST **0** | Sets the offset voltage, which is subtracted to correct an offset-affected signal. | 315 |
| `CHANnel<m>:COUPling` | S/Q | suffix `<m>` 1..4<br>`<Coupling>` `DCLimit` · `ACLimit` · `GND` | Selects the connection of the indicated channel signal - coupling and termination.<br>Bench: The RTB2 inputs are 1 Mohm only (no internal 50 ohm). For a 50 ohm system use an external feed-through terminator and set WGENerator:OUTPut:LOAD R50 when the generator drives it. | 316 |
| `CHANnel<m>:BANDwidth` | S/Q | suffix `<m>` 1..4<br>`<BandwidthLimit>` `FULL` · `B20`; *RST **FULL** | Selects the bandwidth limit for the indicated channel. | 316 |
| `CHANnel<m>:POLarity` | S/Q | suffix `<m>` 1..4<br>`<Polarity>` `NORMal` · `INVerted`; *RST **NORM** | Turns the inversion of the signal amplitude on or off. | 317 |
| `CHANnel<m>:SKEW` | S/Q | suffix `<m>` 1..4<br>`<Skew>` unit s | Sets a delay for the selected channel. | 317 |
| `CHANnel<m>:ZOFFset[:VALue]` | S/Q | suffix `<m>` 1..4<br>`<ZeroOffset>` unit V; *RST **0** | Sets the zero offset. | 317 |
| `CHANnel<m>:WCOLor` | S/Q | suffix `<m>` 1..4<br>`<WaveformColor>` `TEMPerature` · `RAINbow` · `FIRE` · `DEFault`; *RST **DEF** | Selects the color scale for the waveform color. | 318 |
| `CHANnel<m>:THReshold` | S/Q | suffix `<m>` 1..4<br>`<Threshold>` unit V | Threshold value for digitization of analog signals. | 318 |
| `CHANnel<m>:THReshold:FINDlevel` | E | suffix `<m>` 1..4<br>`<FindLevel>`  | The instrument analyzes the channel and sets the threshold for digitization. | 319 |
| `CHANnel<m>:THReshold:HYSTeresis` | S/Q | suffix `<m>` 1..4<br>`<ThresholdHysteresis>SMALl | MEDium | LARGe` *RST **SMAL** | Defines the size of the hysteresis to avoid the change of signal states due to noise. | 319 |
| `CHANnel<m>:LABel` | S/Q | suffix `<m>` 1..4<br>`<Label>` String value String with max. | Specifies a name for the selected channel. | 319 |
| `CHANnel<m>:LABel:STATe` | S/Q | suffix `<m>` 1..4<br>`<State>` `ON` · `OFF`; *RST **OFF** | Shows or hides the channel name. | 320 |

#### Passive probes (16.4.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `PROBe<m>:SETup:ATTenuation:UNIT` | S/Q | suffix `<m>` 1..4<br>`<Unit>` `V` · `A` | Selects the unit that the probe can measure. | 320 |
| `PROBe<m>:SETup:ATTenuation:MANual` | S/Q | suffix `<m>` 1..4<br>`<ManualAttenuation>` range 0.0001 to 10e6 | Sets the attenuation of the probe. | 320 |
| `PROBe<m>:SETup:GAIN:UNIT` | S/Q | suffix `<m>` 1..4<br>`<Unit>` `V` · `A` | Selects the unit that the probe can measure. | 321 |
| `PROBe<m>:SETup:GAIN:MANual` | S/Q | suffix `<m>` 1..4<br>`<ManualGain>` range 0.0001 to 10000 | Sets the gain of the probe. | 321 |

#### Horizontal settings (16.4.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TIMebase:SCALe` | S/Q | `<TimeScale>` range 1e-9 to 50; lower limits are possible if zoom or FFT is enabled.; unit s/div; *RST **100e-6** | Sets the horizontal scale for all channel and math waveforms. | 321 |
| `TIMebase:POSition` | S/Q | `<Offset>` range Depends on time base setting; unit s; *RST **0** | Defines the trigger position, the time distance from the trigger point to the reference point (trigger offset). | 322 |
| `TIMebase:REFerence` | S/Q | `<ReferencePoint>` `8.33` · `50` · `91.67`; unit %; *RST **50** | Defines the time reference point in the diagram. | 322 |
| `TIMebase:ACQTime`<br>`TIMebase:RANGe` | S/Q | `<AcquisitionTime>` range 250e-12 to 500; unit s | Defines the time of one acquisition, that is the time across the 12 divisions of the dia- gram: Horizontal scale*12. | 323 |
| `TIMebase:DIVisions?` | Q | returns `<HorizDivCount>` Returns 12 divisions. | Queries the number of horizontal divisions on the screen. | 323 |
| `TIMebase:RATime?` | Q | returns `<HWAcqTime>` range Depends on various settings; unit s | Queries the real acquisition time used in the hardware. | 323 |

#### Acquisition settings (16.4.6)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `ACQuire:POINts:AUTomatic` | S/Q | `<AutoRecordLength>` `ON` · `OFF` | Enables or disables the automatic record length. | 324 |
| `ACQuire:POINts[:VALue]` | S/Q | `<RecordLength>` Record length in Samples. | Defines a record length value, the number of recorded waveform points in a segment. | 324 |
| `CHANnel<m>:TYPE` | S/Q | suffix `<m>` 1..4<br>`<DecimationMode>` `SAMPle` · `PDETect` · `HRESolution`; *RST **SAMPle** | Selects the method to reduce the data stream of the ADC to a stream of waveform points with lower sample rate. | 325 |
| `ACQuire:TYPE` | S/Q | `<AcquisitionType>` `REFresh` · `AVERage` · `ENVelope` | Sets the type of the aquisition mode. | 325 |
| `CHANnel<m>:ARIThmetics` | S/Q | suffix `<m>` 1..4<br>`<TrArith>` `OFF` · `ENVelope` · `AVERage`; *RST **OFF** | Selects the method to build the resulting waveform from several consecutive acquisi- tions of the signal. | 326 |
| `ACQuire:PEAKdetect` | S/Q | `<PeakDetect>` `AUTO` · `OFF`; *RST **OFF** | Enables or disables the peak detect acquisition mode. | 326 |
| `ACQuire:HRESolution` | S/Q | `<HighRes>` `AUTO` · `OFF`; *RST **OFF** | Enables or disables the high resolution acquisition mode. | 326 |
| `ACQuire:NSINgle:COUNt` | S/Q | `<NSingleCount>` range 1 to maximum number that depends on the record length.; *RST **1** | Sets the number of waveforms acquired with RUNSingle.<br>Bench: The history may hold fewer segments than requested (50 requested -> 40 available at 10 kSa); read ACQuire:AVAilable?.<br>Bench: The maximum depends on the record length: 40 and 100 were rejected (-222) where 16 was the limit. | 327 |
| `ACQuire:AVERage:COUNt` | S/Q | `<AverageCount>` range 2 to 100,000; *RST **2** | Defines the number of waveforms used to calculate the average waveform. | 327 |
| `ACQuire:AVERage:RESet` | E |  | Deletes the waveform and restarts the average calculation. | 327 |
| `ACQuire:AVERage:COMPlete?` | Q | returns `<AverageComplete>` `0` · `1` | Returns the state of averaging. | 327 |
| `TIMebase:ROLL:AUTomatic` | S/Q | `<AutomaticRoll>` `ON` · `OFF`; *RST **OFF** | Enables the automatic roll mode. | 327 |
| `TIMebase:ROLL:MTIMe` | S/Q | `<MinTimeBase>` unit s/div; *RST **500e-3** | The roll mode is enabled automatically if the time base exceeds the MinTimeBase, and if TIMebase:ROLL:AUTomaticon page 327 is set ON. | 328 |
| `ACQuire:INTerpolate` | S/Q | `<InterpolationType>` `SINX` · `LINear` · `SMHD`; *RST **SINX** | Defines the interpolation mode. | 328 |
| `ACQuire:POINts:ARATe?` | Q | returns `<ACDsampleRate>` unit Hz | Retrieves the sample rate of the ADC, that is the number of points that are sampled by the ADC in one second. | 328 |
| `ACQuire:SRATe?` | Q | `<SampleRate>` unit Sa/s | Returns the sample rate, that is the number of recorded waveform samples per sec- ond. | 329 |

### 6.3 Trigger

#### General trigger settings (16.5.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:MODE` | S/Q | `<TriggerMode>` `AUTO` · `NORMal`; *RST **AUTO** | Sets the trigger mode. | 330 |
| `TRIGger:A:SOURce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `EXTernanalog` · `LINE` · `SBUS1` · `SBUS2` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · … | Sets the trigger source for the selected A trigger type. | 330 |
| `TRIGger:A:TYPE` | S/Q | `<Type>` `EDGE` · `WIDTh` · `TV` · `RISetime` · `RUNT` · `TIM` · `BUS` · `LOGic` · `LINE` | Sets the trigger typer. | 331 |
| `TRIGger:A:HOLDoff:MODE` | S/Q | `<HoldOffMode>` `TIME` · `OFF`; *RST **Off** | Enables or disables the holdoff time. | 331 |
| `TRIGger:A:HOLDoff:TIME` | S/Q | `<HoldOffTime>` unit s | Defines the holdoff time. | 332 |

#### Edge trigger (16.5.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:EDGE:SLOPe` | S/Q | `<Slope>` `POSitive` · `NEGative` · `EITHer`; *RST **POSitive** | Sets the slope for the edge trigger. | 332 |
| `TRIGger:A:LEVel<n>[:VALue]` | S/Q | suffix `<n>` 1..5<br>`<Level>` range Depends on vertical scale.; unit V | Sets the trigger threshold voltage for edge, width, and timeout trigger. | 332 |
| `TRIGger:A:FINDlevel` | E |  | Sets the trigger level to 50% of the signal amplitude. | 333 |
| `TRIGger:A:EDGE:COUPling` | S/Q | `<Coupling>` `DC` · `AC` · `LFReject`; *RST **DC** | Sets the coupling for the trigger source. | 333 |
| `TRIGger:A:HYSTeresis` | S/Q | `<Hysteresis>` `AUTO` · `SMALl` · `MEDium` · `LARGE`; *RST **AUTO** | Sets a hysteresis range around the trigger level.<br>**Not implemented in FW 03.000.**<br>Bench: Returns -113 'Undefined header' on FW 03.000. | 333 |
| `TRIGger:A:EDGE:FILTer:HFReject` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Enables or disables an additional 5kHz lowpass filter in the trigger path. | 333 |
| `TRIGger:A:EDGE:FILTer:NREJect` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Turns an additional 100 MHz lowpass filter in the trigger path on or off. | 334 |

#### Width trigger (16.5.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:WIDTh:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **POSitive** | Sets the polarity of the pulse. | 334 |
| `TRIGger:A:WIDTh:RANGe` | S/Q | `<RangeMode>` `WITHin` · `OUTSide` · `SHORter` · `LONGer`; *RST **LONGer** | Defines how the measured pulse width is compared with the given limits. | 335 |
| `TRIGger:A:WIDTh:WIDTh` | S/Q | `<Time1>` range 20E-9 to 6.87194685440; *RST **20E-9** | For the ranges WITHin and OUTSide (defined using TRIGger:A:WIDTh:RANGe), the <Time1> defines the center of a range which is defined by the limits ±<Delta> (set with… | 335 |
| `TRIGger:A:WIDTh:DELTa` | S/Q | `<Delta>` range Minimum is 0. Maximum depends on the defined pulse width (TRIG:A:WIDTH:WITDH). | Defines a variation range around the width value specified using TRIGger:A:WIDTh: WIDTh. | 335 |

#### Video/TV trigger (16.5.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:TV:STANdard` | S/Q | `<Standard>` `PAL` · `NTSC` · `SECam` · `PALM` · `I576` · `P720` · `P1080` · `I1080`; *RST **PAL** | Selects the color television standard. | 336 |
| `TRIGger:A:TV:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **NEGative** | Selects the polarity of the signal. | 336 |
| `TRIGger:A:TV:FIELd` | S/Q | `<Field>` `EVEN` · `ODD` · `ALL` · `LINE` · `ALINe`; *RST **ALL** | Sets the trigger on the beginning of the video signal fields, or on the beginning of video signal lines. | 336 |
| `TRIGger:A:TV:LINE` | S/Q | `<Line>` range 1 to 525 (NTSC, PAL-M); 625 (PAL, SECAM, SDTV I-576); 750 (HDTV P720); 1125 (HDTV I1080, HDTV P1080); *RST **1** | Sets an exact line number if TRIGger:A:TV:FIELd is set to LINE. | 337 |

#### Pattern definition (16.5.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:PATTern:SOURce` | S/Q | `<SourceString>` String containing 0, 1, or X for each channel. | Sets the state for each channel. | 337 |
| `TRIGger:A:PATTern:FUNCtion` | S/Q | `<Function>` `AND` · `OR`; *RST **AND** | Sets the logical combination of the channel states . | 338 |
| `TRIGger:A:PATTern:CONDition` | S/Q | `<ConditionString>` `TRUE` · `FALSE`; *RST **""TRUE""** | Defines whether the instrument triggers on fulfillment of the logical condition, or on vio- lation. | 338 |
| `TRIGger:A:PATTern:MODE` | S/Q | `<PatternMode>` `OFF` · `TIMeout` · `WIDTh` | Disables the time limitation or sets the time comparison mode. | 339 |
| `TRIGger:A:PATTern:WIDTh:RANGe` | S/Q | `<PatternRange>` `WITHin` · `OUTSide` · `SHORter` · `LONGer` | Selects how the time limit for the pattern condition is defined. | 339 |
| `TRIGger:A:PATTern:WIDTh[:WIDTh]` | S/Q | `<PatternWidth>` unit s | For the ranges WITHin and OUTSide, the <PatternWidth> defines the center of a range which is defined by the limits ±<Delta>. | 339 |
| `TRIGger:A:PATTern:WIDTh:DELTa` | S/Q | `<PatternDelta>` unit s | Defines a range around the pattern width value specified using TRIGger:A: PATTern:WIDTh[:WIDTh]. | 340 |

#### Runt trigger (16.5.6)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:LEVel<n>:RUNT:LOWer` | S/Q | suffix `<n>` 1..5<br>`<Level>`  | Sets the lower voltage threshold for runt detection. | 340 |
| `TRIGger:A:LEVel<n>:RUNT:UPPer` | S/Q | suffix `<n>` 1..5<br>`<Level>`  | Sets the upper voltage threshold for runt detection. | 340 |
| `TRIGger:A:RUNT:DELTa` | S/Q | `<Variation>` range Depends on various settings, mainly time base and sample rate | Sets a range Δt to the reference pulse width set with TRIGger:A:RUNT:WIDTh, if TRIGger:A:RUNT:RANGe is set to WITHin or OUTSide. | 340 |
| `TRIGger:A:RUNT:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Indicates the polarity of a the runt to be triggered on. | 341 |
| `TRIGger:A:RUNT:RANGe` | S/Q | `<Range>` `LONGer` · `SHORter` · `WITHin` · `OUTSide`; *RST **LONG** | Sets how the measured pulse width is compared with the given limit(s). | 341 |
| `TRIGger:A:RUNT:WIDTh` | S/Q | `<Width>` range Depends on various settings, mainly time base and sample rate | Sets the reference runt pulse width, the nominal value for comparisons. | 341 |

#### Risetime trigger (16.5.7)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:LEVel<n>:RISetime:LOWer` | S/Q | suffix `<n>` 1..5<br>`<Level>`  | Sets the lower voltage threshold. | 342 |
| `TRIGger:A:LEVel<n>:RISetime:UPPer` | S/Q | suffix `<n>` 1..5<br>`<Level>`  | Sets the upper voltage threshold. | 342 |
| `TRIGger:A:RISetime:DELTa` | S/Q | `<Variation>`  | Sets a range Δt to the reference rise/fall time set with TRIGger:A:RISetime:TIME, if TRIGger:A:RISetime:RANGe is set to Within or Outside. | 342 |
| `TRIGger:A:RISetime:RANGe` | S/Q | `<Range>` `LONGer` · `SHORter` · `WITHin` · `OUTSide`; *RST **LONG** | Sets how the measured rise or fall time is compared with the given limit(s). | 342 |
| `TRIGger:A:RISetime:SLOPe` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Sets the edge of the slope to be triggered on. | 343 |
| `TRIGger:A:RISetime:TIME` | S/Q | `<RiseTime>` Depends on various settings, mainly time base and sample rate | Sets the reference rise or fall time, the nominal value for comparisons. | 343 |

#### Timeout trigger (16.5.8)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:A:TIMeout:RANGe` | S/Q | `<Range>` `HIGH` · `LOW`; *RST **HIGH** | Sets the relation of the signal level to the threshold. | 343 |
| `TRIGger:A:TIMeout:TIME` | S/Q | `<Time>` range 6.4e-9 to 13.7439; *RST **10e-6** | Sets the time limit for the timeout at which the instrument triggers. | 344 |

#### Actions on trigger (16.5.10)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:EVENt[:ENABle]` | S/Q | `<EventsEnabled>` `ON` · `OFF`; *RST **OFF** | Activates the selected actions on trigger event. | 344 |
| `TRIGger:EVENt:SOUNd` | S/Q | `<SoundEnable>` `ON` · `OFF`; *RST **OFF** | If ON, the instrument generates a beep on trigger event. | 345 |
| `TRIGger:EVENt:REFSave` | S/Q | `<SaveReference>` `ON` · `OFF`; *RST **OFF** | If ON, the instrument saves reference waveforms of all active channels, and activates the references. | 345 |
| `TRIGger:EVENt:SCRSave` | S/Q | `<SaveScreenshot>` `ON` · `OFF`; *RST **OFF** | If ON, the instrument saves a screenshot on each trigger event. | 345 |
| `TRIGger:EVENt:SCRSave:DESTination` | S/Q | `<File>` String parameter String with path and file name | Defines the target directory and file name for a screenshot that is saved on trigger event when TRIGger:EVENt:SCRSave is ON. | 346 |
| `TRIGger:EVENt:TRIGgerout` | S/Q | `<PulseOut>` `ON` · `OFF`; *RST **OFF** | If ON, the instrument generates a pulse on the AuxOut connector on trigger event. | 346 |
| `TRIGger:EVENt:WFMSave` | S/Q | `<SaveWaveform>` `ON` · `OFF`; *RST **OFF** | If ON, the instrument saves the data of a selected waveform on each trigger event. | 346 |
| `TRIGger:EVENt:WFMSave:DESTination` | S/Q | `<File>` String parameter String with path and file name | Defines the target directory and file name for a waveform that is saved on trigger event when TRIGger:EVENt:WFMSave is ON. | 347 |

### 6.4 Waveform analysis

#### Zoom (16.6.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TIMebase:ZOOM:STATe` | S/Q | `<ZoomState>` `ON` · `OFF`; *RST **OFF** | Switches the zoom on or off. | 347 |
| `TIMebase:ZOOM:SCALe` | S/Q | `<ZoomScale>` range Depends on various settings; unit s/div; *RST **50e-6** | Defines the horizontal scale for the zoom window in seconds per division, the timebase of the zoom window. | 347 |
| `TIMebase:ZOOM:TIME` | S/Q | `<Time>` unit s; *RST **0** | Defines the distance of the trigger point to the reference point in the zoom window. | 348 |
| `TIMebase:ZOOM:POSition` | S/Q | `<Position>` range Depends on the zoom time base, from nearly 0 to 100 % for large zoom.; unit %; *RST **50** | Defines the position of the zoom window in the upper window. | 348 |
| `DISPlay:CBAR:ZOOM[:POSition]` | S/Q | `<DividerPosition>` unit px | Defines the position of the divide bar between normal waveform and zoom window. | 348 |

#### Mathematics (16.6.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `CALCulate:MATH<m>:STATe` | S/Q | suffix `<m>` 1..5<br>`<State>` `ON` · `OFF`; *RST **OFF** | Activates the mathematics function and displays the defined math waveforms. | 349 |
| `CALCulate:MATH<m>[:EXPRession][:DEFine]` | S/Q | suffix `<m>` 1..5<br>`<RemComplExpr>` String parameter, consisting of the mathematical operation and the source(s), written in… | Defines the equation to be calculated for the selected math waveform as a regular expression. | 349 |
| `CALCulate:MATH<m>:LABel` | S/Q | suffix `<m>` 1..5<br>`<Label>` String Data String with label text | Defines a label for the specified math waveform (equation), which is shown at the waveform. | 350 |
| `CALCulate:MATH<m>:LABel:STATe` | S/Q | suffix `<m>` 1..5<br>`<LabelVisible>` `ON` · `OFF` | Activates the diplay of the label that is defined with CALCulate:MATH<m>:LABel. | 350 |
| `CALCulate:MATH<m>:POSition` | S/Q | suffix `<m>` 1..5<br>`<Position>` Position value, given in divisions. | Sets the vertical position of the math waveform. | 351 |
| `CALCulate:MATH<m>:SCALe` | S/Q | suffix `<m>` 1..5<br>`<Scale>` range -1.0E-24 to 5.0E+25; *RST **1** | Sets the vertical scale of the math waveform. | 351 |
| `CALCulate:MATH<m>:WCOLor` | S/Q | suffix `<m>` 1..5<br>`<WaveformColor>` `YELLow` · `GREen` · `ORANge` · `BLUE` · `LBLUE` · `WHITE` · `CYAN` · `PINK` · `RED` · `TEMPerature` · `RAINbow` · `FIRE` · `DEFault`; *RST **DEF** | Sets the color of the indicated math waveform. | 351 |
| `CALCulate:MATH<m>:TRACk:EDGE` | S/Q | suffix `<m>` 1..5<br>`<Polarity>` `ON` · `OFF` | Sets the rising or falling edge as a reference for each measurement. | 351 |
| `CALCulate:MATH<m>:TRACk:THReshold:HYSTeresis` | S/Q | suffix `<m>` 1..5<br>`<Hysteresis>` unit V | Sets the hysteresis for correct edge detection. | 352 |
| `CALCulate:MATH<m>:TRACk:THReshold[:UPPer]` | S/Q | suffix `<m>` 1..5<br>`<ThresholdLevel>` unit V | Sets the threshold for unipolar track sources. | 352 |

#### Reference waveforms (16.6.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `REFCurve<m>:SOURce` | S/Q | suffix `<m>` 1..4<br>`<Source>` `RE2` · `RE3` · `RE4` · `D70` · `D158` · `SPEC` · `MINH` · `MAXH` · `AVER`; *RST **CH1** | Defines the source of the reference waveform. | 353 |
| `REFCurve<m>:SOURce:CATalog?` | Q | suffix `<m>` 1..4<br>`<Source Catalogue>` `CH1` · `CH2` · `CH3` · `CH4` · `QMA` · `RE1` · `RE2` · `RE3` · `RE4` · `D70` · `D158` · `SPEC` · `MINH` · `MAXH` · … | Returns the source waveform. | 353 |
| `REFCurve<m>:STATe` | S/Q | suffix `<m>` 1..4<br>`<State>` `ON` · `OFF`; *RST **OFF** | Displays or hides the selected reference waveform. | 353 |
| `REFCurve<m>:UPDate` | E | suffix `<m>` 1..4 | Updates the selected reference by the waveform defined with REFCurve<m>:SOURce. | 354 |
| `REFCurve<m>:SAVE` | S | suffix `<m>` 1..4<br>`<Filename>` String with path and file name | Stores the reference waveform in the specified file. | 354 |
| `REFCurve<m>:LOAD` | S | suffix `<m>` 1..4<br>`<Filename>` String with path and file name | Loads the waveform data from the indicated reference file to the reference waveform. | 354 |
| `REFCurve<m>:LOAD:STATe` | E | suffix `<m>` 1..4 | Loads the instrument settings in addition to the reference waveform data. | 355 |
| `REFCurve<m>:HORizontal:POSition` | S/Q | suffix `<m>` 1..4<br>`<Position>` unit s | Changes the horizontal position of the reference waveform independently of the chan- nel waveform settings. | 355 |
| `REFCurve<m>:HORizontal:SCALe` | S/Q | suffix `<m>` 1..4<br>`<Scale>` unit s/div | Changes the horizontal scale (timebase) of the reference waveform independently of the channel waveform settings. | 355 |
| `REFCurve<m>:VERTical:POSition` | S/Q | suffix `<m>` 1..4<br>`<Position>` unit div | Changes the vertical position of the reference waveform. | 355 |
| `REFCurve<m>:VERTical:SCALe` | S/Q | suffix `<m>` 1..4<br>`<Scale>` unit V/div | Changes the vertical scale of the reference waveform. | 356 |
| `REFCurve<m>:WCOLor` | S/Q | suffix `<m>` 1..4<br>`<WaveformColor>` `YELLow` · `GREen` · `ORANge` · `BLUE` · `LBLUE` · `WHITE` · `CYAN` · `PINK` · `RED` · `TEMPerature` · `RAINbow` · `FIRE` · `DEFault`; *RST **DEF** | Selects a color for the reference waveform. | 356 |
| `REFCurve<m>:LABel` | S/Q | suffix `<m>` 1..4<br>`<WaveformLabel>` String parameter String parameter | Defines a label for the indicated reference waveform. | 356 |

#### General search settings (16.6.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `SEARch:STATe` | S/Q | `<SearchState>` `ON` · `OFF`; *RST **OFF** | Enables and disables the search mode. | 357 |
| `SEARch:CONDition` | S/Q | `<SearchCondition>` `EDGE` · `WIDTh` · `PEAK` · `RUNT` · `RTIMe` · `DATatoclock` · `PATTern` · `PROTocol`; *RST **EDGE** |  | 357 |
| `SEARch:SOURce` | S/Q | `<SearchSource>` `CH1` · `CH2` · `CH3` · `CH4` · `QMA` · `RE1` · `RE2` · `RE3` · `RE4`; *RST **CH1** | Selects the waveform to be analyzed. | 358 |
| `SEARch:TRIGger:EDGE:SLOPe` | S/Q | `<Slope>` `POSitive` · `NEGative` · `EITHer`; *RST **POS** | Sets the slope to be searched for. | 359 |
| `SEARch:TRIGger:EDGE:LEVel` | S/Q | `<Level>` *RST **0.5 V** | Sets the voltage level for the edge search. | 359 |
| `SEARch:TRIGger:EDGE:LEVel:DELTa` | S/Q | `<DeltaLevel>` range Lower limit depends on vertical scale and other set- tings, no upper limit; *RST **0.2 V** | Sets a hysteresis range above and below the search level to avoid unwanted search results caused by noise oscillation around the level. | 359 |
| `SEARch:TRIGger:WIDTh:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Indicates the polarity of the pulse to be searched for. | 360 |
| `SEARch:TRIGger:WIDTh:LEVel` | S/Q | `<Level>` *RST **500 mV** | Sets the voltage level on which the pulse width is measured. | 360 |
| `SEARch:TRIGger:WIDTh:LEVel:DELTa` | S/Q | `<DeltaLevel>` range Lower limit depends on vertical scale and other set- tings, no upper limit; *RST **200 mV** | Sets a hysteresis range above and below the search level to avoid unwanted search results caused by noise oscillation around the level. | 360 |
| `SEARch:TRIGger:WIDTh:RANGe` | S/Q | `<Range>` `WITHin` · `OUTSide` · `SHORter` · `LONGer`; *RST **WITH** | Sets how the measured pulse width is compared with the given limit(s). | 360 |
| `SEARch:TRIGger:WIDTh:WIDTh` | S/Q | `<Width>` unit s | Sets the reference pulse width, the nominal value for comparisons. | 361 |
| `SEARch:TRIGger:WIDTh:DELTa` | S/Q | `<DeltaWidth>` range Lower limit depends on the resolution, practically no upper limit | Sets a range Δt to the reference pulse width set with SEARch:TRIGger:WIDTh: WIDTh, if SEARch:TRIGger:WIDTh:RANGe is set to WITHin or OUTSide. | 361 |
| `SEARch:MEASure:PEAK:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative` · `EITHer`; *RST **POS** | Indicates the polarity of a the pulse to be searched for a peak. | 361 |
| `SEARch:MEASure:LEVel:PEAK:MAGNitude` | S/Q | `<Magnitude>` unit V | Sets the peak-to-peak limit. | 361 |
| `SEARch:TRIGger:RISetime:SLOPe` | S/Q | `<Polarity>` `POSitive` · `NEGative` · `EITHer`; *RST **POS** | Sets the slope to be found. | 362 |
| `SEARch:TRIGger:LEVel:RISetime:LOWer` | S/Q | `<LowerLevel>` unit V; *RST **400 mV** | Sets the lower voltage threshold. | 362 |
| `SEARch:TRIGger:LEVel:RISetime:UPPer` | S/Q | `<UpperLevel>` unit V; *RST **600 mV** | Sets the upper voltage threshold. | 362 |
| `SEARch:TRIGger:RISetime:RANGe` | S/Q | `<Range>` `LONGer` · `SHORter` · `WITHin` · `OUTSide`; *RST **LONG** | Sets how the measured rise or fall time is compared with the given limit(s). | 363 |
| `SEARch:TRIGger:RISetime:TIME` | S/Q | `<Time>` range Depends on various settings, mainly time base and sample rate; unit s; *RST **200e-6** | Sets the reference rise or fall time, the nominal value for comparisons. | 363 |
| `SEARch:TRIGger:RISetime:DELTa` | S/Q | `<DeltaTime>` range Depends on various settings, mainly time base and sample rate; unit s; *RST **50e-6** | Sets a range Δt to the reference rise/fall time set with SEARch:TRIGger:RISetime: TIME, if SEARch:TRIGger:RISetime:RANGe is set to Within or Outside. | 363 |
| `SEARch:TRIGger:RUNT:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative` · `EITHer`; *RST **POS** | Indicates the polarity of a the runt to be searched for. | 364 |
| `SEARch:TRIGger:LEVel:RUNT:LOWer` | S/Q | `<LowerLevel>` range Depends on vertical scale and LSB value.; unit V; *RST **400 mV** | Sets the lower voltage threshold for runt detection. | 364 |
| `SEARch:TRIGger:LEVel:RUNT:UPPer` | S/Q | `<UpperLevel>` range Depends on vertical scale and LSB value.; unit V; *RST **600 mV** | Sets the upper voltage threshold for runt detection. | 364 |
| `SEARch:TRIGger:RUNT:RANGe` | S/Q | `<Range>` `LONGer` · `SHORter` · `WITHin` · `OUTSide`; *RST **LONG** | Sets how the measured pulse width is compared with the given limit(s). | 364 |
| `SEARch:TRIGger:RUNT:WIDTh` | S/Q | `<Width>` range Depends on various settings, mainly time base and sample rate.; unit s; *RST **200e-6** | Sets the reference runt pulse width, the nominal value for comparisons. | 365 |
| `SEARch:TRIGger:RUNT:DELTa` | S/Q | `<DeltaWidth>` range Depends on various settings, mainly time base and sample rate.; unit s; *RST **50e-6** | Sets a range Δt to the reference pulse width set with SEARch:TRIGger:RUNT:WIDTh , if SEARch:TRIGger:RUNT:RANGe is set to WITHin or OUTSide. | 365 |
| `SEARch:TRIGger:DATatoclock:CSOurce` | S/Q | `<ClockSource>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH1** | Selects the input channel of the clock signal. | 366 |
| `SEARch:TRIGger:DATatoclock:CLEVel` | S/Q | `<ClockLevel>` range Depends on vertical scale | Sets the voltage level for the clock signal. | 366 |
| `SEARch:TRIGger:DATatoclock:DLEVel` | S/Q | `<DataLevel>` range Depends on vertical scale | Sets the voltage level for the data signal. | 366 |
| `SEARch:TRIGger:DATatoclock:CLEVel:DELTa`<br>`SEARch:TRIGger:DATatoclock:DLEVel:DELTa` | S/Q | `<LevelDelta>` range Lower limit depends on vertical scale and other set- tings, no upper limit | Set a hysteresis range to the clock and data levels in order to avoid unwanted search results caused by noise oscillation around the level. | 366 |
| `SEARch:TRIGger:DATatoclock:CEDGe` | S/Q | `<ClockEdge>` `POSitive` · `NEGative` · `EITHer`; *RST **POS** | Sets the edge of the clock signal to define the start point for the setup and hold time. | 366 |
| `SEARch:TRIGger:DATatoclock:HTIMe` | S/Q | `<HoldTime>` range Depends on time base and sample interval | Sets the minimum time after the clock edge while the data signal must stay steady above or below the data level. | 367 |
| `SEARch:TRIGger:DATatoclock:STIMe` | S/Q | `<SetupTime>` range Depends on time base and sample interval | Sets the minimum time before the clock edge while the data signal must stay steady above or below the data level. | 367 |
| `SEARch:TRIGger:PATTern:SOURce` | S/Q | `<Pattern>` String parameter String containing 0, 1, X\|x for each channel. | Specifies the search pattern - the state for each channel. | 367 |
| `SEARch:TRIGger:PATTern:FUNCtion` | S/Q | `<Function>` `AND` · `OR` · `NAND` · `NOR`; *RST **AND** | Sets the logical combination of the channel states. | 368 |
| `SEARch:TRIGger:PATTern:LEVel<n>` | S/Q | suffix `<n>` 1..4<br>`<ThresholdLevel>` range Depends on vertical scale | Sets the threshold value for the specified source channel. | 368 |
| `SEARch:TRIGger:PATTern:LEVel<n>:DELTa` | S/Q | suffix `<n>` 1..4<br>`<LevelDelta>` range Lower limit depends on vertical scale and other set- tings, no upper limit | Sets a hysteresis range to the threshold of the specified source channel to avoid unwanted search results caused by noise oscillation around the level. | 369 |
| `SEARch:TRIGger:PATTern:WIDTh:RANGe` | S/Q | `<Range>` `WITHin` · `OUTSide` · `LONGer` · `SHORter`; *RST **LONG** | Sets the condition how the duration of a steady pattern is compared with the given ref- erence time. | 369 |
| `SEARch:TRIGger:PATTern:WIDTh[:WIDTh]` | S/Q | `<Width>` unit s | Sets the reference time of a steady pattern, the nominal value for comparisons. | 369 |
| `SEARch:TRIGger:PATTern:WIDTh:DELTa` | S/Q | `<DeltaTime>` unit s | Sets a range Δt to the reference pattern duration set with SEARch:TRIGger: PATTern:WIDTh[:WIDTh], if SEARch:TRIGger:PATTern:WIDTh:RANGe is set to WITHin or OUTSide. | 370 |
| `SEARch:RESult:BCOunt?` | Q | returns `<BufferedCount>` Maximum number of search results | Returns the maximum number of search results, which the instrument can store. | 370 |
| `SEARch:RESDiagram:SHOW` | S/Q | `<ResultShow>` `ON` · `OFF`; *RST **OFF** | Shows or hides the table of search results. | 370 |
| `SEARch:RESult:ALL?` | Q | returns `<AllResults>` List of results items seperated by comma For each result, six values are returned: 1. | Returns all result values of the search. | 370 |
| `SEARch:RESult<n>?` | Q | suffix `<n>` *<br>returns `<Result>` Comma-separated value list Meaning of the values: Result number, time value, y-position… | Returns the result values of the specified search result. | 371 |
| `SEARch:RCOunt?` | Q | returns `<ResultCount>` *RST **0** | Returns the number of search results. | 371 |
| `EXPort:SEARch:NAME` | S/Q | `<FileName>` String parameter | Defines the path and filename for search results that will be saved with EXPort: SEARch:SAVE. | 372 |
| `EXPort:SEARch:SAVE` | S |  | Saves the search results to the path and file defined by EXPort:SEARch:NAME. | 372 |

#### History settings (16.6.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `ACQuire:MEMory[:MODE]` | S/Q | `<MemoryMode>` `AUTomatic` · `DMEMory` · `MANual`; *RST **AUT** | Defines how the record length is set. | 372 |
| `ACQuire:POINts:AUTomatic` | S/Q | `<AutoRecordLength>` `ON` · `OFF` | Enables or disables the automatic record length. | 373 |
| `ACQuire:POINts[:VALue]` | S/Q | `<RecordLength>` Record length in Samples. | Defines a record length value, the number of recorded waveform points in a segment. | 373 |
| `ACQuire:COUNt` | S/Q | `<NoOfAcquisitions>` Number of available segments in the memory | Sets the number of segments to be captured. | 374 |
| `ACQuire:NSINgle:COUNt` | S/Q | `<NSingleCount>` range 1 to maximum number that depends on the record length.; *RST **1** | Sets the number of waveforms acquired with RUNSingle.<br>Bench: The history may hold fewer segments than requested (50 requested -> 40 available at 10 kSa); read ACQuire:AVAilable?.<br>Bench: The maximum depends on the record length: 40 and 100 were rejected (-222) where 16 was the limit. | 374 |
| `ACQuire:AVAilable?` | Q | `<Acquisitions>` Number of captured segments | Returns the number of segments that are currently saved in the memory. | 374 |
| `ACQuire:SEGMented:STATe` | S/Q | `<State>` `ON` · `OFF` | If ON, the acquisitions are performed as fast as possible, without processing and dis- playing the waveforms. | 374 |
| `ACQuire:AVERage:CURRent?` | Q | `<CurrentAverages>` range Minimum is 2; maximum depends on the instrument settings. | Returns the current amount of acquired waveforms that contribute to the average. | 375 |
| `CALCulate:MATH<m>:HISTory:CONTrol[:ENABle]`<br>`BUS<b>:HISTory:CONTrol[:ENABle]`<br>`DIGital<m>:HISTory:CONTrol[:ENABle]`<br>`LOGic<p>:HISTory:CONTrol[:ENABle]`<br>`CHANnel<m>:HISTory:CONTrol:[ENABle]` | S/Q | `<PlayerControl>` `ON` · `OFF`; *RST **OFF** | Displays or hides the history player on the screen. | 376 |
| `CALCulate:MATH<m>:HISTory:CURRent`<br>`BUS<b>:HISTory:CURRent`<br>`DIGital<m>:HISTory:CURRent`<br>`LOGic<p>:HISTory:CURRent`<br>`SPECtrum:HISTory:CURRent`<br>`CHANnel<m>:HISTory:CURRent` | S/Q | `<CurrentAcquisition>` Segment index. | Accesses a particular history segment in the memory to display it.<br>Bench: Out-of-range segment indexes are clamped silently (no error). Valid range: HISTory:STARt? .. HISTory:STOP? (0 = newest).<br>Bench: Selecting a segment re-runs the bus decode for that segment (about 190-240 ms per segment). Segmented memory uses a short record: keep the sample rate at least ~10x the bus bit rate or the per-segment decode is garbled. | 376 |
| `CALCulate:MATH<m>:HISTory:PALL`<br>`BUS<b>:HISTory:PALL`<br>`DIGital<m>:HISTory:PALL`<br>`LOGic<p>:HISTory:PALL`<br>`SPECtrum:HISTory:PALL`<br>`CHANnel<m>:HISTory:PALL` | S/Q | `<PlayAll>` `ON` · `OFF`; *RST **ON** | Enables the replay of all acquired segments. | 377 |
| `CALCulate:MATH<m>:HISTory:STARt`<br>`BUS<b>:HISTory:STARt`<br>`DIGital<m>:HISTory:STARt`<br>`LOGic<p>:HISTory:STARt`<br>`SPECtrum:HISTory:STARt`<br>`CHANnel<m>:HISTory:STARt` | S/Q | `<StartAcquisition>` Start index. | Sets the index of the oldest segment to be displayed. | 377 |
| `CALCulate:MATH<m>:HISTory:STOP`<br>`BUS<b>:HISTory:STOP`<br>`DIGital<m>:HISTory:STOP`<br>`LOGic<p>:HISTory:STOP`<br>`SPECtrum:HISTory:STOP`<br>`CHANnel<m>:HISTory:STOP` | S/Q | `<StopAcquisition>` Stop index. | Sets the index of the latest segment to be displayed. | 377 |
| `CALCulate:MATH<m>:HISTory:PLAYer:SPEed`<br>`BUS<b>:HISTory:PLAYer:SPEed`<br>`DIGital<m>:HISTory:PLAYer:SPEed`<br>`LOGic<p>:HISTory:PLAYer:SPEed`<br>`SPECtrum:HISTory:PLAYer:SPEed`<br>`CHANnel<m>:HISTory:PLAYer:SPEed` | S/Q | `<PlayerSpeed>` `SLOW` · `MEDium` · `FAST` · `AUTO`; *RST **AUTO** | Sets the speed of the history replay. | 377 |
| `CALCulate:MATH<m>:HISTory:REPLay`<br>`BUS<b>:HISTory:REPLay`<br>`DIGital<m>:HISTory:REPLay`<br>`LOGic<p>:HISTory:REPLay`<br>`SPECtrum:HISTory:REPLay`<br>`CHANnel<m>:HISTory:REPLay` | S/Q | `<Replay>` `ON` · `OFF`; *RST **STOP** | If set to ON, the replay of the selected history segments repeats automatically. | 378 |
| `CALCulate:MATH<m>:HISTory:PLAYer:STATe`<br>`BUS<b>:HISTory:PLAYer:STATe`<br>`DIGital<m>:HISTory:PLAYer:STATe`<br>`LOGic<p>:HISTory:PLAYer:STATe`<br>`SPECtrum:HISTory:PLAYer:STATe`<br>`CHANnel<m>:HISTory:PLAYer:STATe` | S/Q | `<PlayerState>` `RUN` · `STOP`; *RST **STOP** | Starts and stops the replay of the history segments. | 378 |
| `CALCulate:MATH<m>:HISTory:TTABle[:ENABle]`<br>`BUS<b>:HISTory:TTABle[:ENABle]`<br>`DIGital<m>:HISTory:TTABle[:ENABle]`<br>`LOGic<p>:HISTory:TTABle[:ENABle]`<br>`CHANnel<m>:HISTory:TTABle[:ENABle]` | S/Q | `<TimeTableEnable>` `ON` · `OFF`; *RST **OFF** | Displays or hides the segment table on the screen. | 379 |
| `CALCulate:MATH<m>:HISTory:TSRelative?`<br>`BUS<b>:HISTory:TSRelative?`<br>`DIGital<m>:HISTory:TSRelative?`<br>`LOGic<p>:HISTory:TSRelative?`<br>`SPECtrum:HISTory:TSRelative?`<br>`CHANnel<m>:HISTory:TSRelative?` | Q | returns `<TimeToNewestAcq>` Time to newest acquisition | Returns the time difference of the selected segment to the newest segment. | 380 |
| `CALCulate:MATH<m>:HISTory:TSRelative:ALL?`<br>`BUS<b>:HISTory:TSRelative:ALL?`<br>`DIGital<m>:HISTory:TSRelative:ALL?`<br>`LOGic<p>:HISTory:TSRelative:ALL?`<br>`SPECtrum:HISTory:TSRelative:ALL?`<br>`CHANnel<m>:HISTory:TSRelative:ALL?` | Q | returns `<TimeToNewestAcq>` List of Values The list starts with the oldest segment, and the newest segment is the… | Returns the time differences to the newest acquisition of all history segments. | 380 |
| `CALCulate:MATH<m>:HISTory:TSABsolute?`<br>`BUS<b>:HISTory:TSABsolute?`<br>`DIGital<m>:HISTory:TSABsolute?`<br>`LOGic<p>:HISTory:TSABsolute?`<br>`SPECtrum:HISTory:TSABsolute?`<br>`CHANnel<m>:HISTory:TSABsolute?` | Q | returns `<Hour>, <Minute>,` Comma-separated list<br>returns `<Seconds>`  | Returns the absolute daytime of the selected acquisition (CHANnel<m>:HISTory: CURRent). | 380 |
| `CALCulate:MATH<m>:HISTory:TSABsolute:ALL?`<br>`BUS<b>:HISTory:TSABsolute:ALL?`<br>`DIGital<m>:HISTory:TSABsolute:ALL?`<br>`LOGic<p>:HISTory:TSABsolute:ALL?`<br>`SPECtrum:HISTory:TSABsolute:ALL?`<br>`CHANnel<m>:HISTory:TSABsolute:ALL?` | Q | returns `<Hour>, <Minute>,` Comma-separated list of hour, minute, and second values.<br>returns `<Second>`  | Returns the absolute daytimes of all history segments.<br>Bench: Returns hour,minute,second triples per segment; dates come from ...:TSDate:ALL? as year,month,day triples. | 381 |
| `CALCulate:MATH<m>:HISTory:TSDate?`<br>`BUS<b>:HISTory:TSDate?`<br>`DIGital<m>:HISTory:TSDate?`<br>`LOGic<p>:HISTory:TSDate?`<br>`SPECtrum:HISTory:TSDate?`<br>`CHANnel<m>:HISTory:TSDate?` | Q | returns `<Year>, <Month>,` Comma-separated list<br>returns `<Day>`  | Returns the date of the selected acquisition (CHANnel<m>:HISTory:CURRent). | 381 |
| `CALCulate:MATH<m>:HISTory:TSDate:ALL?`<br>`BUS<b>:HISTory:TSDate:ALL?`<br>`DIGital<m>:HISTory:TSDate:ALL?`<br>`LOGic<p>:HISTory:TSDate:ALL?`<br>`SPECtrum:HISTory:TSDate:ALL?`<br>`CHANnel<m>:HISTory:TSDate:ALL?` | Q | returns `<Year>, <Month>,` Comma-separated list of year, month, and day values.<br>returns `<Day>` The list starts with the oldest segment, and the newest segment is the last one. | Returns the dates of all history segments. | 382 |
| `BPLot:EXPort:NAME`<br>`BUS<b>:HISTory:EXPort:NAME`<br>`DIGital<m>:HISTory:EXPort:NAME`<br>`LOGic<p>:HISTory:EXPort:NAME`<br>`SPECtrum:HISTory:EXPort:NAME`<br>`CHANnel<m>:HISTory:EXPort:NAME`<br>`EXPort:ATABle:NAME` | S/Q | `<ExportPath>` String parameter | <br>Bench: BUS<b>:HISTory:EXPort writes only the segment timestamp table (CSV, same content as EXPort:ATABle), not decoded frames. Harvest decodes with the FRAMe queries per segment instead. Download the CSV with MMEMory:DATA?. | 382 |
| `BPLot:EXPort:SAVE`<br>`BUS<b>:HISTory:EXPort:SAVE`<br>`DIGital<m>:HISTory:EXPort:SAVE`<br>`LOGic<p>:HISTory:EXPort:SAVE`<br>`SPECtrum:HISTory:EXPort:SAVE`<br>`CHANnel<m>:HISTory:EXPort:SAVE`<br>`EXPort:ATABle:SAVE` | E |  | Saves the acquisition timestamps table to the file that is defined by the EXPort: ATABle:NAME command.<br>Bench: BUS<b>:HISTory:EXPort writes only the segment timestamp table (CSV, same content as EXPort:ATABle), not decoded frames. Harvest decodes with the FRAMe queries per segment instead. Download the CSV with MMEMory:DATA?. | 383 |

### 6.5 Measurements

#### Quick measurements (16.7.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `MEASurement<m>:AON` | E |  | Starts the quick measurement. | 384 |
| `MEASurement<m>:AOFF` | E |  | Stops the quick measurement. | 384 |
| `MEASurement<m>:ARESult?` | Q | returns `<Data>` List of values Quick measurement results are listed in the following order: PEAK (Vpp),… | Returns the results of the quick measurement. | 384 |
| `MEASurement<m>:ALL[:STATe]` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Starts or stops the quick measurement and sets the status bit. | 384 |

#### Measurement settings (16.7.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `MEASurement<m>[:ENABle]` | S/Q | suffix `<m>` 1..8<br>`<State>` `ON` · `OFF`; *RST **OFF** | Activates or deactivates the selected measurement. | 385 |
| `MEASurement<m>:MAIN` | S/Q | suffix `<m>` 1..8<br>`<MeasType>` `FREQuency` · `PERiod` · `PEAK` · `UPEakvalue` · `LPEakvalue` · `PPCount` · `NPCount` · `RECount` · `FECount` · `HIGH` · `LOW` · `AMPLitude` · `MEAN` · `RMS` · …; *RST **PEAK** | Defines the measurement type to be performed on the selected source. | 385 |
| `MEASurement<m>:SOURce` | S/Q | suffix `<m>` 1..8<br>`<SignalSource>` `CH1` · `CH2` · `CH3` · `CH4` · `MA1` · `RE1` · `RE2` · `RE3` · `RE4` · `D0` · `D1` · `D2` · `D3` · `D4` · …<br>`<SignalSource2>` `None` · `CH1` · `CH2` · `CH3` · `CH4` · `MA1` · `RE1` · `RE2` · `RE3` · `RE4` · `D0` · `D1` · `D2` · `D3` · … | Selects one of the active signal, reference or math channels as the source(s) of the selected measurement. | 387 |
| `MEASurement<m>:DELay:SLOPe` | S/Q | suffix `<m>` 1..8<br>`<SignalSlope>` `POSitive` · `NEGative`; *RST **POS**<br>`<ReferenceSlope>` `POSitive` · `NEGative`; *RST **POS** | Sets the edges to be used for delay measurement and delay to trigger measurement. | 388 |
| `MEASurement<m>:STATistics[:ENABle]` | S/Q | `<StatisticEnable>` `ON` · `OFF`; *RST **OFF** | Activates or deactivates the statistical evaluation for all active measurements. | 388 |
| `MEASurement<m>:STATistics:RESet` | E | suffix `<m>` 1..8 | Deletes the statistical results of the indicated measurement. | 388 |
| `MEASurement<m>:TIMeout[TIME]` | S/Q | `<TimeoutTime>` unit s; *RST **0** | Sets a waiting time, how long the instrument waits until it returns the measurement results on MEASurement:RESult?. | 389 |
| `MEASurement<m>:TIMeout:AUTO` | S/Q | `<AutomaticMode>` `ON` · `OFF`; *RST **OFF** | The waiting time is set automatically, based on the horizontal scale. | 389 |
| `MEASurement<m>:RESult[:ACTual]?` | Q | suffix `<m>` 1..8<br>query: `<MeasType>` `FREQuency` · `PERiod` · `PEAK` · `UPEakvalue` · `LPEakvalue` · `PPCount` · `NPCount` · `RECount` · `FECount` · `HIGH` · `LOW` · `AMPLitude` · `MEAN` · `RMS` · …<br>returns `<Value>` Measurement result. | Returns the result of the specified measurement type.<br>Bench: 9.91E+37 means 'no value' (NaN). | 390 |
| `MEASurement<m>:RESult:AVG?` | Q | suffix `<m>` 1..8<br>query: `<AverageValue>` Statistic value | Returns the average value of the current measurement series.<br>Bench: 9.91E+37 means 'no value' (NaN). | 390 |
| `MEASurement<m>:RESult:STDDev?` | Q | suffix `<m>` 1..8<br>query: `<StandardDeviation>` Statistic value | Returns the statistical standard deviation of the current measurement series.<br>Bench: 9.91E+37 means 'no value' (NaN). | 390 |
| `MEASurement<m>:RESult:NPEak?` | Q | suffix `<m>` 1..8<br>query: `<NegativePeak>` Minimum measurement result | Returns the minimum measurement result of the current measurement series.<br>Bench: 9.91E+37 means 'no value' (NaN). | 391 |
| `MEASurement<m>:RESult:PPEak?` | Q | suffix `<m>` 1..8<br>query: `<PositivePeak>` Maximum measurement result | Returns the maximum measurement result of the current measurement series.<br>Bench: 9.91E+37 means 'no value' (NaN). | 391 |
| `MEASurement<m>:RESult:WFMCount?` | Q | suffix `<m>` 1..8<br>query: `<WaveformCount>` Number of measured waveforms | Returns the current number of measured waveforms.<br>Bench: 9.91E+37 means 'no value' (NaN). | 391 |
| `MEASurement<m>:STATistics:WEIGht?` | Q | suffix `<m>` 1..8<br>returns `<BufferSize>` The buffer size is always 1000 result values. | Returns the size of the statistics buffer. | 391 |
| `MEASurement<m>:STATistics:VALue:ALL?` | Q | suffix `<m>` 1..8<br>returns `<ValueList>` Comma separated List of Values Comma-separated list of statistical values | Returns all values from the statistics buffer.<br>Bench: Returns the full statistics buffer (~1000 values). 9.91E+37 means 'no value' (NaN). | 392 |
| `MEASurement<m>:STATistics:VALue<n>?` | Q | suffix `<m>` 1..8, `<n>` *<br>returns `<StatisticValue>` Statistical value | Returns the nth statistical value from the indicated buffer place. | 392 |
| `EXPort:MEASurement<m>:STATistics:NAME` | S/Q | suffix `<m>` 1..8<br>`<FileName>` String parameter | Defines the path and filename of the statistics file. | 392 |
| `EXPort:MEASurement<m>:STATistics:SAVE` | E | suffix `<m>` 1..8 | Saves statistical results of the indicated measurement place to the file that is defined by the EXPort:MEASurement<m>:STATistics:NAME command. | 393 |
| `EXPort:MEASurement:STATistics:ALL:NAME` | S/Q | `<FileName>` String parameter | Defines the path and filename of the statistics file. | 393 |
| `EXPort:MEASurement:STATistics:ALL:SAVE` | E |  | Saves statistical results of all measurement places to the file that is defined by the EXPort:MEASurement:STATistics:ALL:NAME command. | 393 |
| `MEASurement<m>:GATE` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Activates or deactivates the measurement gate. | 395 |
| `MEASurement<m>:GATE:MODE` | S/Q | `<GateMode>` `RELative` · `ABSolute`; *RST **REL** | Defines whether the gate is defined in absolute or relative values. | 395 |
| `MEASurement<m>:GATE:ABSolute:STARt` | S/Q | `<StartTime>` unit s; *RST **-300e-6** | Defines the absolute start time for the measurement gate. | 395 |
| `MEASurement<m>:GATE:ABSolute:STOP` | S/Q | `<StopTime>` unit s; *RST **300e-6** | Defines the absolute end time for the measurement gate. | 396 |
| `MEASurement<m>:GATE:RELative:STARt` | S/Q | `<StartPosition>` unit %; *RST **25** | Defines the relative start value for the measurement gate. | 396 |
| `MEASurement<m>:GATE:RELative:STOP` | S/Q | `<StopPosition>` unit %; *RST **75** | Defines the relative end value for the measurement gate. | 396 |
| `REFLevel:RELative:MODE` | S/Q | `<RelativeMode>` `TEN` · `TWENty` · `FIVE` · `USER`; *RST **TEN** | Sets the lower and upper reference levels for rise and fall time mesurements (cursor and automatic mesurements) as well as the middle reference level for phase and… | 397 |
| `REFLevel:RELative:LOWer`<br>`REFLevel:RELative:UPPer` | S/Q | `<LowerLevel>` unit %; *RST **10**<br>`<UpperLevel>` unit %; *RST **90** | Set the lower and upper reference levels for rise and fall time mesurements (cursor and automatic mesurements) if REFLevel:RELative:MODE is set to USER. | 397 |
| `REFLevel:RELative:MIDDle` | S/Q | `<MiddleLevel>` unit %; *RST **50** | Set the middle reference level that is used for phase and delay measurements, if REFLevel:RELative:MODE is set to USER. | 397 |

#### Cursor settings (16.7.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `CURSor<m>:AOFF` | E |  | Switches the cursor off. | 398 |
| `CURSor<m>:STATe` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Activates or deactivates the cursor measurement. | 398 |
| `CURSor<m>:FUNCtion` | S/Q | `<Type>` `HORizontal` · `VERTical` · `HVERtical`; *RST **VERTical** | Defines the cursor measurement type. | 398 |
| `CURSor<m>:SOURce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `MA1` · `MA2` · `MA3` · `MA4` · `MA5` · `RE1` · `RE2` · `RE3` · `RE4` · `XY1` · … | Defines the source of the cursor measurement. | 399 |
| `CURSor<m>:USSource` | S/Q | `<UseSecondSource>` `ON` · `OFF`; *RST **OFF** | Enables the second cursor source. | 400 |
| `CURSor<m>:SSOurce` | S/Q | `<SecondSource>` `CH1` · `CH2` · `CH3` · `CH4` · `MA1` · `MA2` · `MA3` · `MA4` · `MA5` · `RE1` · `RE2` · `RE3` · `RE4` · `XY1` · … | Selects the second cursor source. | 400 |
| `CURSor<m>:TRACking[:STATe]` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | If set to ON, the V-Marker cursor measurement is enabled. | 400 |
| `CURSor<m>:X1Position`<br>`CURSor<m>:X2Position` | S/Q | `<Xposition1>` range Depends on the current instrument settings, for<br>`<Xposition2>` unit s | The commands specify or return the positions of vertical cursor lines on the x-axis (time, frequency for FFT). | 401 |
| `CURSor<m>:Y1Position`<br>`CURSor<m>:Y2Position` | S/Q | `<Yposition2>` range Depends on the current instrument settings.; unit V | The commands specify or return the positions of horizontal cursor lines on the y-axis (voltage, current, level for FFT). | 401 |
| `CURSor<m>:YCOupling`<br>`CURSor<m>:XCOupling` | S/Q | `<Coupling>` `ON` · `OFF`; *RST **OFF** | If enabled, the cursor lines are coupled so that the distance between the two lines remains the same if one cursor is moved. | 401 |
| `CURSor<m>:SWAVe` | E |  | Autoset for cursor lines, sets the cursor lines to typical points of the waveform depend- ing on the selected cursor type. | 402 |
| `CURSor<m>:TRACking:SCALe[:STATe]` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Enables the adjustment of cursor lines if the vertical or horizontal scales are changed. | 402 |
| `CURSor<m>:SPPeak<n>`<br>`CURSor<m>:SNPeak<n>` | E | suffix `<n>` 1..2 | Set the cursor line to the previous / next peak, respectively. | 402 |
| `CURSor<m>:XDELta[:VALue]?` | Q | returns `<Delta>` range -100E24 to 100E24; unit s | Returns the time difference between two vertical cursor lines (Δt). | 403 |
| `CURSor<m>:XDELta:INVerse?` | Q | returns `<DeltaInverse>` range -100E24 to 100E24; unit 1/s | Returns the inverse time difference between the two cursors (1/Δt, frequency). | 403 |
| `CURSor<m>:YDELta[:VALue]?` | Q | returns `<YDelta>` Delta value | Queries the delta of the values in y-direction at the two cursor lines. | 403 |
| `CURSor<m>:YDELta:SLOPe?` | Q | returns `<Slope>` Inverse value | Returns the inverse value of the vertical difference (e.g. | 404 |

### 6.6 Applications

#### General (16.8.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `DEVice:MODE` | S/Q | `<OperationalMode>` `YT` · `ZOOM` · `XY` · `QMEas` · `UPDate` · `AUToset` · `MASKtest` · `FFT` · `BPL` | Sets the operation mode or application.<br>Bench: Reads back BPL while the Bode plot application is active (FW 03.000); BPL is not in the manual's value list.<br>Bench: Applications are exclusive: in BPL mode, bus decode and other settings fail with -221 'Settings conflict'. Switch with DEV:MODE YT.<br>Bench: Leaving the Bode application turns the generator output off.<br>Bench: The Bode application switches ACQuire:MEMory to LIM (75 kSa) and leaves it there; afterwards ACQ:MEM AUT is refused with -221 and only restoring a SYSTem:SET snapshot from before the sweep brings AUTO back. rs-scope-mcp saves one before every bode() and restores it when the mode leaves BPL. | 404 |

#### Mask setup (16.8.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `MASK:STATe` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Turns the mask test application on or off. | 404 |
| `MASK:SOURce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH1** | Defines the channel to be compared with the mask, and also the channel from which the mask is created. | 405 |
| `MASK:CHCopy` | E |  | Creates a mask from the envelope waveform of the source set with MASK:SOURce.<br>Bench: Switches ACQuire:TYPE to ENVelope (bench-verified on FW 03.000); switch back with ACQ:TYPE REFR before testing. | 405 |
| `MASK:YPOSition` | S/Q | `<Yposition>` unit div | Moves the mask vertically within the display. | 405 |
| `MASK:YSCale` | S/Q | `<Yscale>` unit % | Changes the vertical scaling to stretch or compress the mask in y-direction. | 405 |
| `MASK:XWIDth` | S/Q | `<Xaddition>` unit div | Changes the width of the mask in horizontal direction. | 405 |
| `MASK:YWIDth` | S/Q | `<Yaddition>` unit div | Changes the width of the mask in vertical direction. | 406 |
| `MASK:SAVE` | S | `<Filename>` String parameter Path and file name | Saves the current mask in the specified file. | 406 |
| `MASK:LOAD` | S | `<Filename>` String parameter Path and file name | Loads a stored mask from the specified file. | 406 |
| `MASK:ACTion:SOUNd:EVENt:MODE`<br>`MASK:ACTion:SCRSave:EVENt:MODE`<br>`MASK:ACTion:WFMSave:EVENt:MODE`<br>`MASK:ACTion:PULSe:EVENt:MODE` | S/Q | `<EventMode>` `OFF` · `EACH`; *RST **OFF** | Defines when and how often the action is executed.; SOUNd: Generates a beep sound on mask violation.; PULSe: Creates a pulse on the [AuxOut] connector. | 407 |
| `MASK:ACTion:STOP:EVENt:MODE` | S/Q | `<EventMode>` `OFF` · `CYCLic`; *RST **OFF** | Stops the waveform acquisition, after mask is violated for a defined number of times. | 407 |
| `MASK:ACTion:STOP:EVENt:COUNt` | S/Q | `<EventCount>` Integer value, number of the violations that executes the action. | Sets the number of mask violations after which the action is executed. | 408 |
| `MASK:ACTion:SCRSave:DESTination` | S/Q | `<Path>` String parameter | Defines the directory for a screenshot that is saved on mask violation. | 408 |
| `MASK:ACTion:WFMSave:DESTination` | S/Q | `<Path>` String parameter | Defines the directory for waveform data files that are saved on mask violation. | 408 |
| `MASK:ACTion:YOUT:ENABle` | S/Q | `<Yout>` `ON` · `OFF`; *RST **OFF** | Creates a pulse on the [AuxOut] connector if the mask is violated. | 409 |
| `MASK:TEST` | S/Q | `<Test>` `RUN` · `STOP` · `PAUSe`; *RST **STOP** | Starts, finishes or interrupts a mask test.<br>Bench: MASK:CHCopy switches ACQuire:TYPE to ENVelope; set ACQ:TYPE REFR before MASK:TEST RUN, otherwise the test runs on the accumulated envelope (bench-verified).<br>Bench: RUN can answer -221 Settings conflict; a fresh SINGle and RUN usually clears it. Never re-learn the mask from the signal under test.<br>Bench: With noise on the signal, use TRIGger:A:EDGE:FILTer:HFReject ON so the edge trigger does not fire on the falling slope; otherwise the trace sits 180 degrees off the mask. | 409 |
| `MASK:RESet:COUNter` | E |  | Sets the counters of passed and failed acquisitions to zero. | 409 |
| `MASK:COUNt?` | Q | returns `<TotalCount>` Total number of tested acquisitions | Returns the number of tested acquisitions. | 410 |
| `MASK:VCOunt?` | Q | returns `<ViolationCount>` Total number of violations | Returns the number of acquisitions that hit the mask. | 410 |
| `MASK:CAPTure[:MODE]` | S/Q | `<CaptureMode>` `ALL` · `FAILED`; *RST **ALL** | Only available with history.<br>Bench: FAILED keeps only the failing acquisitions in the history, so they can be examined afterwards (bench-verified). | 410 |

#### General settings (16.8.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `SPECtrum[:STATe]` | S/Q | `<State>` `ON` · `OFF` | Switches on the spectrum analysis. | 411 |
| `SPECtrum:SOURce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4` | Selects the source for the spectrum analysis diagrams. | 411 |
| `SPECtrum:FREQuency:WINDow:TYPE` | S/Q | `<WindowFunction>` `RECTangular` · `HAMMing` · `HANNing` · `BLACkmanharris` · `FLATtop` | Window functions are multiplied with the input values and thus can improve the spec- trum analysis display. | 411 |
| `SPECtrum:FREQuency:MAGNitude:SCALe` | S/Q | `<MagnitudeScale>` `LINear` · `DBM` · `DBV` · `DBUV` | Defines the scaling unit of the y-axis. | 412 |
| `SPECtrum:FREQuency:POSition` | S/Q | `<Position>` unit div | Defines the vertical position of the spectrum. | 412 |
| `SPECtrum:FREQuency:SCALe` | S/Q | `<Scale>` Range values and unit depend on SPECtrum:FREQuency: MAGNitude:SCALe. | Sets the vertical scale of the spectrum analysis waveform. | 413 |
| `DISPlay:CBAR:FFT[:POSition]` | S/Q | `<DividerPosition>` unit px | Defines the position of the divide bar between normal waveform and FFT window. | 413 |
| `SPECtrum:FREQuency:CENTer` | S/Q | `<CenterFrequency>`  | Defines the position of the displayed frequency domain, which is (Center - Span/2) to (Center + Span/2). | 413 |
| `SPECtrum:FREQuency:FULLspan` | E |  | Performs the spectrum analysis calculation for the full frequency span. | 413 |
| `SPECtrum:FREQuency:SPAN` | S/Q | `<Span>`  | The span is specified in Hertz and defines the width of the displayed frequency range, which is (Center - Span/2) to (Center + Span/2). | 413 |
| `SPECtrum:FREQuency:STARt` | S/Q | `<StartFrequency>` range Depends on various other settings, mainly on time base, span/RBW ratio, and center frequency. | Defines the start frequency of the displayed frequency domain at the left display edge: Center - Span/2 You can set start and stop frequency instead of defining a… | 414 |
| `SPECtrum:FREQuency:STOP` | S/Q | `<StopFrequency>` range Depends on various other settings, mainly on time base, span/RBW ratio, and center frequency. | Defines the stop frequency of the displayed frequency domain at the right display edge: Center + Span/2 You can set start and stop frequency instead of defining a… | 414 |
| `SPECtrum:FREQuency:BANDwidth[:RESolution][:VALue]` | S/Q | `<ResolutionBandwidth>Range:` Depends on various other settings. | Defines the resolution bandwidth - the minimum frequency step at which the individual components of a spectrum can be distinguished. | 414 |
| `SPECtrum:FREQuency:BANDwidth[:RESolution]:RATio` | S/Q | `<SpanRBWratio>` range The value is changed in 2^n steps from 2^10 to 2^15 (1024, 2048,4096,8192,16384, 32768). | Defines the ratio of span (Hz) / resolution bandwidth (Hz). | 414 |
| `SPECtrum:TIME:POSition` | S/Q | `<TimePosition>`  | Sets the time position of the analyzed time range. | 415 |
| `SPECtrum:TIME:RANGe` | S/Q | `<TimeRange>`  | Sets the time range for the time domain diagram. | 415 |
| `SPECtrum:WAVeform:AVERage[:ENABle]`<br>`SPECtrum:WAVeform:MAXimum[:ENABle]`<br>`SPECtrum:WAVeform:MINimum[:ENABle]`<br>`SPECtrum:WAVeform:SPECtrum[:ENABle]` | S/Q | `<WaveformEnable>` `ON` · `OFF`; *RST **ON** | Enables/diables the indicated waveform. | 415 |
| `SPECtrum:FREQuency:AVERage:COUNt` | S/Q | `<AverageCount>`  | Defines the number of spectrums used for averaging. | 415 |
| `SPECtrum:FREQuency:AVERage:COMPlete?` | Q | `<AverageComplete>`  | Returns the state of spectrum averaging. | 416 |
| `SPECtrum:FREQuency:RESet` | E |  | Resets the Min Hold, Max Hold, Spectrum and Average waveforms to the current waveform. | 416 |
| `SPECtrum:WAVeform:AVERage:DATA?`<br>`SPECtrum:WAVeform:MAXimum:DATA?`<br>`SPECtrum:WAVeform:MINimum:DATA?`<br>`SPECtrum:WAVeform:SPECtrum:DATA?` | Q | returns `<Data>` List of values | Returns the data of the indicated waveform points for transmission from the instrument to the controlling computer. | 417 |
| `SPECtrum:WAVeform:AVERage:DATA:HEADer?`<br>`SPECtrum:WAVeform:MAXimum:DATA:HEADer?`<br>`SPECtrum:WAVeform:MINimum:DATA:HEADer?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:HEADer?` | Q | returns `<Header>` StringData | Returns information on the indicated waveform. | 417 |
| `SPECtrum:WAVeform:AVERage:DATA:POINts?`<br>`SPECtrum:WAVeform:MAXimum:DATA:POINts?`<br>`SPECtrum:WAVeform:MINimum:DATA:POINts?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:POINts?` | Q | returns `<DataPoints>`  | Returns the number of data samples that are returned with SPECtrum:WAVeform:xxx:DATA for the indicated waveform. | 417 |
| `SPECtrum:WAVeform:AVERage:DATA:XINCrement?`<br>`SPECtrum:WAVeform:MAXimum:DATA:XINCrement?`<br>`SPECtrum:WAVeform:MINimum:DATA:XINCrement?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:XINCrement?` | Q | returns `<Xincrement>`  | Return the level difference between two adjacent samples of the indicated waveform. | 417 |
| `SPECtrum:WAVeform:AVERage:DATA:XORigin?`<br>`SPECtrum:WAVeform:MAXimum:DATA:XORigin?`<br>`SPECtrum:WAVeform:MINimum:DATA:XORigin?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:XORigin?` | Q | returns `<Xorigin>`  | Returns the frequency of the first sample of the indicated waveform. | 418 |
| `SPECtrum:WAVeform:AVERage:DATA:YINCrement?`<br>`SPECtrum:WAVeform:MAXimum:DATA:YINCrement?`<br>`SPECtrum:WAVeform:MINimum:DATA:YINCrement?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:YINCrement?` | Q | returns `<Yincrement>`  | Returns the voltage value per bit of the indicated waveform. | 418 |
| `SPECtrum:WAVeform:AVERage:DATA:YORigin?`<br>`SPECtrum:WAVeform:MAXimum:DATA:YORigin?`<br>`SPECtrum:WAVeform:MINimum:DATA:YORigin?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:YORigin?` | Q | returns `<Yorigin>`  | Returns the vertical bit resolution of the indicated waveform. | 418 |
| `SPECtrum:WAVeform:AVERage:DATA:YRESolution?`<br>`SPECtrum:WAVeform:MAXimum:DATA:YRESolution?`<br>`SPECtrum:WAVeform:MINimum:DATA:YRESolution?`<br>`SPECtrum:WAVeform:SPECtrum:DATA:YRESolution?` | Q | returns `<Yresolution>`  | Returns the vertical bit resolution of the indicated waveform. | 418 |

#### XY-Waveforms (16.8.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `DISPlay:MODE` | S/Q | `<Mode>` `YT` · `XY`; *RST **YT** | Sets the diagram mode. | 419 |
| `DISPlay:XY:XSOurce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH1** | Defines the source to be displayed in x-direction in an XY-diagram, replacing the usual time base. | 419 |
| `DISPlay:XY:Y1Source` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH2** | Defines the first source to be displayed in y-direction in an XY-diagram. | 419 |
| `DISPlay:XY:Y2Source` | S/Q | `<Source>` `NONE` · `CH1` · `CH2` · `CH3` · `CH4`; *RST **NONE** | Defines an optional second source to be displayed in y-direction in an XY-diagram. | 420 |

#### Digital voltmeter (16.8.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `DVM<m>:ENABle` | S/Q | `<VoltmeterEnable>` `ON` · `OFF`; *RST **OFF** | Enables and disables all configured meter measurements. | 420 |
| `DVM<m>:SOURce` | S/Q | suffix `<m>` 1..4<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` | Selects an analog channel as the source of the selected meter measurement. | 420 |
| `DVM<m>:TYPE` | S/Q | suffix `<m>` 1..4<br>`<MeasurementType>` `DC` · `ACDCrms` · `ACRMs` · `OFF`; *RST **OFF** | Sets the measurement type for the indicated DVM measurement. | 420 |
| `DVM<m>:RESult[:ACTual]?` | Q | suffix `<m>` 1..4 | Returns the current value of the selected meter measurement. | 421 |
| `DVM<m>:RESult[:ACTual]:STATus?` | Q | suffix `<m>` 1..4<br>returns `<CurrentValue>` Measured value<br>returns `<Status>` Decimal status value | Returns the result value and the status of the result. | 421 |

#### Trigger counter (16.8.6)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TCOunter:ENABle` | S/Q | `<Enable>` `ON` · `OFF` | Enables or disables the trigger counter measurements. | 422 |
| `TCOunter:SOURce` | S/Q | `<CounterSource>` `CH1` · `CH2` · `CH3` · `CH4` · `TRIGger`; *RST **TRIG** | Sets the measurement source for the counter. | 422 |
| `TCOunter:RESult[:ACTual]:FREQuency?` | Q | returns `<FrequencyValue>` unit Hz | Returns the frequency of the trigger source. | 422 |
| `TCOunter:RESult[:ACTual]:PERiod?` | Q | returns `<PeriodValue>` unit s | Returns the period of the trigger source. | 422 |

#### Bode plot setup (16.8.7)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BPLot:ENABle` | S/Q | `<PlotEnable>` `ON` · `OFF`; *RST **OFF** | Enables the Bode plot diagram. | 423 |
| `BPLot:AUToscale` | E |  | Sets the start and stop frequencies for the Bode plot automatically. | 423 |
| `BPLot:AMPLitude:PROFile:COUNt` | S/Q | `<NumberOfPoints>` range 2 to 16; *RST **4** | Sets the number of different points that you can define for the amplitude profile. | 423 |
| `BPLot:AMPLitude:PROFile:POINt<n>:AMPLitude` | S/Q | `<AmplitudeMode>`  | Sets the amplitude of the specified point of the amplitude profile. | 424 |
| `BPLot:AMPLitude:PROFile:POINt<n>:FREQuency` | S/Q | `<Frequency>`  | Sets the frequency of the specified point of the amplitude profile. | 424 |
| `BPLot:AMPLitude:MODE` | S/Q | `<AmplitudeMode>` `CONStant` · `PROFil`; *RST **CONS** | Sets the amplitude mode. | 424 |
| `BPLot:FREQuency:DATA?` | Q | `<FrequencyData>`  | Returns the data of the frequency waveform.<br>Bench: Honours FORMat; ASCii rounds to 4 significant digits, use REAL,32. Poll BPLot:STATe? until STOP after a RUN. | 424 |
| `BPLot:FREQuency:STARt` | S/Q | `<StartFrequency>` range 10 to 25e6; *RST **100** | Sets the start frequency of the sweep for the Bode plot. | 425 |
| `BPLot:FREQuency:STOP` | S/Q | `<StopFrequency>` range 10 to 25e6; *RST **1e6** | Sets the stop frequency of the sweep for the Bode plot. | 425 |
| `BPLot:INPut[:SOURce]` | S/Q | `<InputSource>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH1** | Selects the channel for the input signal of the DUT. | 425 |
| `BPLot:MEASurement:DELay` | S/Q | `<MeasDelay>` range 0 to 10.0; *RST **0** | Sets a time delay, that the system waits before measuring the next point of the bode plot. | 425 |
| `BPLot:MEASurement:POINt[:DISPlay]` | S/Q | `<PointDisplay>` `ON` · `OFF` | Enables the display of the measurement points in the bode plot. | 425 |
| `BPLot:OUTPut[:SOURce]` | S/Q | `<OutputSource>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH2** | Selects the channel for the output signal of the DUT. | 426 |
| `BPLot:POINts:LOGarithmic` | S/Q | `<PointsPerDecade>` range 10 to 500; *RST **10** | Sets the number of points per decade that are measured. | 426 |
| `BPLot:REPeat` | S/Q | `<RepeatedMeasurement>` `ON` · `OFF`; *RST **OFF** | Repeats the measurement, using the same parameters. | 426 |
| `BPLot:RESet` | E |  | Deletes all test results. | 426 |
| `BPLot:STATe` | S/Q | `<PlotState>` `RUN` · `STOP`; *RST **STOP** | Starts the Bode plot measurement. | 426 |
| `BPLot:GAIN:DATA?` | Q | `<GainData>`  | Returns the data of the gain waveform.<br>Bench: Honours FORMat; ASCii rounds to 4 significant digits, use REAL,32. Poll BPLot:STATe? until STOP after a RUN. | 427 |
| `BPLot:GAIN:ENABle` | S/Q | `<Enable>` `ON` · `OFF`; *RST **ON** | Enables the gain waveform for the Bode plot. | 427 |
| `BPLot:GAIN:POSition` | S/Q | `<WaveformPosition>` range -20.0 to 20.0; *RST **3.0** | Sets the vertical position of the gain waveform in divisions. | 427 |
| `BPLot:GAIN:SCALe` | S/Q | `<WaveformScale>` range 0.1 to 20.0; *RST **20.0** | Sets the vertical scale for the gain waveform. | 427 |
| `BPLot:PHASe:DATA?` | Q | `<PhaseData>`  | Returns the data of the phase waveform.<br>Bench: Honours FORMat; ASCii rounds to 4 significant digits, use REAL,32. Poll BPLot:STATe? until STOP after a RUN. | 428 |
| `BPLot:PHASe:ENABle?` | Q | `<Enable>` `ON` · `OFF`; *RST **ON** | Enables the phase waveform for the Bode plot. | 428 |
| `BPLot:PHASe:POSition?` | Q | `<WaveformPosition>` range -20.0 to 20.0; *RST **0.0** | Sets the vertical position of the phase waveform in divisions. | 428 |
| `BPLot:PHASe:SCALe?` | Q | `<WaveformScale>` range 0.1 to 45.0; *RST **45.0** | Sets the vertical scale for the phase waveform. | 428 |
| `BPLot:AMPLitude:ENABle` | S/Q | `<Enable>` `ON` · `OFF`; *RST **OFF** | Enables the amplitude waveform for the Bode plot. | 428 |
| `BPLot:AMPLitude:POSition` | S/Q | `<WaveformPosition>` range -10.0 to 10.0; *RST **-5.0** | Sets the vertical position of the amplitude waveform in divisions. | 429 |
| `BPLot:AMPLitude:SCALe` | S/Q | `<WaveformScale>` range 0.1 to 2.0; *RST **1.0** | Sets the vertical scale for the amplitude waveform. | 429 |
| `BPLot:MARKer<m>:DIFFerence:FREQ?` | Q | `<FrequencyDifference>`  | Returns the delta value of the frequency between the two markers. | 429 |
| `BPLot:MARKer<m>:DIFFerence:GAIN?` | Q | `<GainDifference>`  | Returns the delta value of the gain between the two markers. | 429 |
| `BPLot:MARKer<m>:DIFFerence:PHASe?` | Q | `<PhaseDifference>`  | Returns the delta value of the phase between the two markers. | 430 |
| `BPLot:MARKer<m>:FREQuency` | S/Q | suffix `<m>` 1..2<br>`<MarkerFrequency>`  | Returns the frequency for the specified marker. | 430 |
| `BPLot:MARKer<m>:GAIN?` | Q | suffix `<m>` 1..2<br>`<PhaseValue>`  | Returns the gain for the specified marker. | 430 |
| `BPLot:MARKer<m>:INDex` | S/Q | suffix `<m>` 1..2<br>`<Index>`  | Returns the index for the specified marker. | 430 |
| `BPLot:MARKer<m>:PHASe?` | Q | suffix `<m>` 1..2<br>`<GainValue>`  | Returns the phase value for the specified marker. | 430 |
| `BPLot:MARKer<m>:SSCReen` | E | suffix `<m>` 1..2 | Resets the marker to their initial positions. | 431 |

### 6.7 Documenting results

#### Format settings (16.9.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `FORMat[:DATA]` | S/Q | `<DataFormat>` `ASCii` · `REAL` · `UINTeger`; *RST **ASC**<br>`<Accuracy>` `0` · `8` · `16` · `32`; *RST **0** | Defines the format for data export with; CHANnel<m>:DATA?on page 433; on page 436; on page 437; REFCurve<m>:DATA?on page 438 | 431 |
| `FORMat:BORDer` | S/Q | `<ByteOrder>` `MSBFirst` · `LSBFirst`; *RST **MSBF** | Defines the byte order for binary data export if FORMat[:DATA] is set to REAL or UINT,16\|32. | 433 |
| `CHANnel<m>:DATA?` | Q | suffix `<m>` 1..4<br>returns `<Data>` List of values according to the format settings - the voltages of recorded waveform… | Returns the data of the analog channel waveform for transmission from the instrument to the controlling computer.<br>Bench: Volts = YORigin + YINCrement * code for UINT formats (verified FW 03.000). FORMat is not part of SYSTem:SET. | 433 |
| `CHANnel<m>:DATA:HEADer?` | Q | suffix `<m>` 1..4<br>`<DataHeader>` Comma-separated value list Example: -9.477E-008,9.477E-008,120000,1 | Returns information on the channel waveform. | 434 |
| `CHANnel<m>:DATA:POINts` | S/Q | `<PointSelection>` `DEFault` · `MAXimum` · `DMAXimum`; *RST **DEFault** | As a setting, the command selects a range of samples that will be returned with CHANnel<m>:DATA?<br>Bench: MAX/DMAX only take effect while stopped. At 6 MSa, REAL,32 transfers 24 MB in ~1.4 s; UINT,16 is lossless for the 10-bit ADC at half the size. | 434 |
| `CHANnel<m>:DATA:ENVelope?` | Q | suffix `<m>` 1..4<br>`<EnvelopeData>` List of values according to the format settings - the voltages of the envelope points. | Returns the data of the envelope. | 436 |
| `CHANnel<m>:DATA:ENVelope:HEADer?` | Q | suffix `<m>` 1..4<br>`<EnvelopeHeader>` Comma-separated value list Example: -9.477E-008,9.477E-008,200000,2 | Returns information on the envelope waveform. | 436 |
| `CALCulate:MATH<m>:DATA?` | Q | suffix `<m>` 1..5<br>returns `<Data>` List of values according to the format settings - voltages, or magnitudes of a spectrum. | Returns the data of the math waveform points for transmission from the instrument to the controlling computer.<br>Bench: Volts = YORigin + YINCrement * code for UINT formats (verified FW 03.000). FORMat is not part of SYSTem:SET. | 437 |
| `CALCulate:MATH<m>:DATA:HEADer?` | Q | suffix `<m>` 1..5<br>returns `<Header>` Comma-separated value list Example: -9.477E-008,9.477E-008,120000,1 | Returns information on the math waveform. | 437 |
| `CALCulate:MATH<m>:DATA:POINts?` | Q | suffix `<m>` 1..5<br>returns `<DataPoints>` Number of data points | Returns the number of data samples that are returned with CALCulate:MATH<m>: DATA?. | 437 |
| `REFCurve<m>:DATA?` | Q | suffix `<m>` 1..4<br>returns `<Data>` List of values according to the format settings. | Returns the data of the reference waveform for transmission from the instrument to the controlling computer.<br>Bench: Volts = YORigin + YINCrement * code for UINT formats (verified FW 03.000). FORMat is not part of SYSTem:SET. | 438 |
| `REFCurve<m>:DATA:HEADer?` | Q | suffix `<m>` 1..4<br>`<Header>` Comma-separated value list Example: -9.477E-008,9.477E-008,200000,1 | Returns information on the reference waveform. | 438 |
| `MASK:DATA?` | Q | returns `<Data>` List of values according to the format settings - the y-values of the mask points. | Returns the data of the mask. | 439 |
| `MASK:DATA:HEADer?` | Q | returns `<DataHeader>` Comma-separated value list Example: -9.477E-008,9.477E-008,200000,2 | Returns information on the mask data that is delivered with MASK:DATA?. | 439 |
| `CHANnel<m>:DATA:XORigin?`<br>`CHANnel<m>:DATA:ENVelope:XORigin?`<br>`CALCulate:MATH<m>:DATA:XORigin?`<br>`MASK:DATA:XORigin?`<br>`LOGic<p>:DATA:XORigin?`<br>`DIGital<m>:DATA:XORigin?`<br>`REFCurve<m>:DATA:XORigin?` | Q | suffix `<m>` 1..4<br>returns `<Xorigin>` Time in s | Return the time of the first sample of the indicated waveform. | 441 |
| `CHANnel<m>:DATA:XINCrement?`<br>`CHANnel<m>:DATA:ENVelope:XINCrement?`<br>`CALCulate:MATH<m>:DATA:XINCrement?`<br>`MASK:DATA:XINCrement?`<br>`LOGic<p>:DATA:XINCrement?`<br>`DIGital<m>:DATA:XINCrement?`<br>`REFCurve<m>:DATA:XINCrement?` | Q | suffix `<m>` 1..4<br>returns `<Xincrement>` Time in s | Return the time difference between two adjacent samples of the indicated waveform. | 441 |
| `CHANnel<m>:DATA:YORigin?`<br>`CHANnel<m>:DATA:ENVelope:YORigin?`<br>`CALCulate:MATH<m>:DATA:YORigin?`<br>`MASK:DATA:YORigin?`<br>`LOGic<p>:DATA:YORigin?`<br>`DIGital<m>:DATA:YORigin?`<br>`REFCurve<m>:DATA:YORigin?` | Q | suffix `<m>` 1..4<br>returns `<Yorigin>` Voltage in V | Return the voltage value for binary value 0 of the indicated waveform. | 441 |
| `CHANnel<m>:DATA:YINCrement?`<br>`CHANnel<m>:DATA:ENVelope:YINCrement?`<br>`CALCulate:MATH<m>:DATA:YINCrement?`<br>`MASK:DATA:YINCrement?`<br>`LOGic<p>:DATA:YINCrement?`<br>`DIGital<m>:DATA:YINCrement?`<br>`REFCurve<m>:DATA:YINCrement?` | Q | suffix `<m>` 1..4<br>returns `<Yincrement>` Voltage in V | Return the voltage value per bit of the indicated waveform. | 442 |
| `CHANnel<m>:DATA:YRESolution?`<br>`CHANnel<m>:DATA:ENVelope:YRESolution?`<br>`CALCulate:MATH<m>:DATA:YRESolution?`<br>`MASK:DATA:YRESolution?`<br>`LOGic<p>:DATA:YRESolution?`<br>`DIGital<m>:DATA:YRESolution?`<br>`REFCurve<m>:DATA:YRESolution?` | Q | suffix `<m>` 1..4<br>returns `<Yresolution>` For default waveforms, the resolution is 8 bit. | Return the vertical bit resolution of the indicated waveform. | 442 |

#### Waveform data export to file (16.9.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `EXPort:WAVeform:SOURce` | S/Q | `<WaveformSource>` `CH1` · `CH2` · `CH3` · `CH4` · `D70` · `D158` · `MA1` · `RE1` · `RE2` · `RE3` · `RE4` | Defines the waveform to be exported. | 443 |
| `EXPort:WFMSave:DESTination` | S/Q | `<File>` String parameter String with directory path | Defines the directory where waveforms are saved. | 443 |
| `EXPort:WAVeform:NAME` | S/Q | `<FileName>` String parameter | Defines the path and filename for a waveform data file that will be saved with EXPort: WAVeform:SAVE. | 444 |
| `EXPort:WAVeform:SAVE` | E |  | Executes saving a waveform, for which the path and filename have been defined by EXPort:WAVeform:NAME. | 444 |

#### Screenshots (16.9.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `EXPort:SCRSave:DESTination` | S/Q | `<File>` String parameter String with directory path | Defines the directory where screenshots are saved. | 445 |
| `MMEMory:NAME` | S/Q | `<Filename>` String parameter | Defines the file name to store an image of the display with HCOPy[:IMMediate]. | 445 |
| `HCOPy:CWINdow` | S/Q | `<CloseWindow>` `ON` · `OFF`; *RST **ON** | If ON, open dialog boxes and menus are closed before the screenshot is saved. | 445 |
| `HCOPy[:IMMediate]` | E |  | Saves a screenshot to the specified file. | 445 |
| `HCOPy:DATA?` | Q | returns `<ScreenShot>` 488.2 block data | Returns the data of the image file.<br>Bench: Present although missing from SYSTem:TREE? on FW 03.000. PNG 1280x824, ~45 kB, ~0.7 s. | 446 |
| `HCOPy:FORMat`<br>`HCOPy:LANGuage` | S/Q | `<Format>` `BMP` · `PNG` · `GIF`; *RST **PNG** | Defines the format of the screenshot. | 446 |
| `HCOPy:SIZE:X?` | Q | returns `<Xsize>` Numeric value | Returns the number of horizontal pixels of the oscilloscope screen. | 446 |
| `HCOPy:SIZE:Y?` | Q | returns `<Ysize>` Numeric value | Returns the number of vertical pixels of the oscilloscope screen. | 446 |
| `HCOPy:COLor:SCHeme` | S/Q | `<ColorScheme>` `COLor` · `GRAYscale` · `INVerted`; *RST **COLor** | Defines the color mode for screenshots. | 446 |

#### Instrument settings: mass MEMomory subsystem (16.9.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `MMEMory:DRIVes?` | Q | `<Drive>` List of strings, for example, ""/INT"",""/USB_FRONT"" /INT: internal storage /USB_FRONT:… | Returns the storage devices available on the R&SRTB2. | 447 |
| `MMEMory:MSIS` | S/Q | `<Drive>` One of the available drives: /INT, or /USB_FRONT | Changes the default storage location (drive). | 448 |
| `MMEMory:MDIRectory` | S | `<DirectoryName>` String parameter Absolute path including the storage device, or relative to the cur-… | Creates a new directory with the specified name. | 448 |
| `MMEMory:CDIRectory` | S/Q | `<DirectoryName>` String parameter to specify the directory, including the storage device. | Specifies the current directory for file access. | 448 |
| `MMEMory:RDIRectory` | S | `<DirectoryName>` String parameter, absolute path or relative to the current direc- tory | Deletes the specified directory. | 448 |
| `MMEMory:DCATalog?` | Q | query: `<PathName>` String parameter Specifies the directory.<br>returns `<FileEntry>` String parameter List of subdirectory strings separated by commas. | Returns the subdirectories of the specified directory. | 449 |
| `MMEMory:DCATalog:LENGth?` | Q | query: `<PathName>` String parameter Specifies the directory.<br>returns `<DirCount>` Number of directories. | Returns the number of directories in specified directory. | 449 |
| `MMEMory:CATalog?` | Q | query: `<PathName>` String parameter Specifies the directory.<br>query: `<Format>` `ALL` · `WTIMe`<br>returns `<UsedMemory>` Total amount of storage currently used in the directory, in bytes.<br>returns `<FreeMemory>` Total amount of storage available in the directory, in bytes.<br>returns `<FileEntry>` String parameter All files of the directory are listed with their file name, format and… | Returns the a list of files contained in the specified directory. | 450 |
| `MMEMory:CATalog:LENGth?` | Q | query: `<PathName>` String parameter Directory to be queried, absolute or relative path<br>returns `<Count>` Number of files. | Returns the number of files in the specified directory. | 451 |
| `MMEMory:COPY` | S | `<FileSource>` String parameter Name and path of the file to be copied<br>`<FileDestination>` String parameter Name and path of the new file. | Copies data to another directory on the same or different storage device. | 451 |
| `MMEMory:MOVE` | S | `<FileSource>` String parameter Path and name of the file to be moved<br>`<FileDestination>` String parameter Path and name of the new file | Moves an existing file to a new location. | 451 |
| `MMEMory:DELete` | S | `<FileSource>` String parameter File name and path of the file to be removed. | Removes a file from the specified directory. | 452 |
| `MMEMory:DATA` | S/Q | `<Data>` 488.2 block data The block begins with character '#'.<br>`<FileName>` String parameter containing the file name | Writes data to the specified file in the current directory MMEMory:CDIRectory, or reads the data.<br>Bench: MMEM:DATA? "name" returns a file from the current directory (MMEM:CDIR "/INT") as block data; about 1 ms for 1 kB. | 452 |
| `MMEMory:STORe:STATe` | S | `<StateNumber>` range 1 to 1; *RST **1**<br>`<FileName>` String parameter File name, with or without file extension | Saves the current device settings to the specified file in the current directory. | 452 |
| `MMEMory:LOAD:STATe` | S | `<StateNumber>` range 1 to 1; *RST **1**<br>`<FileName>` String parameter File name, with or without file extension | Loads the device settings from the specified file in the current directory. | 453 |

### 6.8 General instrument setup

#### Display settings (16.10.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `DISPlay:LANGuage` | S/Q | `<Language>` `ENGLish` · `GERMan` · `FRENch` · `SPANish` · `RUSSian` · `SCHinese` · `TCHinese` · `JAPanese` · `KORean` · `ITALian` · `PORTuguese` · `CZECh` · `POLish`; *RST **ENGL** | Selects the language in which the button labels and other screen information is dis- played. | 454 |
| `DISPlay:DTIMe` | S/Q | `<DateTimeVisible>` `ON` · `OFF` | Switches the date and time display in the upper right corner of the screen on or off. | 454 |
| `DISPlay:CLEar[:SCReen]` | E |  | Deletes all waveforms and measurement results. | 454 |
| `DISPlay:PERSistence:TYPE` | S/Q | `<Type>` `OFF` · `TIME` · `INFinite`; *RST **OFF** | Defines how long every new data point remains on the screen. | 454 |
| `DISPlay:PERSistence:TIME` | S/Q | `<Time>` range 50e-3 to 12.8; unit s; *RST **50e-3** | Persistence time if persistence is active (DISPlay:PERSistence[:STATe] is set to TIME. | 455 |
| `DISPlay:PERSistence:CLEar` | E |  | Removes the displayed persistent waveform from the screen. | 455 |
| `DISPlay:PERSistence[:STATe]` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | Defines whether the waveform persists on the screen or whether the screen is refreshed continuously. | 455 |
| `DISPlay:PERSistence:INFinite` | S/Q | `<InfinitePersistence>` `ON` · `OFF`; *RST **OFF** | Sets the persistence time to infinite if DISPlay:PERSistence[:STATe] is ON. | 456 |
| `DISPlay:DIALog:CLOSe` | E |  | Closes an open message box. | 456 |
| `DISPlay:DIALog:MESSage` | S | `<MessageText>` String String that contains the message. | Sends a message text to the instrument and displays it in a message box. | 456 |
| `DISPlay:GRID:STYLe` | S/Q | `<Style>` `LINes` · `RETicle` · `NONE`; *RST **LIN** | Defines how the grid is displayed. | 456 |
| `DISPlay:INTensity:GRID` | S/Q | `<Intensity>` range 0 to 100; unit %; *RST **not available, *RST does not change the intensity** | Defines the brightness of the grid lines in the diagram. | 456 |
| `DISPlay:INTensity:WAVeform` | S/Q | `<Intensity>` range 0 to 100; unit %; *RST **not available, *RST does not change the intensity** | Defines the brightness of the waveform lines in the diagram. | 457 |
| `DISPlay:PALette` | S/Q | `<Palette>` `NORMal` · `INVerse`; *RST **NORM** | Sets the color and brightness of the displayed waveform samples depending on their cumulative occurance. | 457 |
| `DISPlay:STYLe` | S/Q | `<Style>` `VECTors` · `DOTS`; *RST **VECT** | Defines how the waveform data is displayed | 457 |
| `DISPlay:GRID:ANNotation[:ENABle]` | S/Q | `<State>` `ON` · `OFF`; *RST **ON** | Enables or disables the display of scale values and units for the x-axis and y-axis at the grid lines. | 458 |
| `DISPlay:GRID:ANNotation:TRACk` | S/Q | `<State>` `ON` · `OFF`; *RST **OFF** | If enabled, the grid moves with the waveforms, if you change the waveform's position in horizontal or vertical direction. | 458 |

#### System settings (16.10.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `CALibration` | S/Q |  | Calibration starts the self-alignment process. | 459 |
| `CALibration:STATe?` | Q | returns `<SelfAlignmentState>` NOALignment: no self-aligment was performed. | Returns the overall state of the self-alignment. | 459 |
| `SYSTem:NAME` | S/Q | `<Name>` String with max. | Defines an instrument name. | 459 |
| `SYSTem:DATE` | S/Q | `<Year>` unit a<br>`<Month>` range 1 to 12<br>`<Day>` range 1 to 31; unit d | Specifies the internal date for the instrument. | 459 |
| `SYSTem:TIME` | S/Q | `<Hour>` range 0 to 23; unit h<br>`<Minute>` range 0 to 59; unit min<br>`<Second>` range 0 to 59; unit s | Specifies the internal time for the instrument. | 460 |
| `SYSTem:COMMunicate:INTerface[:SELect]` | S/Q | `<Interface>` `USB` · `ETHernet` | Selects the interface for remote control and web browser access ( ETHernet only). | 460 |
| `SYSTem:BEEPer:CONTrol:STATe` | S/Q | `<ControlBeep>` `ON` · `OFF` | Enables or disables a sound for general control events, e.g. | 460 |
| `SYSTem:BEEPer:ERRor:STATe` | S/Q | `<ErrorBeep>` `ON` · `OFF` | Enables or disables the beep if an error occurs. | 460 |
| `SYSTem:BEEPer:TRIG:STATe` | S/Q | `<TriggerBeep>` `ON` · `OFF` | Enables or disables the beep if a trigger occurs. | 461 |
| `SYSTem:BEEPer[:IMMediate]` | E |  | Generates an immediate beep. | 461 |
| `SYSTem:SET` | S/Q | `<Setup>` 488.2 block data | Defines or queries the device settings that can be saved and load manually with [File] > "Device Settings".<br>Bench: Query returns the complete setup as a ~5 kB block; sending it back (SYST:SET #<block>) restores it in ~1 s. Does not include FORMat. | 461 |
| `SYSTem:ERRor[:NEXT]?` | Q | returns `<Error>` Error/event_number,"Error/event_description>[;Device-depend- ent info]" | Returns the oldest item of the error/event queue and removes it from the queue. | 461 |
| `SYSTem:ERRor:ALL?` | Q | returns `<ErrorList>` List of ErrorFormat List of: Error/event_number,"Error/event_description>[;Device-… | Returns a list of all error/event numbers and their description, and removes it from the error/event queue. | 461 |
| `SYSTem:PRESet` | E |  | Resets the instrument to the default state, has the same effect as *RST. | 462 |
| `SYSTem:EDUCation:PRESet` | E |  | Deletes the password of the education mode. | 462 |
| `SYSTem:DFPRint?` | Q | returns `<DeviceFootprint>` Block Data Information as block data. | Returns the device footprint of the instrument. | 462 |
| `SYSTem:TREE?` | Q | returns `<SystemTree>` List of commands | REturns a list of the implemented remote commands. | 462 |

#### LAN settings (16.10.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `SYSTem:COMMunicate:INTerface:ETHernet:DHCP` | S/Q | `<DHCP>` `ON` · `OFF` | Enables DHCP for automatic network parameter distribution. | 463 |
| `SYSTem:COMMunicate:INTerface:ETHernet:IPADdress`<br>`SYSTem:COMMunicate:INTerface:ETHernet:SUBNet`<br>`SYSTem:COMMunicate:INTerface:ETHernet:GATeway` | S/Q | `<FirstByte>` range 0 to 255<br>`<SecondByte>` range 0 to 255<br>`<ThirdByte>` range 0 to 255<br>`<FourthByte>` range 0 to 255 | Return or specify.; IP address of the instrument.; IP subnet mask used by the instrument.; IP gateway used by the instrument. | 463 |
| `SYSTem:COMMunicate:INTerface:ETHernet:IPPort` | S/Q | `<IPPort>` range 1024 to 65535 | Returns or specifies the IP port number (default = 5025). | 463 |
| `SYSTem:COMMunicate:INTerface:ETHernet:VXIPort` | S/Q | `<VXIport>` range 0 to 65535; *RST **1024** | Specifies the VXI-11 port number. | 464 |
| `SYSTem:COMMunicate:INTerface:ETHernet:HTTPport` | S/Q | `<HTTPport>` range 0 to 65535; *RST **80** | Returns the HTTP port number. | 464 |
| `SYSTem:COMMunicate:INTerface:ETHernet:TRANsfer` | S/Q | `<TransferMode>` `AUTO` · `FD10` · `FD100` · `HD10` · `HD100` | Enables automatic transfer speed selection, or selects one of the predefined settings that corresponds to your network data rate. | 464 |
| `SYSTem:COMMunicate:INTerface:ETHernet:MACaddress?` | Q | returns `<MACaddress>` String data String parameter | Returns the instrument's media access control address. | 464 |

#### USB settings (16.10.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `SYSTem:COMMunicate:INTerface:USB:CLASs` | S/Q | `<USBClass>` `TMC` · `VCP` · `MTP` | Selects the USB mode.; USB TMC (Test & Measurement Class); USB VCP (Virtual Com Port); USB MTP (Media Transfer Protocol) | 465 |

#### Trigger out (16.10.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `TRIGger:OUT:MODE` | S/Q | `<OutputMode>` `OFF` · `TRIGger` · `REFerence` · `MASK` · `GENerator`; *RST **OFF** | Defines which signals are generated at the [AuxOut] connector. | 465 |
| `TRIGger:OUT:PLENgth` | S/Q | `<PulseLength>` *RST **1E-6** | Defines the pulse width of the pulse at the [AuxOut] front connector (at trigger event or mask viaolation). | 465 |
| `TRIGger:OUT:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Defines the polarity of the pulse at the [AuxOut] front connector (at trigger event or mask viaolation). | 466 |

#### Firmware update (16.10.6)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `DIAGnostic:UPDate:TRANsfer:OPEN` | S/Q | `<TransferItem>` FIRMware | Opens a data transfer for the firmware update file, and checks for errors. | 466 |
| `DIAGnostic:UPDate:TRANsfer:DATA` | S | `<Offset>` Specifies the byte offset of the blockdata in the file.<br>`<Checksum>` CRC-16-CCITT type checksum calculated for the raw binary data in blockdata.<br>`<Data>` Block data is composed of a header #nm containing the length of the data followed by the… | Sends the firmware update file data to the internal RAM of the instrument | 466 |
| `DIAGnostic:UPDate:TRANsfer:CLOSe` | E |  | Closes the file transfer. | 466 |
| `DIAGnostic:UPDate:INSTall` | S | `<Path>` Empty string | Starts the firmware update. | 467 |

### 6.9 Serial bus analysis

#### General (16.11.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:TYPE` | S/Q | suffix `<b>` 1 | 2<br>`<Type>` `PARallel` · `CPARallel` · `I2C` · `SPI` · `SSPI` · `UART` · `CAN` · `LIN`; *RST **PARallel** | Defines the bus or interface type for analysis. | 467 |
| `BUS<b>:STATe` | S/Q | suffix `<b>` 1 | 2<br>`<State>` `ON` · `OFF`; *RST **OFF** | Switches protocol decoding on or off. | 468 |
| `BUS<b>:FORMat` | S/Q | suffix `<b>` 1 | 2<br>`<Format>` `ASCii` · `HEXadecimal` · `BINary` · `DECimal` · `OCTal`; *RST **HEX** | Sets the decoding format for the display on the screen. | 468 |
| `BUS<b>:LABel` | S/Q | suffix `<b>` 1..2<br>`<Label>` String value | Defines an additional name label for the selected bus. | 468 |
| `BUS<b>:LABel:STATe` | S/Q | suffix `<b>` 1..2<br>`<State>` `ON` · `OFF`; *RST **ON** | Displays or hides the bus label. | 468 |
| `BUS<b>:DSIGnals` | S/Q | suffix `<b>` 1 | 2<br>`<BitsSignals>` `ON` · `OFF`; *RST **ON** | Displays the individual bit lines above the decoded bus line. | 469 |
| `BUS<b>:DSIZe` | S/Q | suffix `<b>` 1 | 2<br>`<DisplaySize>` `SMALl` · `MEDium` · `LARGe` · `DIV2` · `DIV4`; *RST **MEDium** | Sets the height of the decoded bus signal on the screen. | 469 |
| `BUS<b>:POSition` | S/Q | suffix `<b>` 1 | 2<br>`<Position>` range 5 to -5; unit DIV; *RST **-3.5** | Sets the vertical position of the decoded bus signal in divisions on the screen. | 469 |
| `BUS<b>:RESult` | S/Q | suffix `<b>` 1 | 2<br>`<ShowResultTable>` `ON` · `OFF` | Displays or hides the table of decode results. | 469 |

#### SPI (with CS)- configuration (16.11.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:SPI:CS:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the chip select line. | 470 |
| `BUS<b>:SPI:CS:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **NEGative** | Selects whether the chip select signal is high active (high = 1) or low active (low = 1). | 471 |
| `BUS<b>:SPI:CLOCk:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the clock line. | 471 |
| `BUS<b>:SPI:CLOCk:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Selects if data is stored with the rising or falling slope of the clock. | 471 |
| `BUS<b>:SPI:DATA:SOURce`<br>`BUS<b>:SPI:MOSI:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<MosiSource>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the MOSI / MISO line. | 471 |
| `BUS<b>:SPI:MISO:SOURce` | S/Q | suffix `<b>`<br>`<MisoSource>` `CH1` · `CH2` · `CH3` · `CH4` · `NONE` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · …; *RST **NONE** | Selects the input channel of the optional MISO line. | 472 |
| `BUS<b>:SPI:DATA:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **POSitive** | Selects whether transmitted data is high active (high = 1) or low active (low = 1) on the data line. | 472 |
| `BUS<b>:SPI:MOSI:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<MosiPolarity>` `ACTLow` · `ACTHigh`; *RST **ACTH** | Selects if transmitted data is high active (high = 1) or low active (low = 1) on the MOSI/ MISO line. | 472 |
| `BUS<b>:SPI:MISO:POLarity` | S/Q | suffix `<b>`<br>`<MisoPolarity>` `ACTLow` · `ACTHigh`; *RST **ACTH** | Selects whether transmitted data is high active (high = 1) or low active (low = 1) on the MISO line. | 473 |
| `BUS<b>:SPI:BORDer` | S/Q | suffix `<b>` 1 | 2<br>`<BitOrder>` `MSBFirst` · `LSBFirst`; *RST **MSBFirst** | Defines if the data of the messages starts with MSB (most significant bit) or LSB (least significant bit). | 473 |
| `BUS<b>:SPI:SSIZe` | S/Q | suffix `<b>` 1 | 2<br>`<SymbolSize>` range 4 to 32; unit Bit; *RST **8** | Sets the word length, the number of bits in a message. | 473 |
| `BUS<b>:SSPI:CLOCk:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the clock line. | 474 |
| `BUS<b>:SSPI:CLOCk:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **POSitive** | Selects if data is stored with the rising or falling slope of the clock. | 474 |
| `BUS<b>:SSPI:DATA:SOURce`<br>`BUS<b>:SSPI:MOSI:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<MosiSource>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the MOSI / MISO line. | 474 |
| `BUS<b>:SSPI:MISO:SOURce` | S/Q | suffix `<b>`<br>`<MisoSource>` `CH1` · `CH2` · `CH3` · `CH4` · `NONE` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · …; *RST **NONE** | Selects the input channel of the optional MISO line. | 475 |
| `BUS<b>:SSPI:DATA:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **POSitive** | Selects whether transmitted data is high active (high = 1) or low active (low = 1) on the data line. | 475 |
| `BUS<b>:SSPI:MOSI:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<MosiPolarity>` `ACTLow` · `ACTHigh`; *RST **ACTH** | Selects if transmitted data is high active (high = 1) or low active (low = 1) on the MOSI/ MISO line. | 475 |
| `BUS<b>:SSPI:MISO:POLarity` | S/Q | suffix `<b>`<br>`<MisoPolarity>` `ACTLow` · `ACTHigh`; *RST **ACTH** | Selects whether transmitted data is high active (high = 1) or low active (low = 1) on the MISO line. | 476 |
| `BUS<b>:SSPI:BITime` | S/Q | suffix `<b>` 1 | 2<br>`<BurstIdleTime>` range 16e-9 to 838.832e-6; unit s; *RST **100e-6** | Within the idle time the data and clock lines are low. | 476 |
| `BUS<b>:SSPI:BORDer` | S/Q | suffix `<b>` 1 | 2<br>`<BitOrder>` `MSBFirst` · `LSBFirst`; *RST **MSBFirst** | Defines if the data of the messages starts with MSB (most significant bit) or LSB (least significant bit). | 476 |
| `BUS<b>:SSPI:SSIZe` | S/Q | suffix `<b>` 1 | 2<br>`<SymbolSize>` range 4 to 32; unit Bit; *RST **8** | Sets the word length, the number of bits in a message. | 476 |
| `TRIGger:A:SOURce:SPI` | S/Q | `<SpiSource>` `MOSI` · `MISO` | Selects the MOSI or the MISO line as trigger source. | 477 |
| `TRIGger:A:SPI:MODE` | S/Q | `<Mode>` `BSTart` · `BEND` · `NTHBit` · `PATTern`; *RST **BSTart** | Specifies the trigger mode for the SPI protocols (with and without CS). | 477 |
| `TRIGger:A:SPI:PATTern` | S/Q | `<DataPattern>` String with max. | Defines the bit pattern as trigger condition. | 478 |
| `TRIGger:A:SPI:PLENgth` | S/Q | `<PatternLength>` range 1 to 32; *RST **4** | Returns the number of bits in the previously defined bit pattern (TRIGger:A:SPI: PATTern). | 478 |
| `TRIGger:A:SPI:POFFset` | S/Q | `<PatternBitOffset>` range 0 to 4095; *RST **0** | Sets the number of bits before the first bit of the pattern. | 478 |
| `BUS<b>:SPI:FCOunt?` | Q | suffix `<b>` 1 | 2<br>returns `<FrameCount>` Total number of decoded frames. | Returns the number of decoded frames.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 479 |
| `BUS<b>:SPI:FRAMe<n>:STATus?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<Status>` `OK` · `INCFirst` · `INCLast` · `INSufficient` | Returns the overall state of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 479 |
| `BUS<b>:SPI:FRAMe<n>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 480 |
| `BUS<b>:SPI:FRAMe<n>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StopTime>` range depends on sample rate, record length, and time base; unit s | Returns the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 480 |
| `BUS<b>:SPI:FRAMe<n>:DATA:MOSI?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<DataMosi>` List of decimal values of data bytes | Returns the data words of the specified frame of the MOSI line.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 480 |
| `BUS<b>:SPI:FRAMe<n>:DATA:MISO?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<DataMiso>` List of decimal values of data bytes | Returns the data words of the specified frame of the MISO line.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 481 |
| `BUS<b>:SPI:FRAMe<n>:WCOunt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<WordCount>` Number of words | Returns the number of words in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 481 |
| `BUS<b>:SPI:FRAMe<n>:WORD<o>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified data word.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 481 |
| `BUS<b>:SPI:FRAMe<n>:WORD<o>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<StopTime>` range depends on sample rate, record length, and time base; unit s | Returns the end time of the specified data word.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 482 |
| `BUS<b>:SPI:FRAMe<n>:WORD<o>:MOSI?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Data>` Decimal value of the data word | Returns the data value of the specified word on the MOSI line.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 482 |
| `BUS<b>:SPI:FRAMe<n>:WORD<o>:MISO?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Data>` Decimal value of the data word | Returns the data value of the specified word on the optional MISO line.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 482 |

#### I²C - configuration (16.11.3)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:I2C:CLOCk:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Sets the input channel to which the clock line is connected. | 483 |
| `BUS<b>:I2C:DATA:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Sets the input channel to which the data line is connected. | 483 |
| `TRIGger:A:I2C:MODE` | S/Q | `<Mode>` `STARt` · `RESTart` · `STOP` · `MACKnowledge` · `PATTern`; *RST **STARt** | Specifies the trigger mode for I²C. | 484 |
| `TRIGger:A:I2C:ACCess` | S/Q | `<Access>` `READ` · `WRITe`; *RST **READ** | Toggles the trigger condition between Read and Write access of the master. | 485 |
| `TRIGger:A:I2C:AMODe` | S/Q | `<AdrMode>` `NORMal` · `EXTended`; *RST **NORMal** | Sets the lenght of the slave address. | 485 |
| `TRIGger:A:I2C:ADDRess` | S/Q | `<AddressString>` String with max. | Sets the address of the slave device. | 485 |
| `TRIGger:A:I2C:PATTern` | S/Q | `<DataPattern>` String with max. | Defines the bit pattern as trigger condition. | 486 |
| `TRIGger:A:I2C:PLENgth` | S/Q | `<PatternLength>` range 1 to 3; *RST **1** | Defines how many bytes are considered in the trigger condition. | 486 |
| `TRIGger:A:I2C:POFFset` | S/Q | `<PatternByteOffset>` range 0 to 4095; *RST **0** | Sets the number of bytes before the first byte of interest, relating to the end of the address bytes. | 486 |
| `BUS<b>:I2C:FCOunt?` | Q | suffix `<b>` 1 | 2<br>returns `<FrameCount>` Total number of decoded frames. | Returns the number of received frames.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 487 |
| `BUS<b>:I2C:FRAMe<n>:DATA?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<DataWords>` Comma-separated list of decimal values of the data bytes. | Returns the data words of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 487 |
| `BUS<b>:I2C:FRAMe<n>:STATus?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<State>` `INComplete` · `OK` · `UNEXpstop` · `INSufficient` · `ADDifferent` | Returns the overall state of the frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame).<br>Bench: Observed values include OK and INS (insufficient: frame not fully inside the acquisition). | 488 |
| `BUS<b>:I2C:FRAMe<n>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 488 |
| `BUS<b>:I2C:FRAMe<n>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<EndTime>` range depends on sample rate, record length, and time base; unit s | Returns the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 488 |
| `BUS<b>:I2C:FRAMe<n>:AACCess?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<Acknowledge>` `INComplete` · `ACK` · `NACK` · `EITHer` | Returns the address acknowledge bit value for the indicated frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 489 |
| `BUS<b>:I2C:FRAMe<n>:ACCess?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<Access>` `INComplete` · `READ` · `WRITE` · `EITHer` · `UNDF` | Returns the transfer direction - read or write access from master to slave.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 489 |
| `BUS<b>:I2C:FRAMe<n>:ACOMplete?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AddressComplete>` `ON` · `OFF` | Returns the state of the address.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 489 |
| `BUS<b>:I2C:FRAMe<n>:ADBStart?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AckStartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the address acknowledge bit.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 490 |
| `BUS<b>:I2C:FRAMe<n>:ADDRess?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AddressValue>` range 0 to 2047 | Returns the decimal address value of the indicated frame including the R/W bit.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 490 |
| `BUS<b>:I2C:FRAMe<n>:ADEVice?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<SlaveAddress>` range 0 to 1023 | Returns the decimal address value of the indicated frame without R/W bit.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 490 |
| `BUS<b>:I2C:FRAMe<n>:AMODe?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AddressMode>` `BIT7` · `BIT10` | Returns the address length.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 491 |
| `BUS<b>:I2C:FRAMe<n>:ASTart?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the address for the indicated frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 491 |
| `BUS<b>:I2C:FRAMe<n>:BCOunt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ByteCount im` Number of words (bytes) | Returns the number of data bytes in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 491 |
| `BUS<b>:I2C:FRAMe<n>:BYTE<o>:ACCess?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Acknowledge>` `INComplete` · `ACK` · `NACK` · `EITHer` | Returns the acknowledge bit value of the specified data byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 492 |
| `BUS<b>:I2C:FRAMe<n>:BYTE<o>:ACKStart?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<AckStartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the acknowledge bit of the specified byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 492 |
| `BUS<b>:I2C:FRAMe<n>:BYTE<o>:COMPlete?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteComplete>` `ON` · `OFF` | Returns the state of the byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 492 |
| `BUS<b>:I2C:FRAMe<n>:BYTE<o>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified data byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 493 |
| `BUS<b>:I2C:FRAMe<n>:BYTE<o>:VALue?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteValue>` range 0 to 255 | Returns the decimal value of the specified byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 493 |

#### UART - configuration (16.11.4)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:UART:RX:SOURce`<br>`BUS<b>:UART:DATA:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Selects the input channel of the data line. | 494 |
| `BUS<b>:UART:TX:SOURce` | S/Q | suffix `<b>`<br>`<TxSource>` `CH1` · `CH2` · `CH3` · `CH4` · `NONE` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · …; *RST **NONE** | Selects the input channel of the transmitter TX line. | 494 |
| `BUS<b>:UART:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<IdleState>` `IDLLow` · `IDLHigh`; *RST **IDLH** | Defines the logic levels of the bus. | 495 |
| `BUS<b>:UART:DATA:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `POSitive` · `NEGative`; *RST **POS** | Defines if the transmitted data on the bus is high (high = 1) or low (low = 1) active. | 495 |
| `BUS<b>:UART:SSIZe` | S/Q | suffix `<b>` 1 | 2<br>`<SymbolSize>` range 5 to 9; unit Bit; *RST **8** | Sets the number of data bits in a message. | 495 |
| `BUS<b>:UART:PARity` | S/Q | suffix `<b>` 1..2<br>`<Parity>` `ODD` · `EVEN` · `NONE`; *RST **NONE** | Defines the optional parity bit that is used for error detection. | 496 |
| `BUS<b>:UART:SBIT` | S/Q | suffix `<b>` 1..2<br>`<StopBitNumber>` `B1` · `B1_5` · `B2`; *RST **B1** | Sets the stop bits. | 496 |
| `BUS<b>:UART:BAUDrate` | S/Q | suffix `<b>` 1 | 2<br>`<Baudrate>` range 100 to 78.1E6; unit Bit; *RST **115200** | Sets the number of transmitted bits per second. | 496 |
| `BUS<b>:UART:BITime` | S/Q | suffix `<b>` 1 | 2<br>`<BurstIdleTime>` range Range depends on the bus configuration, mainly on bit rate and symbol size.; unit s | Sets the minimal time between two data frames (packets), that is, between the last stop bit and the start bit of the next frame. | 497 |
| `TRIGger:A:SOURce:UART` | S/Q | `<UartSource>` `RX` · `TX` | Selects the transmitter or receiver line as trigger source. | 497 |
| `TRIGger:A:UART:MODE` | S/Q | `<Mode>` `BSTart` · `SBIT` · `NTHSymbol` · `SYMBol` · `PATTern` · `PRERror` · `SPERror` · `BREak`; *RST **SBIT** | Specifies the trigger mode for UART/RS-232 interfaces. | 497 |
| `TRIGger:A:UART:PATTern` | S/Q | `<DataPattern>` *RST **1 = "00000001"** | Defines the bit pattern as trigger condition. | 498 |
| `TRIGger:A:UART:PLENgth` | S/Q | `<PatternLength>` range 1 to 4; *RST **1** | Defines how many symbols build up the serial pattern. | 498 |
| `TRIGger:A:UART:POFFset` | S/Q | `<PatternByteOffset>` range 0 to 4095; *RST **0** | Sets the number of symbols before the first symbol of the pattern. | 498 |
| `BUS<b>:UART:FCOunt?`<br>`BUS<b>:UART:RX:FCOunt?`<br>`BUS<b>:UART:TX:FCOunt?` | Q | suffix `<b>` 1 | 2<br>returns `<FrameCount>` Total number of decoded frames. | Return the number of decoded frames on the data line.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 499 |
| `BUS<b>:UART:FRAMe<n>:STARt?`<br>`BUS<b>:UART:RX:FRAMe<n>:STARt?`<br>`BUS<b>:UART:TX:FRAMe<n>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<TxFrameStart>` Time in s, range depends on sample rate, record length, and time base | Return the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 500 |
| `BUS<b>:UART:FRAMe<n>:STOP?`<br>`BUS<b>:UART:RX:FRAMe<n>:STOP?`<br>`BUS<b>:UART:TX:FRAMe<n>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<TxFrameStop>` Time in s, range depends on sample rate, record length, and time base | Return the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 500 |
| `BUS<b>:UART:FRAMe<n>:STATe?`<br>`BUS<b>:UART:RX:FRAMe<n>:STATe?`<br>`BUS<b>:UART:TX:FRAMe<n>:STATe?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<TxFrameState>` `STER` · `SPER` · `PRER` · `BRE` · `OK` · `INS` | Returns the status of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame).<br>Bench: Observed values include OK and INS (insufficient: frame not fully inside the acquisition). | 500 |
| `BUS<b>:UART:FRAMe<n>:WCOunt?`<br>`BUS<b>:UART:RX:FRAMe<n>:WCOunt?`<br>`BUS<b>:UART:TX:FRAMe<n>:WCOunt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<WordCount>` Number of words (symbols, characters) | Returns the number of symbols in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 501 |
| `BUS<b>:UART:FRAMe<n>:WORD<o>:SOURce?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Source>` TX or RX | Returns the line on which the specified word was transferred.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 501 |
| `BUS<b>:UART:FRAMe<n>:WORD<o>:STATe?`<br>`BUS<b>:UART:RX:FRAMe<n>:WORD<o>:STATe?`<br>`BUS<b>:UART:TX:FRAMe<n>:WORD<o>:STATe?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Status>` `OK` · `FRSTart` · `FRENd` · `FRMError` · `STERror` · `SPERror` · `PRERror` · `INSufficient` · `BREak` | Returns the status of the specified symbol (word).<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame).<br>Bench: Observed values include OK and INS (insufficient: frame not fully inside the acquisition). | 501 |
| `BUS<b>:UART:FRAMe<n>:WORD<o>:STARt?`<br>`BUS<b>:UART:RX:FRAMe<n>:WORD<o>:STARt?`<br>`BUS<b>:UART:TX:FRAMe<n>:WORD<o>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified symbol (word).<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 502 |
| `BUS<b>:UART:FRAMe<n>:WORD<o>:STOP?`<br>`BUS<b>:UART:RX:FRAMe<n>:WORD<o>:STOP?`<br>`BUS<b>:UART:TX:FRAMe<n>:WORD<o>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<StopTime>` range depends on sample rate, record length, and time base; unit s | Returns the end time of the specified symbol (word).<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 503 |
| `BUS<b>:UART:FRAMe<n>:WORD<o>:VALue?`<br>`BUS<b>:UART:FRAMe<n>:WORD<o>:RXValue?`<br>`BUS<b>:UART:FRAMe<n>:WORD<o>:TXValue?`<br>`BUS<b>:UART:RX:FRAMe<n>:WORD<o>:VALue?`<br>`BUS<b>:UART:TX:FRAMe<n>:WORD<o>:VALue?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<Value>` range 0 to 511 | Return the value of the specified symbol (word) on the Rx line and Tx line, respectively.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 503 |

#### CAN - configuration (16.11.5)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:CAN:DATA:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Sets the source of the data line. | 504 |
| `BUS<b>:CAN:TYPE` | S/Q | suffix `<b>` 1 | 2<br>`<SignalType>` `CANH` · `CANL`; *RST **CANH** | Selects the CAN-High or CAN-Low line. | 504 |
| `BUS<b>:CAN:SAMPlepoint` | S/Q | suffix `<b>` 1 | 2<br>`<SamplePoint>` range 10 to 90; unit %; *RST **50** | Sets the position of the sample point within the bit in percent of the nominal bit time. | 505 |
| `BUS<b>:CAN:BITRate` | S/Q | suffix `<b>` 1 | 2<br>`<BitRate>` range 100 to 2E06; unit Bit/s; *RST **50E03** | Sets the number of transmitted bits per second. | 505 |
| `TRIGger:A:CAN:TYPE` | S/Q | `<TriggerType>` `STOFrame` · `EOFrame` · `ID` · `IDDT` · `FTYPe` · `ERRCondition`; *RST **STOF** | Specifies the trigger mode for CAN. | 506 |
| `TRIGger:A:CAN:FTYPe` | S/Q | `<FrameType>` `DATA` · `REMote` · `ERRor` · `OVERload` · `ANY`; *RST **ERR** | Specifies the frame type to be triggered on if TRIGger:A:CAN:TYPE is set to FTYPe. | 506 |
| `TRIGger:A:CAN:ITYPe` | S/Q | `<IdentifierType>` `B11` · `B29` · `ANY`; *RST **B11** | Selects the length of the identifier: 11 bit for CAN base frames, or 29 bits for CAN extended frames. | 507 |
| `TRIGger:A:CAN:ICONdition` | S/Q | `<IdentifierCondition>` `EQUual` · `NEQual` · `GTHan` · `LTHan`; *RST **EQ** | Sets the comparison condition: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified value. | 507 |
| `TRIGger:A:CAN:IDENtifier` | S/Q | `<Identifier>` String containing binary pattern with 11 bit or 29 bit. | Defines the identifier pattern. | 507 |
| `TRIGger:A:CAN:DCONdition` | S/Q | `<DataCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQ** | Sets the comparison condition for data: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified value. | 507 |
| `TRIGger:A:CAN:DLC` | S/Q | `<DataLength>` range 0 to 8; unit Byte; *RST **1** | Defines the length of the data pattern - the number of bytes in the pattern. | 508 |
| `TRIGger:A:CAN:DATA` | S/Q | `<Data>` String containing binary pattern with max. | Defines the data pattern. | 508 |
| `TRIGger:A:CAN:ACKerror` | S/Q | `<AcknowledgeError>` `ON` · `OFF`; *RST **OFF** | Triggers on acknowledgement errors. | 508 |
| `TRIGger:A:CAN:BITSterror` | S/Q | `<BitStuffingError>` `ON` · `OFF`; *RST **ON** | Triggers on bit stuffing errors. | 508 |
| `TRIGger:A:CAN:CRCerror` | S/Q | `<CRCerror>` `ON` · `OFF`; *RST **OFF** | Triggers on errors in the Cyclic Redundancy Check. | 509 |
| `TRIGger:A:CAN:FORMerror` | S/Q | `<FormError>` `ON` · `OFF`; *RST **OFF** | Triggers on form errors. | 509 |
| `BUS<b>:CAN:FCOunt?` | Q | suffix `<b>` 1 | 2<br>returns `<FrameCount>` Total number of decoded frames. | Returns the number of received frames.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 510 |
| `BUS<b>:CAN:FRAMe<n>:TYPE?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameType>` `DATA` · `REMote` · `ERR` · `OVLD` | Returns the type of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 510 |
| `BUS<b>:CAN:FRAMe<n>:STATus?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameStatus>` `OK` · `BTST` · `CRCD` · `ACKD` · `CRC` · `EOFD` · `NOACk` · `INSufficient` | Returns the overall state of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 510 |
| `BUS<b>:CAN:FRAMe<n>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StartTime>` unit s | Returns the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 511 |
| `BUS<b>:CAN:FRAMe<n>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StopTime>` unit s | Returns the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 511 |
| `BUS<b>:CAN:FRAMe<n>:DATA?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameData>` Comma-separated list of decimal values of the data bytes. | Returns the data words of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 511 |
| `BUS<b>:CAN:FRAMe<n>:ACKState?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AcknowledgeState>` `OK` · `UNDF` | Returns the state of the acknowledge field.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 511 |
| `BUS<b>:CAN:FRAMe<n>:ACKValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<AcknowledgeValue>` Decimal value | Returns the value of the acknowledge field.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 512 |
| `BUS<b>:CAN:FRAMe<n>:CSSTate?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ChecksumState>` `OK` · `UNDF` | Returns the state of the checksum.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 512 |
| `BUS<b>:CAN:FRAMe<n>:CSValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ChecksumValue>` Decimal value | Returns the checksum value.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 512 |
| `BUS<b>:CAN:FRAMe<n>:DLCState?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<DLCState>` `OK` · `UNDF` | Returns the state of the data length code.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 513 |
| `BUS<b>:CAN:FRAMe<n>:DLCValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<DLCValue>` Non-negative integer | Returns the number of data bytes in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 513 |
| `BUS<b>:CAN:FRAMe<n>:IDSTate?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierState>` `OK` · `UNDF` | Returns the state of the identifier.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 513 |
| `BUS<b>:CAN:FRAMe<n>:IDTYpe?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierType>` `ANY` · `B11` · `B29` | Returns the length of the identifier: 11 bits for CAN base frames, 29 bits for CAN exten- ded frames.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 513 |
| `BUS<b>:CAN:FRAMe<n>:IDValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierValue>` Decimal value | Returns the identifier of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 514 |
| `BUS<b>:CAN:FRAMe<n>:BSEPosition?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ErrorPosition>` unit s; *RST **0** | Returns the position of the bit stuffing error in the specified frame (if available).<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 514 |
| `BUS<b>:CAN:FRAMe<n>:BCOunt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ByteCount>` Number of words (bytes) | Returns the number of data bytes in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 514 |
| `BUS<b>:CAN:FRAMe<n>:BYTE<o>:STATe?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteStatus>` `OK` · `UNDF` | Returns the state of the specified data byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 515 |
| `BUS<b>:CAN:FRAMe<n>:BYTE<o>:VALue?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteValue>` Decimal value | Returns the decimal value of the specified byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 515 |
| `SEARch:PROTocol:CAN:CONDition` | S/Q | `<SearchCondition>` `FRAMe` · `ERRor` · `IDENtifier` · `IDData` · `IDERror`; *RST **FRAM** | Sets the event or combination of events to be searched for. | 516 |
| `SEARch:PROTocol:CAN:FRAMe` | S/Q | `<Frame>` `SOF` · `EOF` · `OVERload` · `ERRor` · `DTA11` · `DTA29` · `REM11` · `REM29`; *RST **SOF** | Selects the frame type to be searched for. | 516 |
| `SEARch:PROTocol:CAN:ACKerror` | S/Q | `<AcknowledgeError>` `ON` · `OFF`; *RST **OFF** | Searches for acknowledgement errors. | 517 |
| `SEARch:PROTocol:CAN:BITSterror` | S/Q | `<BitStuffingError>` `ON` · `OFF`; *RST **OFF** | Searches for bit stuffing errors. | 517 |
| `SEARch:PROTocol:CAN:CRCerror` | S/Q | `<CRCerror>` `ON` · `OFF`; *RST **OFF** | Searches for errors in the Cyclic Redundancy Check. | 517 |
| `SEARch:PROTocol:CAN:FORMerror` | S/Q | `<FormError>` `ON` · `OFF`; *RST **OFF** | Searches for form errors. | 517 |
| `SEARch:PROTocol:CAN:FTYPe` | S/Q | `<FrameType>` `DATA` · `REMote` · `ANY` | Specifies the frame type to be searched for if SEARch:PROTocol:CAN:CONDition is set to IDENtifier. | 518 |
| `SEARch:PROTocol:CAN:ITYPe` | S/Q | `<IdType>` `B11` · `B29`; *RST **B11** | Selects the length of the identifier: 11 bit for CAN base frames, or 29 bits for CAN extended frames. | 518 |
| `SEARch:PROTocol:CAN:ICONdition` | S/Q | `<IdCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQU** | Sets the comparison condition for the identifier: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified… | 518 |
| `SEARch:PROTocol:CAN:IDENtifier` | S/Q | `<Identifier>` String containing binary pattern with max. | Defines the identifier pattern. | 519 |
| `SEARch:PROTocol:CAN:DLENgth` | S/Q | `<DataLength>` range 0 to 8; unit Byte; *RST **1** | Defines the length of the data pattern - the number of bytes in the pattern. | 519 |
| `SEARch:PROTocol:CAN:DCONdition` | S/Q | `<DataCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQU** | Sets the comparison condition for data: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified value. | 519 |
| `SEARch:PROTocol:CAN:DATA` | S/Q | `<Data>` String containing binary pattern with max. | Defines the data pattern. | 519 |

#### LIN - configuration (16.11.6)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:LIN:DATA:SOURce` | S/Q | suffix `<b>` 1 | 2<br>`<Source>` `CH1` · `CH2` · `CH3` · `CH4` · `D0` · `D1` · `D2` · `D3` · `D4` · `D5` · `D6` · `D7` · `D8` · `D9` · …; *RST **CH1** | Sets the source of the data line. | 520 |
| `BUS<b>:LIN:POLarity` | S/Q | suffix `<b>` 1 | 2<br>`<Polarity>` `IDLHigh` · `IDLLow`; *RST **IDLL** | Defines the idle state of the bus. | 520 |
| `BUS<b>:LIN:STANdard` | S/Q | suffix `<b>` 1 | 2<br>`<Standard>` `V1X` · `V2X` · `J2602` · `AUTO`; *RST **V1X** | Selects the version of the LIN standard that is used in the DUT. | 521 |
| `BUS<b>:LIN:BITRate` | S/Q | suffix `<b>` 1 | 2<br>`<BitRate>` range 1 kbit/s to 2.5 Mbit/s; unit Bit/s; *RST **9,6E03** | Sets the number of transmitted bits per second. | 521 |
| `TRIGger:A:LIN:TYPE` | S/Q | `<TriggerType>` `SYNC` · `WKFRame` · `ID` · `IDDT` · `ERRCondition`; *RST **SYNC** | Specifies the trigger mode for LIN. | 522 |
| `TRIGger:A:LIN:CHKSerror` | S/Q | `<ChecksumError>` `ON` · `OFF`; *RST **ON** | Triggers on a checksum error. | 522 |
| `TRIGger:A:LIN:IPERror` | S/Q | `<IdParityError>` `ON` · `OFF`; *RST **OFF** | Triggers on a parity error. | 522 |
| `TRIGger:A:LIN:SYERror` | S/Q | `<SyncError>` `ON` · `OFF`; *RST **OFF** | Triggers if synchronization caused an error. | 523 |
| `TRIGger:A:LIN:ICONdition` | S/Q | `<IdentifierCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQ** | Sets the comparison condition for the identifier: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified… | 523 |
| `TRIGger:A:LIN:IDENtifier` | S/Q | `<Identifier>` String containing binary pattern. | Defines the identifier pattern. | 523 |
| `TRIGger:A:LIN:DATA` | S/Q | `<Data>` String containing binary pattern with max. | Defines the data pattern. | 523 |
| `TRIGger:A:LIN:DCONdition` | S/Q | `<DataCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQ** | Sets the comparison condition for data: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified value. | 524 |
| `TRIGger:A:LIN:DLENgth` | S/Q | `<DataLength>` range 1 to 8; unit Byte; *RST **1** | Defines the length of the data pattern - the number of bytes in the pattern. | 524 |
| `BUS<b>:LIN:FCOunt?` | Q | suffix `<b>` 1 | 2<br>returns `<FrameCount>` Total number of decoded frames. | Returns the number of received frames of the active LIN bus.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 525 |
| `BUS<b>:LIN:FRAMe<n>:DATA?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameData>` Comma-separated list of decimal values of the data bytes. | Returns the data bytes of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 525 |
| `BUS<b>:LIN:FRAMe<n>:STATus?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameStatus>` `OK` · `UART` · `CHCKsum` · `PRERror` · `SYERror` · `WAKeup` · `INSufficient` · `ERR` · `LENer` | Returns the overall state of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 525 |
| `BUS<b>:LIN:FRAMe<n>:STARt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StartTime>` range depends on sample rate, record length, and time base; unit s | Returns the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 525 |
| `BUS<b>:LIN:FRAMe<n>:STOP?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<StopTime>` range depends on sample rate, record length, and time base; unit s | Returns the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 526 |
| `BUS<b>:LIN:FRAMe<n>:CSSTate?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ChecksumState>` `OK` · `ERR` · `UNDF` | Returns the checksum state of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 526 |
| `BUS<b>:LIN:FRAMe<n>:CSValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ChecksumValue>` Decimal value | Returns the checksum value.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 526 |
| `BUS<b>:LIN:FRAMe<n>:IDPValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierParityValue>Decimal value`  | Returns the parity value.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 527 |
| `BUS<b>:LIN:FRAMe<n>:IDSTate?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierState>` `OK` · `PRERror` · `UVAL` · `INSufficient` | Returns the identifier state of the selected frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 527 |
| `BUS<b>:LIN:FRAMe<n>:IDValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<IdentifierValue>` Decimal value | Returns the identifier value (address)<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 527 |
| `BUS<b>:LIN:FRAMe<n>:SYSTate?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<SyncFieldState>` `OK` · `ERR` · `UNDF` | Returns the state of the sync field for the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 528 |
| `BUS<b>:LIN:FRAMe<n>:SYValue?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<SyncFieldValue>` Decimal value | Returns the value of the synchronization field.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 528 |
| `BUS<b>:LIN:FRAMe<n>:VERSion?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<FrameVersion>` `V1X` · `V2X` · `UNK` | Returns the version of the LIN standard for the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 528 |
| `BUS<b>:LIN:FRAMe<n>:BCOunt?` | Q | suffix `<b>` 1 | 2, `<n>` *<br>returns `<ByteCount>` Number of words (bytes) | Returns the number of data bytes in the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 528 |
| `BUS<b>:LIN:FRAMe<n>:BYTE<o>:STATe?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteStatus>` `OK` · `INS` · `UART` | Returns the state of the specified data byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 529 |
| `BUS<b>:LIN:FRAMe<n>:BYTE<o>:VALue?` | Q | suffix `<b>` 1 | 2, `<n>` *, `<o>` *<br>returns `<ByteValue>` Decimal value | Returns the decimal value of the specified byte.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 529 |
| `SEARch:PROTocol:LIN:CONDition` | S/Q | `<SearchCondition>` `FRAMe` · `ERRor` · `IDENtifier` · `IDData` · `IDERror`; *RST **FRAM** | Sets the event or combination of events to be searched for. | 530 |
| `SEARch:PROTocol:LIN:FRAMe` | S/Q | `<Frame>` `SOF` · `WAKeup`; *RST **SOF** | Selects the frame type to be searched for. | 530 |
| `SEARch:PROTocol:LIN:IPERror` | S/Q | `<IdParityError>` `ON` · `OFF`; *RST **OFF** | Searches for parity errors. | 531 |
| `SEARch:PROTocol:LIN:CHKSerror` | S/Q | `<ChecksumError>` `ON` · `OFF`; *RST **OFF** | Searches for checksum errors. | 531 |
| `SEARch:PROTocol:LIN:SYERror` | S/Q | `<SyncError>` `ON` · `OFF`; *RST **OFF** | Searches for synchronization errors. | 531 |
| `SEARch:PROTocol:LIN:ICONdition` | S/Q | `<IdCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQU** | Sets the comparison condition for the identifier: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified… | 532 |
| `SEARch:PROTocol:LIN:IDENtifier` | S/Q | `<Identifier>` String containing binary pattern. | Defines the identifier pattern. | 532 |
| `SEARch:PROTocol:LIN:DLENgth` | S/Q | `<DataLength>` range 1 to 8; unit Byte; *RST **1** | Defines the length of the data pattern - the number of bytes in the pattern. | 532 |
| `SEARch:PROTocol:LIN:DCONdition` | S/Q | `<DataCondition>` `EQUal` · `NEQual` · `GTHan` · `LTHan`; *RST **EQU** | Sets the comparison condition for data: If the pattern contains at least one X (don't care), you can trigger on values equal or not equal to the specified value. | 532 |
| `SEARch:PROTocol:LIN:DATA` | S/Q | `<Data>` String containing binary pattern with max. | Defines the data pattern. | 533 |

### 6.10 Logic analyzer (MSO)

#### Logic channels (16.12.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `LOGic<p>:PROBe[:ENABle]?` | Q | suffix `<p>` 1..2<br>returns `<ProbeEnable>` `1` · `0` | Checks if the logic probe is connected. | 534 |
| `LOGic<p>:CURRent:STATe:MAXimum?`<br>`DIGital<m>:CURRent:STATe:MAXimum?`<br>`LOGic<p>:CURRent:STATe:MINimum?`<br>`DIGital<m>:CURRent:STATe:MINimum?` | Q | suffix `<m>` 0..15, `<p>` 1..2<br>returns `<CurrentState>` range 0 \| 1 | Both commands together return the current status of the indicated logic channel/pod regardless of the trigger settings, and even without any acquisition. | 534 |
| `LOGic<p>:STATe` | S/Q | suffix `<p>` 1..2<br>`<State>` `ON` · `OFF`; *RST **OFF** | Switches the logic pod on or off. | 535 |
| `DIGital<m>:PROBe[:ENABle]?` | Q | suffix `<m>` 0..15<br>returns `<ProbeEnable>` `ON` · `OFF` | Tests whether the digital probe is connected and recognized by the instrument. | 535 |
| `DIGital<m>:DISPlay` | S/Q | suffix `<m>` 0..15<br>`<State>` `ON` · `OFF`; *RST **OFF** | Enables and displays the indicated logic channel, or disables it. | 535 |
| `DIGital<m>:TECHnology` | S/Q | suffix `<m>` 0..15<br>`<ThresholdMode>` `TTL` · `ECL` · `CMOS` · `MANual`; *RST **MAN** | Selects the threshold voltage for various types of integrated circuits and applies it to the channel group to which the indicated logic channel belongs. | 535 |
| `LOGic<p>:THReshold` | S/Q | suffix `<p>` 1..2<br>`<ThresholdMode>` `TTL` · `ECL` · `CMOS` · `USER` | Selects the threshold voltage for the indicated pod.<br>Bench: The manual lists THReshold and THReshold:UDLevel as one command. On FW 03.000 THReshold takes the mode (TTL\|ECL\|CMOS\|USER) and THReshold:UDLevel takes the user level in volts. | 536 |
| `DIGital<m>:THReshold` | S/Q | suffix `<m>` 0..15<br>`<ThresholdLevel>` unit V; *RST **1.4** | Sets the logical threshold for the pod to which the indicated logic channel belongs. | 536 |
| `LOGic<p>:THReshold:UDLevel<n>` | S/Q | suffix `<p>` 1..2, `<n>` 1..2<br>`<Level>` unit V | User-defined threshold level of the logic pod, used when LOGic<p>:THReshold is USER.<br>Bench: Bench-verified on FW 03.000: LOG1:THR USER; LOG1:THR:UDL 0.5 for the ~1 V pattern-generator outputs.<br>Bench: The firmware also lists UDLevel2; presumably the level for the upper nibble of the pod (unverified). | 536 |
| `LOGic<p>:TYPE` | S/Q | `<DecimationMode>` `SAMPle` · `PDETect`; *RST **SAMP** | Selects the method to reduce the captured data of logical channels to a waveform with lower sample rate. | 537 |
| `LOGic<p>:HYSTeresis`<br>`DIGital<m>:HYSTeresis` | S/Q | suffix `<m>` 0..15, `<p>` 1..2<br>`<Hysteresis>` `SMALl` · `MEDium` · `LARGe` | Defines the size of the hysteresis to avoid the change of signal states due to noise. | 537 |
| `DIGital<m>:SIZE` | S/Q | suffix `<m>` 0..15<br>`<Size>` range 0.2 to 8; unit DIV; *RST **0.3** | Sets the vertical size of the indicated digital channel. | 537 |
| `DIGital<m>:POSition` | S/Q | suffix `<m>` 0..15<br>`<Position>` unit DIV | Sets the vertical position of the indicated vertical channel. | 537 |
| `DIGital<m>:LABel` | S/Q | suffix `<m>` 0..15<br>`<Label>` String value String parameter | Defines a label for the indicated logic channel. | 538 |
| `DIGital<m>:LABel:STATe` | S/Q | suffix `<m>` 0..15<br>`<State>` `ON` · `OFF`; *RST **OFF** | Displays or hides the label of the indicated logic channel. | 538 |
| `LOGic<p>:DATA?`<br>`DIGital<m>:DATA?` | Q | suffix `<m>` 0..15, `<p>` 1..2<br>`<WaveformData>` List of values according to the format settings. | Returns the data of the specified logic channel/pod for transmission from the instru- ment to the controlling computer.<br>Bench: Volts = YORigin + YINCrement * code for UINT formats (verified FW 03.000). FORMat is not part of SYSTem:SET.<br>Bench: Pod data: one value per sample, bit n = channel n of the pod (D0..D7 for pod 1). Use UINT,8: 7.5 MSa in about 0.9 s. With UINT,16 the pod value lands in the high byte. | 539 |
| `LOGic<p>:DATA:HEADer?`<br>`DIGital<m>:DATA:HEADer?` | Q | suffix `<m>` 0..15, `<p>` 1..2<br>`<Header>` Comma-separated value list Example: -9.477E-008,9.477E-008,200000,1 | Returns information on the waveform of the specified logic channel/pod. | 539 |
| `LOGic<p>:DATA:POINts`<br>`DIGital<m>:DATA:POINts` | S/Q | suffix `<m>` 0..15, `<p>` 1..2<br>`<PointSelection>` `DEFault` · `MAXimum` · `DMAXimum`; *RST **DEFault** | As a setting, the command selects a range of samples that will be returned with DIGital<m>:DATA?.<br>Bench: MAX/DMAX only take effect while stopped. At 6 MSa, REAL,32 transfers 24 MB in ~1.4 s; UINT,16 is lossless for the 10-bit ADC at half the size. | 540 |

#### Parallel bus - line configuration (16.12.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `BUS<b>:PARallel:WIDTh` | S/Q | suffix `<b>` 1..2<br>`<BusWidth>` range 1 to 4; unit Bit; *RST **4** | Sets the number of lines to be analyzed for the parallel bus. | 541 |
| `BUS<b>:CPARallel:WIDTh` | S/Q | suffix `<b>` 1..2<br>`<BusWidth>` range 1 to 15 (clock only) or 14 (clock and CS); unit Bit; *RST **4** | Sets the number of lines to be analyzed for the parallel clocked bus. | 541 |
| `BUS<b>:PARallel:DATA<m>:SOURce`<br>`BUS<b>:CPARallel:DATA<m>:SOURce` | S/Q | suffix `<b>` 1..2, `<m>`<br>`<DataSource>` D0..D15 | Defines the logic channel that is assigned to the selected bit. | 541 |
| `BUS<b>:CPARallel:CLOCk:SOURce` | S/Q | suffix `<b>` 1..2<br>`<ClockSource>` *RST **D0** | Selects the logic channel that is used as clock line. | 542 |
| `BUS<b>:CPARallel:CLOCk:SLOPe` | S/Q | suffix `<b>` 1..2<br>`<ClockSlope>` `POSitive` · `NEGative` · `EITHer` | Selects if the data is sampled on the rising or falling slope of the clock, or on both edges (EITHer). | 542 |
| `BUS<b>:CPARallel:CS:ENABle` | S/Q | suffix `<b>` 1..2<br>`<ChipSelectEnable>` `ON` · `OFF`; *RST **ON** | Enables and disables the chip select line. | 543 |
| `BUS<b>:CPARallel:CS:SOURce` | S/Q | suffix `<b>` 1..2<br>`<ChipSelectSource>` *RST **D1** | Selects the logic channel that is used as chip select line. | 543 |
| `BUS<b>:CPARallel:CS:POLarity` | S/Q | suffix `<b>` 1..2<br>`<Polarity>` `POSitive` · `NEGative` | Selects if the chip select signal is high active (high = 1) or low active (low = 1). | 543 |
| `BUS<b>:PARallel:FCOunt?`<br>`BUS<b>:CPARallel:FCOunt?` | Q | suffix `<b>` 1..2<br>returns `<FrameCount>` Total number of decoded frames. | Returns the number of decoded frames.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 544 |
| `BUS<b>:PARallel:FRAMe<n>:DATA?`<br>`BUS<b>:CPARallel:FRAMe<n>:DATA?` | Q | suffix `<b>` 1..2, `<n>` *<br>returns `<FrameData>` List of decimal values of data words | Returns the data words of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 544 |
| `BUS<b>:PARallel:FRAMe<n>:STATe?`<br>`BUS<b>:CPARallel:FRAMe<n>:STATe?` | Q | suffix `<b>` 1..2, `<n>` *<br>returns `<FrameStatus>` `OK` · `ERRor` · `INSufficient` | Returns the overall state of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 544 |
| `BUS<b>:PARallel:FRAMe<n>:STARt?`<br>`BUS<b>:CPARallel:FRAMe<n>:STARt?` | Q | suffix `<b>` 1..2, `<n>` *<br>returns `<StartTime>` unit s | Returns the start time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 545 |
| `BUS<b>:PARallel:FRAMe<n>:STOP?`<br>`BUS<b>:CPARallel:FRAMe<n>:STOP?` | Q | suffix `<b>` 1..2, `<n>` *<br>returns `<StopTime>` unit s | Returns the end time of the specified frame.<br>Bench: Only reliable when BUS<b>:TYPE matches this protocol. FW 03.000 is inconsistent: I2C queries on an SSPI bus give -200 'Execution error' and no response, SPI queries on a UART bus return empty data with no error. Check BUS<b>:TYPE? first.<br>Bench: Frame numbers <n> start at 1; frame count from ...:FCOunt?. Batched field reads run at about 180-230 frames/s (5-6 fields per frame). | 545 |

### 6.11 Signal generation (B6)

#### Basic settings of the function generator (16.13.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `WGENerator:FUNCtion` | S/Q | `<Function>` `DC` · `SINusoid` · `SQUare` · `PULSe` · `TRIangle` · `RAMP` · `SINC` · `ARBitrary` · `EXPonential` | Selects the function to be generated. | 546 |
| `WGENerator:VOLTage` | S/Q | `<Amplitude>` range 6.0000E-02 to 6.00000E+00; unit Vpp; *RST **5.0000E-01** | Defines the amplitude value (peak-to-peak value) of the selected generator function. | 546 |
| `WGENerator:VOLTage:OFFSet` | S/Q | `<Offset>` range -3.00000E+00 to 3.00000E+00; unit V; *RST **0.00E+00** | Sets the DC offset of the selected generator function. | 546 |
| `WGENerator:FREQuency` | S/Q | `<Frequency>` range Depends on the selected function. For details, refer to the datasheet.; *RST **10 kHz** | Defines the frequency. | 546 |
| `WGENerator:FUNCtion:PULSe:DCYCle` | S/Q | `<DutyCycle>` range 1.000E+01 to 9.000E+01; unit %; *RST **2.500E+01** | Defines the duty cycle value of the generator function pulse. | 547 |
| `WGENerator:TRIangle:SYMMetry` | S/Q | `<Symmetry>` range 1 to 99; unit %; *RST **50** | Sets the symmetry for the triangle waveform. | 547 |
| `WGENerator:FUNCtion:PULSe:ETIMe` | S/Q | `<EdgeTime>`  | Sets the pulse edge time. | 547 |
| `WGENerator:FUNCtion:EXPonential:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative` | Sets the polarity of the generator function exponential. | 547 |
| `WGENerator:FUNCtion:RAMP:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative`; *RST **NEG** | Sets the polarity of the generator function ramp. | 547 |
| `WGENerator:NOISe:ABSolute` | S/Q | `<AbsoluteNoise>`  | Sets the noise of the waveform in volts. | 548 |
| `WGENerator:NOISe:RELative` | S/Q | `<RelativeNoise>`  | Sets the noise of the generated waveform in percentage of the amplitude. | 548 |
| `WGENerator:OUTPut:LOAD` | S/Q | `<Load>` `HIGHz` · `R50` | Select the user load, the load of the DUT at its connection.<br>Bench: Not a relay: the generator always has a 50 ohm source impedance. R50 tells it a 50 ohm load is connected, so the amplitude you set appears at the load; HIGHz assumes an open (1 Mohm) input. A Bode sweep leaves it at R50. | 548 |
| `WGENerator:OUTPut[:ENABle]` | S/Q | `<OutputEnable>` `ON` · `OFF` | Enables the function generator and outputs the waveform. | 548 |
| `WGENerator:ARBitrary:SOURce` | S/Q | `<Source>` `CH1` · `CH2` · `CH3` · `CH4`; *RST **CH1** | Selects the input channel for an arbitrary function to be generated. | 548 |
| `WGENerator:ARBitrary:RANGe:STARt` | S/Q | `<StartTime>` unit s | Sets the start time of the copied waveform part. | 549 |
| `WGENerator:ARBitrary:RANGe:STOP` | S/Q | `<StopTime>` unit s | Sets the end time of the copied waveform part. | 549 |
| `WGENerator:ARBitrary:UPDate` | E |  | Loads the waveform from the selected signal source (WGENerator:ARBitrary: SOURce). | 549 |
| `WGENerator:ARBitrary[:FILE]:NAME` | S/Q | `<FilePath>` *RST **"** | Sets the file path and the file for an arbitrary waveform to be loaded. | 549 |
| `WGENerator:ARBitrary[:FILE]:OPEN` | E |  | Loads the arbitrary waveform, that is selected with the WGENerator:ARBitrary[: FILE]:NAME command.<br>Bench: Only works when WGENerator:FUNCtion is already ARB; otherwise -200 Execution error. Load order: WGEN:FUNC ARB, WGEN:ARB:FILE:NAME "/INT/<file>.CSV", WGEN:ARB:FILE:OPEN. The file is the scope's waveform CSV format: header 'in s,C1 in V', then time,value rows (2000 points verified); upload it with MMEMory:DATA. | 549 |
| `WGENerator:ARBitrary:VISible` | S/Q | `<ArbWaveformVisible>ON | OFF` *RST **OFF** | Enables the display of the arbitrary waveform. | 550 |
| `WGENerator:BURSt:ITIMe` | S/Q | `<IdleTime>` range 28n to 17; unit s; *RST **100u** | Sets the idle time between two burst cycles. | 550 |
| `WGENerator:BURSt:NCYCle` | S/Q | `<NumberOfCycles>` range 1 to 1023; *RST **1** | Sets the number of times the generator outputs one cycle of the waveform per burst. | 550 |
| `WGENerator:BURSt:TRIGger[:MODE]` | S/Q | `<TriggerMode>` `CONTinuous` · `SINGle`; *RST **CONT** | Selects the trigger mode. | 550 |
| `WGENerator:BURSt:TRIGger:SINGle` | E |  | Outputs one burst if WGENerator:BURSt:TRIGger[:MODE] is set to SINGle. | 551 |
| `WGENerator:BURSt:PHASe` | S/Q | `<PhaseOffset>` range 0 to 360; unit Degree; *RST **0** | Sets the start phase of the burst. | 551 |
| `WGENerator:BURSt[:STATe]` | S/Q | `<Enable>` `ON` · `OFF`; *RST **OFF** | Enables or disables the burst. | 551 |
| `WGENerator:MODulation[:ENABle]` | S/Q | `<Enable>` `ON` · `OFF` | Activates or deactivates modulation of the function generator output. | 552 |
| `WGENerator:MODulation:FUNCtion` | S/Q | `<ModulationFunction>` `SINusoid` · `SQUare` · `TRIangle` · `RAMP` | Selects a function for the modulation.<br>Bench: The manual runs the parameter name and its values together (and spells SINusiod). SQUare with 100 % AM makes a hard-keyed carrier (bench-verified). | 552 |
| `WGENerator:MODulation:TYPE` | S/Q | `<ModulationType>` `AM` · `FM` · `ASK` · `FSK` | Sets the modulation type, which defines how the carrier signal is modified. | 552 |
| `WGENerator:MODulation:AM:FREQuency` | S/Q | `<Frequency>`  | Sets the frequency of the modulating waveform for AM modulation. | 552 |
| `WGENerator:MODulation:AM:DEPTh` | S/Q | `<ModulationDepth>`  | Sets the modulation depth, the percentage of the amplitude range that is used for AM modulation. | 553 |
| `WGENerator:MODulation:FM:FREQuency` | S/Q | `<Frequency>`  | Sets the frequency of the modulating waveform for FM modulation. | 553 |
| `WGENerator:MODulation:FM:DEViation` | S/Q | `<Deviation>`  | Sets the frequency deviation, the maximum difference between the FM modulated sig- nal and the carrier signal. | 553 |
| `WGENerator:MODulation:ASK:FREQuency` | S/Q | `<Frequency>`  | Sets the frequency of the modulating waveform for ASK modulation. | 553 |
| `WGENerator:MODulation:ASK:DEPTh` | S/Q | `<ModulationDepth>`  | Sets the modulation depth, the percentage of the amplitude range that is used for ASK modulation. | 553 |
| `WGENerator:MODulation:FSK:HFREquency` | S/Q | `<HoppingFrequency>` The range depends on the signal type: Sinus: 100mHz - 25MHz… | Sets the second frequency of the FSK-modulated signal. | 554 |
| `WGENerator:MODulation:FSK:RATE` | S/Q | `<Rate>` range 0.1 to 1E6; unit Hz | Sets the rate at which signal switches between the carrier frequency and the hopping frequency. | 554 |
| `WGENerator:MODulation:RAMP:POLarity` | S/Q | `<Polarity>` `POSitive` · `NEGative` | Sets the polarity for the ramp function for a modulation waveform. | 554 |
| `WGENerator:SWEep:FEND` | S/Q | `<StopFrequency>`  | Sets the stop frequency of the sweep signal. | 554 |
| `WGENerator:SWEep:FSTart` | S/Q | `<StartFrequency>`  | Sets the start frequency of the sweep signal. | 554 |
| `WGENerator:SWEep:TIME` | S/Q | `<SweepTime>`  | Sets the duration of the sweep. | 555 |
| `WGENerator:SWEep:TYPE` | S/Q | `<SweepType>` `LINear` · `LOGarithmic` · `TRIangle` | Sets the type of the sweep, a linear, logarithmic or triangle-shaped change of the fre- quency. | 555 |
| `WGENerator:SWEep[:ENABle]` | S/Q | `<SweepEnable>` `ON` · `OFF` | Enables or disables sweeping. | 555 |

#### General settings (16.13.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `PGENerator:FUNCtion` | S/Q | `<PatternFunction>` `SQUarewave` · `COUNter` · `ARBitrary` · `SPI` · `I2C` · `UART` · `CAN` · `LIN` · `MANual` · `I2S` · `TDM`; *RST **SQUarewave** | Selects the pattern generator function.<br>Bench: Serial patterns are fixed demo traffic, levels about 1 V (set the logic threshold to USER 0.5 V). I2C: P0 SCL, P1 SDA; writes '*idn?' to address 0x20 and reads 'Rohde&Schwarz - RTB2004'. SPI: P0 clock, P1 MOSI, P2 MISO, P3 CS (active low); MOSI 'Rohde&Schwarz' and 'RTB2004', MISO echoes one byte later. UART: P0 Tx, 115200 Bd, idle high, '*idn?\n'; decodes cleanest as 9 bits, no parity, 1 stop bit.<br>Bench: COUNter drives a 4-bit up/down counter on P0..P3 at PGENerator:PATTern:COUNter:FREQuency. The serial data rate is not settable by remote command on FW 03.000. | 556 |
| `PGENerator:PATTern:STATe` | S/Q | `<State>` `ON` · `OFF`; *RST **ON** | Activates or deactivates the pattern output. | 556 |
| `PGENerator:PATTern:TRIGger:EXTern:SLOPe` | S/Q | `<ExternSlope>` `POSitive` · `NEGative` · `EITHer`; *RST **POSitive** | Defines the slope of the external arbitrary pattern trigger. | 557 |
| `PGENerator:PATTern:SQUarewave:POLarity` | S/Q | `<Polarity>` `NORMal` · `INVerted`; *RST **NORM** | Defines the polarity of the pattern generator square wave function. | 557 |
| `PGENerator:PATTern:SQUarewave:DCYCle` | S/Q | `<DutyCycle>` range 1.00E+00 to 9.900E+01; unit %; *RST **5.000E+01** | Sets the duty cycle of the square wave function. | 557 |
| `PGENerator:PATTern:PERiod` | S/Q | `<PatternPeriod>` range MIN 1 Sample * 20ns = 20ns to MAX 2048 Sam- ples * 42s = 10416s (approx. 2.89h); unit s; *RST **2.000E-06** | Defines the period of the pattern generator function. | 557 |
| `PGENerator:PATTern:FREQuency` | S/Q | `<PatternFrequency>` unit Hz | Defines the frequency (period) value of the pattern generator function. | 558 |
| `PGENerator:PATTern:COUNter:FREQuency` | S/Q | `<Period>` range 2.380952425301E-02 to 2.500000000000E+07; unit Hz; *RST **1.000000000000E+05** | Defines the frequency value of the pattern generator counter function. | 558 |
| `PGENerator:PATTern:COUNter:DIRection` | S/Q | `<CountDirection>` `UPWard` · `DOWNward`; *RST **UPW** | Sets the pattern generator counter direction. | 558 |
| `PGENerator:PATTern:TRIGger:MODE` | S/Q | `<TriggerMode>` `CONTinuous` · `SINGle`; *RST **CONT** | Defines the arbitrary trigger mode of the pattern generator function. | 559 |
| `PGENerator:PATTern:ARBitrary:DATA[:SET]` | S/Q | `<ArbitraryData>` List of Values | Defines the arbitrary pattern. | 559 |
| `PGENerator:PATTern:ARBitrary:DATA:LENGth` | S/Q | `<PatternLength>` range 1 to 2048; *RST **1** | Defines the arbitrary pattern length. | 559 |
| `PGENerator:PATTern:ARBitrary:DATA:APPend` | S | `<AppendData>` List of Values | Defines the arbitrary pattern. | 560 |
| `PGENerator:PATTern:ARBitrary:DATA:APPend:BOR` | S | `<AppendData>` List of Values | From index = n, data will be integrated in existing pattern via OR combination. | 560 |
| `PGENerator:PATTern:ARBitrary:DATA:APPend:BAND` | S | `<AppendData>` List of Values | From index = n, data will be integrated in existing pattern via AND combination. | 560 |
| `PGENerator:PATTern:ARBitrary:DATA:APPend:INDex` | S/Q | `<AppendIndex>` Numeric value | Defines the index of the arbitrary pattern. | 560 |
| `PGENerator:PATTern:TRIGger:SINGle` | E |  | Manual output of a pattern (single trigger). | 561 |
| `PGENerator:PATTern:STIMe` | S/Q | `<SampleTime>` range 2.000E-08 to 4.200E+01; unit s; *RST **2.000E-08** | Sets the time at which each sample is applied for the pattern generator function. | 561 |
| `PGENerator:PATTern:ITIMe` | S/Q | `<IdleTime>` range 2.000E-08 to 4.200000000000E+01; unit s; *RST **2.5000000000E-01** | Defines the idle time of the pattern generator function. | 561 |
| `PGENerator:PATTern:BURSt:STATe` | S/Q | `<BurstState>` `ON` · `OFF`; *RST **OFF** | Turns the BURST function on or off. | 561 |
| `PGENerator:PATTern:BURSt:NCYCle` | S/Q | `<PatternCycles>` range 1 to 4096; *RST **1** | Defines the BURST pattern cycles. | 561 |
| `PGENerator:MANual:STATe<s>` | S/Q | suffix `<s>` 0..3<br>`<State>` `ON` · `OFF`; *RST **OFF** | Selects the pins S0 to S3 manually and sets their states to high (H) or low (L). | 562 |

### 6.12 Status reporting

#### STATus:OPERation register (16.14.1)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `STATus:OPERation:CONDition?` | Q | returns `<Condition>` range 1 to 65535 | Returns the of the CONDition part of the operational status register. | 562 |
| `STATus:OPERation:ENABle` | S/Q | `<Enable>` range 1 to 65535 | Controls the ENABle part of the STATus:OPERation register. | 563 |
| `STATus:OPERation:NTRansition` | S/Q | `<NegativeTransition>` range 1 to 65535 |  | 563 |
| `STATus:OPERation:PTRansition` | S/Q | `<PositiveTransition>` range 1 to 65535 |  | 563 |
| `STATus:OPERation[:EVENt]?` | Q | returns `<Event>` range 1 to 65535 |  | 563 |

#### STATus:QUEStionable registers (16.14.2)

| Command | Type | Parameters, values, *RST | Description | p. |
|---|---|---|---|---:|
| `STATus:PRESet` | E |  | Resets all STATUS:QUESTIONALBLE registers. | 565 |
| `STATus:QUEStionable:CONDition?`<br>`STATus:QUEStionable:COVerload:CONDition?`<br>`STATus:QUEStionable:ADCState:CONDition?`<br>`STATus:QUEStionable:LIMit:CONDition?`<br>`STATus:QUEStionable:MASK:CONDition?` | Q | returns `<Condition>` range 0 to 65535 | Returns the contents of the CONDition part of the status register to check for question- able instrument or measurement states. | 565 |
| `STATus:QUEStionable:ENABle`<br>`STATus:QUEStionable:COVerload:ENABle`<br>`STATus:QUEStionable:ADCState:ENABle`<br>`STATus:QUEStionable:LIMit:ENABle`<br>`STATus:QUEStionable:MASK:ENABle` | S/Q | `<Enable>` range 0 to 65535 | Sets the ENABle part that allows true conditions in the EVENt part to be reported in the summary bit. | 565 |
| `STATus:QUEStionable[:EVENt]?`<br>`STATus:QUEStionable:COVerload[:EVENt]?`<br>`STATus:QUEStionable:ADCState[:EVENt]?`<br>`STATus:QUEStionable:LIMit[:EVENt]?`<br>`STATus:QUEStionable:MASK[:EVENt]?` | Q | returns `<Event>` range 0 to 65535 | Returns the contents of the EVENt part of the status register to check whether an event has occurred since the last reading. | 566 |
| `STATus:QUEStionable:NTRansition`<br>`STATus:QUEStionable:COVerload:NTRansition`<br>`STATus:QUEStionable:ADCState:NTRansition`<br>`STATus:QUEStionable:LIMit:NTRansition`<br>`STATus:QUEStionable:MASK:NTRansition` | S/Q | `<NegativeTransition>` range 0 to 65535 | Sets the negative transition filter. | 566 |
| `STATus:QUEStionable:PTRansition`<br>`STATus:QUEStionable:COVerload:PTRansition`<br>`STATus:QUEStionable:ADCState:PTRansition`<br>`STATus:QUEStionable:LIMit:PTRansition`<br>`STATus:QUEStionable:MASK:PTRansition` | S/Q | `<PositiveTransition>` range 0 to 65535 | Sets the positive transition filter. | 567 |

## Appendix A. Undocumented firmware commands

These 408 command families are reported by `SYSTem:TREE?` on FW 03.000 but are not in the manual. Numeric suffixes are generalized (`CHAN<a>` stands for CHAN, CHAN2, CHAN3, CHAN4) with the range the firmware listed. Only the short form is known. Parameters are unknown and not validated; query first, and test on a scope you can reset.

| Root | Commands |
|---|---|
| `ACQ` | `ACQ:ADC:RES` · `ACQ:MODE` · `ACQ:REAL` · `ACQ:SEGM:MAX` · `ACQ:SRAT:ZOOM` · `ACQ:WRAT:MAX` · `ACQ:ZRAT` |
| `BPL` | `BPL:AMPL:DATA` · `BPL:EXP:DATA` · `BPL:POIN:LIN` · `BPL:STEP` · `BPL:STEP:MODE` |
| `BUS` | `BUS:HIST:ATIC` · `BUS:HIST:ATIC:ALL` · `BUS:HIST:DTIC` · `BUS:HIST:DTIC:ALL` · `BUS:HIST:TCL` · `BUS:HIST:TINC` · `BUS:HIST:TMOD` · `BUS:I2C:AMOD` · `BUS<a>:HIST:ATIC` (<a> 2..2) · `BUS<a>:HIST:ATIC:ALL` (<a> 2..2) · `BUS<a>:HIST:DTIC` (<a> 2..2) · `BUS<a>:HIST:DTIC:ALL` (<a> 2..2) · `BUS<a>:HIST:TCL` (<a> 2..2) · `BUS<a>:HIST:TINC` (<a> 2..2) · `BUS<a>:HIST:TMOD` (<a> 2..2) · `BUS<a>:I2C:AMOD` (<a> 2..2) |
| `CAL` | `CAL:LOG` |
| `CALC` | `CALC:MATH:ARIT` · `CALC:MATH:AVER:COMP` · `CALC:MATH:AVER:COUN` · `CALC:MATH:AVER:RES` · `CALC:MATH:DATA:ENV` · `CALC:MATH:DATA:ENV:HEAD` · `CALC:MATH:DATA:ENV:POIN` · `CALC:MATH:DATA:ENV:XINC` · `CALC:MATH:DATA:ENV:XOR` · `CALC:MATH:DATA:ENV:YINC` · `CALC:MATH:DATA:ENV:YOR` · `CALC:MATH:DATA:ENV:YRES` · `CALC:MATH:FFT:AVER:COMP` · `CALC:MATH:FFT:AVER:COUN` · `CALC:MATH:FFT:AVER:RES` · `CALC:MATH:FFT:BAND` · `CALC:MATH:FFT:BAND:ADJ` · `CALC:MATH:FFT:BAND:AUTO` · `CALC:MATH:FFT:BAND:RAT` · `CALC:MATH:FFT:BAND:RES` · `CALC:MATH:FFT:BAND:VAL` · `CALC:MATH:FFT:CFR` · `CALC:MATH:FFT:FULL` · `CALC:MATH:FFT:MAGN:SCAL` · `CALC:MATH:FFT:SPAN` · `CALC:MATH:FFT:SRAT` · `CALC:MATH:FFT:STAR` · `CALC:MATH:FFT:STOP` · `CALC:MATH:FFT:TIME:POS` · `CALC:MATH:FFT:TIME:RANG` · `CALC:MATH:FFT:WIND:TYPE` · `CALC:MATH:HIST:ATIC` · `CALC:MATH:HIST:ATIC:ALL` · `CALC:MATH:HIST:DTIC` · `CALC:MATH:HIST:DTIC:ALL` · `CALC:MATH:HIST:EXP:NAME` · `CALC:MATH:HIST:EXP:SAVE` · `CALC:MATH:HIST:TCL` · `CALC:MATH:HIST:TINC` · `CALC:MATH:HIST:TMOD` · `CALC:MATH:TRAC:DPUL` · `CALC:MATH:TRAC:DPUL:ENAB` · `CALC:MATH:TRAC:THR:LOW` · `CALC:MATH<a>:ARIT` (<a> 2..5) · `CALC:MATH<a>:AVER:COMP` (<a> 2..5) · `CALC:MATH<a>:AVER:COUN` (<a> 2..5) · `CALC:MATH<a>:AVER:RES` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:HEAD` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:POIN` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:XINC` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:XOR` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:YINC` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:YOR` (<a> 2..5) · `CALC:MATH<a>:DATA:ENV:YRES` (<a> 2..5) · `CALC:MATH<a>:FFT:AVER:COMP` (<a> 2..5) · `CALC:MATH<a>:FFT:AVER:COUN` (<a> 2..5) · `CALC:MATH<a>:FFT:AVER:RES` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND:ADJ` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND:AUTO` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND:RAT` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND:RES` (<a> 2..5) · `CALC:MATH<a>:FFT:BAND:VAL` (<a> 2..5) · `CALC:MATH<a>:FFT:CFR` (<a> 2..5) · `CALC:MATH<a>:FFT:FULL` (<a> 2..5) · `CALC:MATH<a>:FFT:MAGN:SCAL` (<a> 2..5) · `CALC:MATH<a>:FFT:SPAN` (<a> 2..5) · `CALC:MATH<a>:FFT:SRAT` (<a> 2..5) · `CALC:MATH<a>:FFT:STAR` (<a> 2..5) · `CALC:MATH<a>:FFT:STOP` (<a> 2..5) · `CALC:MATH<a>:FFT:TIME:POS` (<a> 2..5) · `CALC:MATH<a>:FFT:TIME:RANG` (<a> 2..5) · `CALC:MATH<a>:FFT:WIND:TYPE` (<a> 2..5) · `CALC:MATH<a>:HIST:ATIC` (<a> 2..5) · `CALC:MATH<a>:HIST:ATIC:ALL` (<a> 2..5) · `CALC:MATH<a>:HIST:DTIC` (<a> 2..5) · `CALC:MATH<a>:HIST:DTIC:ALL` (<a> 2..5) · `CALC:MATH<a>:HIST:EXP:NAME` (<a> 2..5) · `CALC:MATH<a>:HIST:EXP:SAVE` (<a> 2..5) · `CALC:MATH<a>:HIST:TCL` (<a> 2..5) · `CALC:MATH<a>:HIST:TINC` (<a> 2..5) · `CALC:MATH<a>:HIST:TMOD` (<a> 2..5) · `CALC:MATH<a>:TRAC:DPUL` (<a> 2..5) · `CALC:MATH<a>:TRAC:DPUL:ENAB` (<a> 2..5) · `CALC:MATH<a>:TRAC:THR:LOW` (<a> 2..5) |
| `CHAN` | `CHAN:HIST:ATIC` · `CHAN:HIST:ATIC:ALL` · `CHAN:HIST:CONT` · `CHAN:HIST:DTIC` · `CHAN:HIST:DTIC:ALL` · `CHAN:HIST:TCL` · `CHAN:HIST:TINC` · `CHAN:HIST:TMOD` · `CHAN:OVER` · `CHAN<a>:HIST:ATIC` (<a> 2..4) · `CHAN<a>:HIST:ATIC:ALL` (<a> 2..4) · `CHAN<a>:HIST:CONT` (<a> 2..4) · `CHAN<a>:HIST:DTIC` (<a> 2..4) · `CHAN<a>:HIST:DTIC:ALL` (<a> 2..4) · `CHAN<a>:HIST:TCL` (<a> 2..4) · `CHAN<a>:HIST:TINC` (<a> 2..4) · `CHAN<a>:HIST:TMOD` (<a> 2..4) · `CHAN<a>:OVER` (<a> 2..4) |
| `CURS` | `CURS:RES` · `CURS:SPX` · `CURS:SSCR` · `CURS:X1P:INCR` · `CURS:X2P:INCR` · `CURS:X3P` · `CURS:X3P:INCR` · `CURS:X4P` · `CURS:X4P:INCR` · `CURS:Y1P:INCR` · `CURS:Y2P:INCR` · `CURS:Y3P` · `CURS:Y3P:INCR` · `CURS:Y4P` · `CURS:Y4P:INCR` |
| `DIAG` | `DIAG:SERV:HCLT` · `DIAG:SERV:HWID` · `DIAG:SERV:SNUM` · `DIAG:SERV:SWID` |
| `DIG` | `DIG<a>:AOFF` (<a> 0..15) · `DIG<a>:AON` (<a> 0..15) · `DIG<a>:HIST:ATIC` (<a> 0..15) · `DIG<a>:HIST:ATIC:ALL` (<a> 0..15) · `DIG<a>:HIST:DTIC` (<a> 0..15) · `DIG<a>:HIST:DTIC:ALL` (<a> 0..15) · `DIG<a>:HIST:TCL` (<a> 0..15) · `DIG<a>:HIST:TINC` (<a> 0..15) · `DIG<a>:HIST:TMOD` (<a> 0..15) · `DIG<a>:PROB<b>:ENAB` (<a> 0..15, <b> 2..2) · `DIG<a>:TYPE` (<a> 0..15) |
| `DISP` | `DISP:CBAR:RES` · `DISP:CBAR:RES:POS` · `DISP:CBAR:SPEC` · `DISP:CBAR:SPEC:POS` · `DISP:DIAL:HELP` · `DISP:DIAL:TRAN` · `DISP:LANG:ADD` · `DISP:LANG:CAT` · `DISP:LANG:REM` · `DISP:PROG:TYPE` · `DISP:PROG:VAL` · `DISP:RES` · `DISP:RES:TAB` · `DISP:TOOL:DEF` · `DISP:XY:ZMOD` · `DISP:XY:ZSO` · `DISP:XY:ZTHR` |
| `DVM` | `DVM:FORM` · `DVM:POS` · `DVM:RES:AVG` · `DVM:RES:LTA` · `DVM:RES:LTS` · `DVM:RES:MAX` · `DVM:RES:MIN` · `DVM:RES:NPE` · `DVM:RES:PPE` · `DVM:RES:RES` · `DVM:RES:STDD` · `DVM:RES:WFMC` · `DVM:STAT:RES` · `DVM:STAT:WEIG` · `DVM<a>:FORM` (<a> 2..4) · `DVM<a>:POS` (<a> 2..4) · `DVM<a>:RES:AVG` (<a> 2..4) · `DVM<a>:RES:LTA` (<a> 2..4) · `DVM<a>:RES:LTS` (<a> 2..4) · `DVM<a>:RES:MAX` (<a> 2..4) · `DVM<a>:RES:MIN` (<a> 2..4) · `DVM<a>:RES:NPE` (<a> 2..4) · `DVM<a>:RES:PPE` (<a> 2..4) · `DVM<a>:RES:RES` (<a> 2..4) · `DVM<a>:RES:STDD` (<a> 2..4) · `DVM<a>:RES:WFMC` (<a> 2..4) · `DVM<a>:STAT:RES` (<a> 2..4) · `DVM<a>:STAT:WEIG` (<a> 2..4) |
| `EXP` | `EXP:MEAS<a>:STAT:ALL:NAME` (<a> 2..8) · `EXP:MEAS<a>:STAT:ALL:SAVE` (<a> 2..8) · `EXP:WAV:DATA` · `EXP:WAV:HIST:ALL` · `EXP:WAV:HIST:CURR` · `EXP:WAV:HIST:REM` · `EXP:WAV:HIST:STAR` · `EXP:WAV:HIST:STAT` · `EXP:WAV:HIST:STOP` |
| `LOG` | `LOG:AOFF` · `LOG:AON` · `LOG:ARIT` · `LOG:HIST:ATIC` · `LOG:HIST:ATIC:ALL` · `LOG:HIST:DTIC` · `LOG:HIST:DTIC:ALL` · `LOG:HIST:TCL` · `LOG:HIST:TINC` · `LOG:HIST:TMOD` · `LOG<a>:AOFF` (<a> 2..2) · `LOG<a>:AON` (<a> 2..2) · `LOG<a>:ARIT` (<a> 2..2) · `LOG<a>:HIST:ATIC` (<a> 2..2) · `LOG<a>:HIST:ATIC:ALL` (<a> 2..2) · `LOG<a>:HIST:DTIC` (<a> 2..2) · `LOG<a>:HIST:DTIC:ALL` (<a> 2..2) · `LOG<a>:HIST:TCL` (<a> 2..2) · `LOG<a>:HIST:TINC` (<a> 2..2) · `LOG<a>:HIST:TMOD` (<a> 2..2) |
| `LRN` | `*LRN` |
| `MASK` | `MASK:ACT:PULS:EVEN:COUN` · `MASK:ACT:PULS:PLEN` · `MASK:ACT:PULS:POL` · `MASK:ACT:SCRS:EVEN:COUN` · `MASK:ACT:SOUN:EVEN:COUN` · `MASK:ACT:WFMS:EVEN:COUN` · `MASK:TTIM` |
| `MEAS` | `MEAS:CAT` · `MEAS:GATE:ENAB` · `MEAS:RES:BUFF:AVG` · `MEAS:RES:BUFF:NPE` · `MEAS:RES:BUFF:PPE` · `MEAS:RES:BUFF:STDD` · `MEAS<a>:CAT` (<a> 2..8) · `MEAS<a>:GATE:ENAB` (<a> 2..8) · `MEAS<a>:RES:BUFF:AVG` (<a> 2..8) · `MEAS<a>:RES:BUFF:NPE` (<a> 2..8) · `MEAS<a>:RES:BUFF:PPE` (<a> 2..8) · `MEAS<a>:RES:BUFF:STDD` (<a> 2..8) |
| `MMEM` | `MMEM:COMP` · `MMEM:EXIS` |
| `PROB` | `PROB:ID:BUIL` · `PROB:ID:PART` · `PROB:ID:PRD` · `PROB:ID:SRN` · `PROB:ID:SWV` · `PROB:SET:ADV:AUD` · `PROB:SET:ADV:FILT` · `PROB:SET:ADV:RANG` · `PROB:SET:ATT` · `PROB:SET:ATT:AUTO` · `PROB:SET:BAND` · `PROB:SET:CAP` · `PROB:SET:CMOF` · `PROB:SET:DCOF` · `PROB:SET:GAI` · `PROB:SET:GAI:AUTO` · `PROB:SET:GAI:MAN` · `PROB:SET:GAI:UNIT` · `PROB:SET:IMP` · `PROB:SET:MODE` · `PROB:SET:NAME` · `PROB:SET:OFFS` · `PROB:SET:PRM` · `PROB:SET:TYPE` · `PROB:SET:UOFF` · `PROB:SET:ZAXV` · `PROB<a>:ID:BUIL` (<a> 2..4) · `PROB<a>:ID:PART` (<a> 2..4) · `PROB<a>:ID:PRD` (<a> 2..4) · `PROB<a>:ID:SRN` (<a> 2..4) · `PROB<a>:ID:SWV` (<a> 2..4) · `PROB<a>:SET:ADV:AUD` (<a> 2..4) · `PROB<a>:SET:ADV:FILT` (<a> 2..4) · `PROB<a>:SET:ADV:RANG` (<a> 2..4) · `PROB<a>:SET:ATT` (<a> 2..4) · `PROB<a>:SET:ATT:AUTO` (<a> 2..4) · `PROB<a>:SET:BAND` (<a> 2..4) · `PROB<a>:SET:CAP` (<a> 2..4) · `PROB<a>:SET:CMOF` (<a> 2..4) · `PROB<a>:SET:DCOF` (<a> 2..4) · `PROB<a>:SET:GAI` (<a> 2..4) · `PROB<a>:SET:GAI:AUTO` (<a> 2..4) · `PROB<a>:SET:GAI:MAN` (<a> 2..4) · `PROB<a>:SET:GAI:UNIT` (<a> 2..4) · `PROB<a>:SET:IMP` (<a> 2..4) · `PROB<a>:SET:MODE` (<a> 2..4) · `PROB<a>:SET:NAME` (<a> 2..4) · `PROB<a>:SET:OFFS` (<a> 2..4) · `PROB<a>:SET:PRM` (<a> 2..4) · `PROB<a>:SET:TYPE` (<a> 2..4) · `PROB<a>:SET:UOFF` (<a> 2..4) · `PROB<a>:SET:ZAXV` (<a> 2..4) |
| `REFC` | `REFC:ASC` · `REFC:DATA:POIN` · `REFC<a>:ASC` (<a> 2..4) · `REFC<a>:DATA:POIN` (<a> 2..4) |
| `SEAR` | `SEAR:DBG` |
| `SOUR` | `SOUR:FREQ` · `SOUR:FUNC` · `SOUR:FUNC:SHAP` |
| `SPEC` | `SPEC:DIAG:COL:MAGN:MODE` · `SPEC:DIAG:COL:MAX` · `SPEC:DIAG:COL:MAX:LEV` · `SPEC:DIAG:COL:MIN` · `SPEC:DIAG:COL:MIN:LEV` · `SPEC:DIAG:COL:SCH:FDOM` · `SPEC:DIAG:COL:SCH:SPEC` · `SPEC:DIAG:FDOM` · `SPEC:DIAG:FDOM:ENAB` · `SPEC:DIAG:SPEC` · `SPEC:DIAG:SPEC:ENAB` · `SPEC:DIAG:TDOM` · `SPEC:DIAG:TDOM:ENAB` · `SPEC:FREQ:BAND:AUTO` · `SPEC:FREQ:BAND:RES:AUTO` · `SPEC:HIST:ATIC` · `SPEC:HIST:ATIC:ALL` · `SPEC:HIST:CONT` · `SPEC:HIST:CONT:ENAB` · `SPEC:HIST:DTIC` · `SPEC:HIST:DTIC:ALL` · `SPEC:HIST:TCL` · `SPEC:HIST:TINC` · `SPEC:HIST:TMOD` · `SPEC:HIST:TTAB` · `SPEC:HIST:TTAB:ENAB` · `SPEC:MODE` · `SPEC:SPEC:RES` · `SPEC:SPEC:SCAL` |
| `STAT` | `STAT:QUES:MARG` · `STAT:QUES:MARG:COND` · `STAT:QUES:MARG:ENAB` · `STAT:QUES:MARG:EVEN` · `STAT:QUES:MARG:NTR` · `STAT:QUES:MARG:PTR` · `STAT:QUES:TEMP` · `STAT:QUES:TEMP:COND` · `STAT:QUES:TEMP:ENAB` · `STAT:QUES:TEMP:EVEN` · `STAT:QUES:TEMP:NTR` · `STAT:QUES:TEMP:PTR` |
| `SYST` | `SYST:CAL` · `SYST:CAL:ERR` · `SYST:CAL:STAT` · `SYST:CIE` · `SYST:COMM:INT:ETH:SAVE` · `SYST:COMM:INT:ETH:SUB` · `SYST:DEV` · `SYST:ELIS` · `SYST:HARD` · `SYST:HIDK` · `SYST:HIDM` · `SYST:HIDT` · `SYST:SNUM` · `SYST:SOFT` · `SYST:TEMP:ALL` · `SYST:TEMP:HIS` · `SYST:TEMP:HIS<a>` (<a> 2..4) · `SYST:TEMP:SENS` · `SYST:TEMP:SENS<a>` (<a> 2..4) · `SYST:TEMP:VAL` · `SYST:TEMP:VAL<a>` (<a> 2..4) |
| `TIM` | `TIM:ROLL:ENAB` |
| `TRAC` | `TRAC` · `TRAC:BORD` · `TRAC:DATA` · `TRAC:EXP:NAME` · `TRAC:EXP:SAVE` · `TRAC:FORM` · `TRAC:POIN` · `TRAC:SOUR` · `TRAC:SOUR:CAT` · `TRAC:TYPE` · `TRAC:XINC` · `TRAC:XOR` · `TRAC:XREF` · `TRAC:YINC` · `TRAC:YOR` · `TRAC:YREF` |
| `TRIG` | `TRIG:A:CAN:DLEN` · `TRIG:OUT:FREQ` |
| `TST` | `*TST` · `TST:ACL` · `TST:CLE` · `TST:NEXT` · `TST:PREV` · `TST:SET` |
| `WGEN` | `WGEN:OUTP:DEST` · `WGEN:SWE:FEN` |

## Credits, license and commercial support

Generated from the [`rs-scope-mcp`](https://github.com/sbrunner-atx/rs-scope-mcp) command registry by `scripts/gen_api_docs.py`. Command names, parameters and defaults are from the R&S RTB2 user manual (chapter 16) and from the instrument's firmware; behavior notes are from bench tests. Rohde & Schwarz is not responsible for this document.

**Free for hobbyists.** Personal use, amateur radio, education and nonprofit use of `rs-scope-mcp` and this reference are free (PolyForm Noncommercial License 1.0.0).

**Commercial use needs a license.** Any use by or for a business, including internal use on a company test bench, needs a commercial license. Support for more instruments, custom skills and integrations, and support plans are available too. Buy through **Westlake Professionals**: [westlakeprofessionals.com](https://westlakeprofessionals.com).
