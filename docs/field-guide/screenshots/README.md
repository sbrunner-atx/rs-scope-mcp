# Field Guide screenshots

Captured from an R&S RTB2004 (firmware 03.000) by `scripts/field_guide_shots.py`.
Wiring: Aux Out through a 50 ohm feed-through terminator into CH1; pattern generator P0..P3 into logic probe D0..D3.
Each PNG has a JSON sidecar with every command sent and the scope's own readouts.

| Screenshot | What it shows | Scope readouts |
|---|---|---|
| [01-loopback-sine.png](01-loopback-sine.png) | Generator loopback: 1 kHz sine with automatic measurements | `MEAS1:RES?` 9.997180E+02; `MEAS2:RES?` 1.030000E+00; `MEAS3:RES?` 3.441040E-01; `ACQ:SRAT?` 5.43E+07 |
| [02-square-edge.png](02-square-edge.png) | Edge quality: rise time and overshoot of a 100 kHz square wave | `MEAS1:RES?` 1.360000E-08; `MEAS2:RES?` 4.314000E+00; `MEAS3:RES?` 9.960000E-01 |
| [03-fft-harmonics.png](03-fft-harmonics.png) | FFT of a 10 kHz square wave: the odd harmonics | `SPEC:FREQ:STAR?` 1.072E+02; `SPEC:FREQ:STOP?` 2.001567E+05; `SPEC:FREQ:BAND?` 9.99850E+02 |
| [04-am-envelope.png](04-am-envelope.png) | AM: 100 kHz carrier, 1 kHz tone, 80 % depth (the envelope) | `MEAS1:RES?` 4.480000E-01; `MEAS2:RES?` 9.200000E-01 |
| [05-am-sidebands.png](05-am-sidebands.png) | AM in the frequency domain: carrier and the two sidebands at +/- 1 kHz | `SPEC:FREQ:CENT?` 1.0001760E+05; `SPEC:FREQ:SPAN?` 1.001358E+04; `SPEC:FREQ:BAND?` 5.0066E+01 |
| [06-uart-decode.png](06-uart-decode.png) | Serial decode: a UART byte stream with the bus table | `BUS1:UART:FCO?` 13; `BUS1:UART:FRAM1:STAT?` OK; `BUS1:UART:FRAM1:WORD1:VAL?` 252 |
| [07-history-segments.png](07-history-segments.png) | Segmented memory: a burst of single acquisitions with timestamps in the segment table | `ACQ:AVA?` 16; `CHAN1:HIST:STAR?` -15; `CHAN1:HIST:STOP?` 0 |
| [08-bode-plot.png](08-bode-plot.png) | Bode plot (K36): the loopback as a 0 dB / 0 degree reference sweep | `BPL:FREQ:STAR?` 1.0000E+02; `BPL:FREQ:STOP?` 1.00000000E+06 |
| [09-i2c-decode.png](09-i2c-decode.png) | I2C on the logic probe: the pattern generator asks *idn? and reads the answer | `BUS1:I2C:FCO?` 3; `BUS1:I2C:FRAM1:DATA?` 48,52; `BUS1:I2C:FRAM2:DATA?` 42,105,100,110,63 |
| [10-spi-decode.png](10-spi-decode.png) | SPI on D0..D3: clock, MOSI, MISO and chip select decoded as text | `BUS1:SPI:FCO?` 13; `BUS1:SPI:FRAM1:DATA:MOSI?` 7,0; `BUS1:SPI:FRAM2:DATA:MOSI?` 128,0 |
| [11-uart-logic.png](11-uart-logic.png) | UART on D0: 115200 Bd, measured from the bit time, decoded as *idn? | `BUS1:UART:FCO?` 1; `BUS1:UART:FRAM1:WCO?` 5 |
| [12-logic-counter.png](12-logic-counter.png) | Logic analyzer: a 4-bit counter on D0..D3 decoded as a parallel bus | `BUS1:PAR:FCO?` 35; `BUS1:PAR:FRAM1:DATA?` 12; `BUS1:PAR:FRAM2:DATA?` 13; `BUS1:PAR:FRAM3:DATA?` 15 |
