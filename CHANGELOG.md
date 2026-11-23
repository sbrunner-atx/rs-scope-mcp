# Changelog

## 0.1.2 (unreleased)

Found by the new 40-check acceptance suite run end to end on an RTB2004:

- Mode changes (`DEV:MODE`, `SPEC`, `BPL:ENAB`, `MASK:STAT`, `DISP:MODE`) wait for completion;
  commands sent during the switch failed with -221.
- Leaving the Bode application restores the setup saved before the sweep (the Bode application
  locks the memory to 75 kSa and changes channel and generator settings).
- `mask create` builds the envelope itself, returns to normal acquisition and self-checks the mask;
  the -221 recovery no longer re-learns the mask from the signal under test.
- `capture` switches bus decoding on before acquiring.
- `identify`: idle levels from the longest clock pause or the level before the first edge (short
  records that start mid-transfer); chip select is the line that holds while the clock runs.
- Text previews use the low 8 bits of 9-bit UART words.
- Skills: notes on self-checked masks, the Bode restore, incomplete frames and parallel glitches.
- `scripts/acceptance_test.py`: 40 checks in five groups; real-capture regression fixtures.

## 0.1.1

- `measure` takes a fresh single acquisition when the scope is stopped (a stopped record may not
  contain the source, for example after a capture with that channel off).
- `history` stops the acquisition first (history queries are rejected while it runs); `mask test`
  with `keep_failed` stops at the end so the stored failures stay frozen.
- Desktop extension: display name without a slash (it broke the launch), data folder defaults to
  `${HOME}/rs-scope-data` and is created on first use.
- `scripts/acceptance_test.py`: 21 checks on real hardware through the MCP protocol.

## 0.1.0

First release. Supported and bench-tested: R&S RTB2 / RTB2000 series (RTB2004, firmware 03.000).

- Complete SCPI foundation: the user manual's command chapter (749 command blocks, 962 headers) with
  the firmware's own command tree overlaid (408 undocumented command families) and bench-verified
  corrections. Every command is validated before it is sent; rejected queries are detected in about
  2 ms; guarded commands need confirmation.
- Task tools: `capture`, `measure`, `spectrum`, `bode`, `mask`, `history`, `data`, `session`,
  `setup`, `demo`, plus the foundation tools `connection`, `scpi`, `scpi_search`, `scpi_describe`,
  `scpi_block`, `screenshot`.
- Local data store (SQLite catalog and sample files) with summaries first and drill-down on request.
- Demo mode with analog and full (logic probe) scene sets.
- `data` actions `envelope` (AM depth, keying rise/fall, crest factor) and `identify` (I2C, SPI, UART with baud, PWM/servo, clock from logic edges), bench-verified.
- Skills: scope-operating, ham-bench, bus-reverse-engineering, embedded-bringup.
- `rs-scope` command line for bench checks, the demo and screenshots; a registry-driven simulator.
- Licensed under PolyForm Noncommercial 1.0.0: free for hobbyists; commercial licensing through
  Westlake Professionals.
