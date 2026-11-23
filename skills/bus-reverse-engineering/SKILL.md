---
name: bus-reverse-engineering
description: >
  Find out what an unknown board is saying, the way a hardware reverse engineer works: map which
  pins are active, measure their levels, classify clock, data and select lines, identify the
  protocol (I2C, SPI, UART including inverted serial, CAN, LIN, parallel, PWM) and its rate,
  confirm with the scope's decoder, then build a census of addresses, commands and messages across
  many captures. Use when the operator has an undocumented header, test points, a module to
  sniff, or asks "what is on these pins". Read scope-operating first. Identification verified on an
  RTB2004 against known I2C, SPI, UART and servo signals.
---

# Reverse engineering a bus with the RTB2

## 0. Before connecting anything

- **Levels first.** Put one analog channel (10x probe) on each candidate pin and the ground clip
  on the board's ground. `capture(sources=["CH1"])` gives min/max: 0 to 3.3 V, 0 to 5 V, 0 to 1.8 V,
  or something else (RS-232 swings to +/-5..12 V and must not go on the logic probe; RS-485 and CAN
  are differential pairs). Do not exceed the logic probe's input rating.
- **Threshold.** Set the logic threshold to half the high level:
  `scpi(["LOG1:STAT ON", "LOG1:THR USER", "LOG1:THR:UDL 1.65"])` for 3.3 V logic
  (`LOG1:THR TTL`, `CMOS` presets also exist).
- **Ground.** The logic probe's ground goes to the board's ground. Never to a signal.

## 1. Survey: which lines are alive

1. Long window, free-running: `scpi(["TIM:SCAL 1e-2", "TRIG:A:MODE AUTO"])`.
2. `capture(sources=["POD1"], note="survey")`. The summary lists `active` lines with edge counts,
   `min_pulse_s`, `median_pulse_s`, and `idle_levels` for quiet ones.
3. Quiet lines may be quiet only now: ask the operator to make the device do something (power-up,
   press a button, send a command) and capture again with `acquire="single"` and a trigger on the
   busiest line (`TRIG:A:SOUR D<n>`, `TRIG:A:MODE NORM`).

## 2. Identify

`data(action="identify", capture_id=..., source="D0-D7")` classifies the active lines and returns a
`guess`, a `confidence` and a `decoder_setup`:

| It sees | Guess |
|---|---|
| One line, pulses at multiples of one bit time | UART with measured and nearest standard baud; idle low means inverted (SBUS style) |
| A bursting clock plus one data line, both idling high | I2C (SCL, SDA), with clock frequency |
| A bursting clock, data lines, and a line that holds one level while the clock runs | SPI with clock, chip select (and polarity), MOSI/MISO candidates |
| One steady periodic line | Clock or square wave (duty near 50 %) or PWM, flagged servo/ESC-style for 1-2 ms pulses at 40-500 Hz |

It is a heuristic. The next step decides.

## 3. Confirm with the decoder

Set up the decoder from `decoder_setup`, trigger on the start of a transfer, and capture the bus:

- UART: `BUS1:TYPE UART`, `BUS1:UART:DATA:SOUR D<n>`, `BUS1:UART:BAUD <baud>`,
  `BUS1:UART:POL IDLH|IDLL`, `BUS1:UART:SSIZ 8`, `BUS1:UART:PAR NONE`, `BUS1:UART:SBIT B1`;
  trigger falling edge (or rising for inverted) on that line.
- I2C: `BUS1:TYPE I2C`, `BUS1:I2C:CLOC:SOUR D<scl>`, `BUS1:I2C:DATA:SOUR D<sda>`; trigger falling
  edge on SCL.
- SPI: `BUS1:TYPE SPI`, `BUS1:SPI:CLOC:SOUR`, `BUS1:SPI:CS:SOUR`, `BUS1:SPI:CS:POL NEG|POS`,
  `BUS1:SPI:MOSI:SOUR`, `BUS1:SPI:MISO:SOUR`, `BUS1:SPI:SSIZ 8`; trigger on the CS edge that starts
  a frame.
