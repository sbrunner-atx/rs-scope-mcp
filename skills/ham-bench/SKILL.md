---
name: ham-bench
description: >
  Amateur radio bench measurements with an R&S RTB2 / RTB2000 through rs-scope-mcp: safe RF
  sampling of a transmitter (dummy load and attenuation arithmetic), AM modulation depth, CW keying
  shape and key clicks, SSB two-tone tests for flat-topping and IMD, harmonics, filter response
  (crystal, IF and audio filters) with the Bode plot, and audio levels for digital modes. Use when
  the operator wants to check a transmitter, a modulator, a filter or an audio chain. Read
  scope-operating first. Recipes verified on an RTB2004 with its own generator as the signal source.
---

# Ham radio bench work

## Before any transmitter measurement

**Never connect a transmitter to the scope directly.** Transmit into a dummy load rated for the
full power, and take the scope signal from a sampler or tap on the dummy load, followed by
attenuators, terminated in 50 ohm at the scope input (feed-through terminator).

Keep the signal at the scope below about 2 Vpp (+10 dBm). Peak-to-peak voltage on 50 ohm is
`Vpp = 2 * sqrt(2 * P * 50)`:

| Transmitter | Vpp on 50 ohm | Attenuation for about 1 Vpp at the scope |
|---|---|---|
| 5 W | 45 V | 33 dB |
| 10 W | 63 V | 36 dB |
| 100 W | 200 V | 46 dB |
| 500 W | 447 V | 53 dB |
| 1500 W | 775 V | 58 dB |

The first attenuator (or the sampler) must be rated for the power it sees. A typical setup is a
dummy load with a -40 dB sample port plus a 6 to 20 dB pad. Ask the operator what is in the chain
and do the arithmetic aloud before anyone keys the radio.

**Bandwidth.** RTB2 models run from 70 to 300 MHz depending on the bandwidth option (check
`connection`). HF and 6 m are fine on any of them; 2 m needs a 200 or 300 MHz unit, and amplitudes
near the bandwidth limit read low. The FFT is not a spectrum analyzer: its own spurious and harmonic
levels are roughly -40 to -50 dBc, so report harmonics relative to the carrier and say so.

## AM modulation depth

1. Timebase for 5 to 10 audio cycles (1 kHz tone: `TIM:SCAL 1e-3`). Trigger `AUTO`.
2. `capture(sources=["CH1"], note="AM, tone 1 kHz")`.
3. `data(action="envelope", capture_id=..., source="CH1")` returns `am_depth`
   (m = (Emax - Emin) / (Emax + Emin)), the envelope extremes and the crest factor.
4. Report depth in percent. Over 100 % shows as an envelope that touches zero with flat gaps
   (`envelope_min_v` near zero and a high crest factor): that splatters.

Bench note: the RTB2's own generator set to 80 % AM measured 75 % with this method, consistently
across window sizes. Trust the measurement, not the dial.

Optional, the trapezoid: audio on CH2, RF on CH1, `DISP:MODE XY` with CH2 as X. Straight sides
mean linear modulation; curved sides mean distortion.

## CW keying shape and key clicks

1. Send a string of dits (or hold the key for a long dah). `TIM:SCAL` 2 to 5 ms/div, trigger on CH1
   rising edge, `TRIG:A:MODE NORM`, level a little above zero.
2. `capture(sources=["CH1"])`, then `data(action="envelope", ...)`: `rise_10_90_s` and
   `fall_90_10_s`.
3. Interpret: rise and fall of about 4 to 8 ms are clean. Under 1 ms means hard keying and key
   clicks. The resolution is one envelope window (shown in the result).
4. Confirm in the frequency domain: `spectrum(source="CH1", start_hz=f0 - 5000, stop_hz=f0 + 5000)`.
   A clean keyed carrier has sidebands that fall off quickly; hard keying spreads them wide (peaks
   still 20 dB above the floor several kHz away).

Bench note: a keyed carrier made with 100 % square-wave AM shows a rise under one 20 us window and
sideband peaks every 400 Hz out to 2 kHz either side of the carrier at -20 to -38 dBV.

## SSB: two-tone test

1. Feed two equal audio tones, not harmonically related (700 Hz and 1900 Hz), at normal drive.
2. Envelope: `capture` CH1 over several beat periods (the beat is f2 - f1 = 1200 Hz:
   `TIM:SCAL 1e-3`), then `data(action="envelope", ...)`. A clean two-tone envelope looks like
   rectified sine humps that meet at zero; flat-topping shows as flattened peaks. Compare
   `envelope_max_v` and crest factor at increasing drive: when the peak stops rising while drive
   goes up, the amplifier is compressing.
3. IMD: `spectrum(source="CH1", start_hz=..., stop_hz=..., window="FLAT")` around the carrier with
   `rbw_hz` under 100 Hz. The third-order products sit at 2 f1 - f2 and 2 f2 - f1 (RF: 1200 Hz
   below the lower tone and above the upper one). Report them in dB below one tone.

## Harmonics

`spectrum(source="CH1", start_hz=0, stop_hz=5 * f0)` and report each harmonic in dBc relative to
the fundamental peak. State the FFT's own limit (about -40 to -50 dBc) and recommend a spectrum
analyzer for compliance-level checks.

## Filter response (crystal, IF, audio, matching networks)

The Bode plot (option K36) sweeps the generator into the filter and measures input and output.

1. Wiring: Aux Out -> filter input; CH1 on the filter input (the reference), CH2 on the output.
   Terminate the filter in its design impedance (50 ohm feed-through, or the IF's impedance).
2. Ask the operator to confirm the wiring, then
   `bode(start_hz=..., stop_hz=..., points_per_decade=50, input_channel="CH1", output_channel="CH2", confirm=true)`.
   The range is 10 Hz to 25 MHz.
3. The result gives the low and high-frequency gain, the peak, -3 dB and -6 dB points, the phase
   there and the shape. For a band-pass, bandwidth = upper - lower -3 dB point; centre = geometric
   mean. For a crystal filter, narrow the sweep to the passband and raise the points per decade to
   see ripple.
4. The arrays are stored (`bode.npz` in the capture folder) for comparison between filters.

Bench note: CH1 against CH1 gives a flat 0 dB / 0 degree line in about 7 s for 41 points; use it
to check the setup before connecting the filter.

## Audio levels for digital modes

For the sound card feeding fldigi or WSJT-X:

1. Probe the audio line (10x probe; the audio ground is the ground clip).
2. `capture(sources=["CH1"])`: report Vpp, RMS and `clipping`. Clean digital-mode audio is well
   below the interface's clipping level; a crest factor near 1.4 for a single tone is expected.
3. On the RF side, `spectrum` around the carrier shows splatter from overdrive as extra products
   beside the wanted signal.

## Practice signals without a radio

The scope's generator can play every case above into CH1 (Aux Out -> 50 ohm terminator -> CH1):
`WGEN:FUNC SIN`, `WGEN:MOD:TYPE AM`, `WGEN:MOD:FUNC SIN|SQU`, `WGEN:MOD:AM:DEPT`, FM with
`WGEN:MOD:TYPE FM`, bursts with `WGEN:BURS`. Use them to show the operator what to expect before
measuring the real transmitter.