- Then `scpi(["BUS1:STAT ON", "BUS1:FORM ASC"])` and `capture(sources=["BUS1","POD1"])`.

**Frames decode with status OK: confirmed.** If not, change one thing at a time and capture again:

| Symptom | Try |
|---|---|
| UART stop-bit or parity errors (`SPER`, `PRER`) | Parity EVEN/ODD, 2 stop bits, word size 7 or 9, then baud +/-3 % |
| UART garbage with correct timing | Polarity (inverted line), LSB/MSB |
| SPI bytes look shifted | Clock polarity, the other clock edge, MOSI and MISO swapped, MSB/LSB first, word size |
| I2C mostly NACK | Address scan of a powered-down device, or the lines swapped |
| `-200` on decode queries | `BUS1:TYPE` is not the protocol you are querying |

Bench note: the RTB2 pattern generator's UART decodes best as 115200 Bd, 9 bits, no parity, even
though identification correctly reports 115200 Bd idle high; trying framing variants is normal.

## 4. Census: what is being said

Harvest many transfers, then query the store instead of reading frames one by one:

- Several captures: repeat `capture(sources=["BUS1"])` while the operator exercises the device.
- Many transfers at once: segmented memory. Set the trigger, then
  `scpi(["ACQ:NSIN:COUN 50", "RUNS"], wait=true)` and `history(action="harvest", sources=["BUS1"])`.
  Keep the sample rate at least 10x the bit rate; segments have short records.
- Then SQL across everything in the session:
  - I2C devices: `select address, count(*) from frames where protocol='I2C' group by address`
    (7-bit address = `address >> 1`; the low bit is read (1) or write (0)).
  - First bytes of SPI frames (command opcodes):
    `select substr(data_hex,1,2) op, count(*) from frames where protocol in ('SPI','SSPI') group by op`
  - Errors: `data(action="frames", status="not_ok")`.
  - Text: `data(action="frames", contains="GPGGA")`.

## 5. Recognize common traffic

| Bus | Clue | Likely |
|---|---|---|
| I2C | Address 0x68 or 0x69 | IMU (MPU-6050/9250, ICM-20xxx, BMI) |
| I2C | 0x76 or 0x77 | Barometer (BMP/BME280, MS5611) |
| I2C | 0x1E, 0x0D, 0x0C | Magnetometer (HMC5883, QMC5883, AK8963) |
| I2C | 0x50..0x57 | EEPROM; 0x3C/0x3D: small OLED display |
| SPI | First bytes 0x9F, 0x03, 0x0B, 0x02, 0x06, 0x05 | SPI flash: read ID, read, fast read, program, write enable, status |
| UART | `$GP`, `$GN` text | NMEA GPS (9600, 38400 or 115200 Bd) |
| UART | Frames start with 0xFE or 0xFD | MAVLink v1 / v2 (57600 or 115200 Bd) |
| UART inverted, 100000 Bd, 8E2 | 25-byte frames starting 0x0F | SBUS (RC receiver) |
| UART 420000 Bd | Frames starting 0xC8 | CRSF / ELRS |
| UART | `AT`, `OK` text | Modem, Wi-Fi or Bluetooth module |
| Single line | 1-2 ms pulse at 50-400 Hz | Servo or ESC PWM |
| Single line, about 150-600 kbit/s, 16-bit bursts | Pulse widths about 37 % and 75 % of the bit | DShot ESC protocol |

## 6. Electrical clues

- **Open-drain vs push-pull:** on an analog channel, open-drain lines (I2C, some interrupts) rise
  slowly with an RC curve and fall fast; push-pull lines are symmetric. `measure(source="CH1",
  types=["RTIM","FTIM"])`.
- **Who drives:** a line whose low level differs slightly between two devices shows two "low"
  plateaus on an analog channel: two drivers share it.

## Report

A table of lines (pin or probe channel, level, role, protocol, rate), the devices or commands
found with counts, and anything abnormal (errors, NACKs, glitches) with capture ids. Say how each
finding was confirmed.
