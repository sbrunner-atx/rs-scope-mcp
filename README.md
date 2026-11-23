# rs-scope-mcp

**Let Claude drive your Rohde & Schwarz oscilloscope, and keep what it measures.**

An MCP server that connects Claude (and other AI assistants) to a Rohde & Schwarz oscilloscope over
the LAN. It controls the instrument through its complete SCPI command set and **harvests the data**:
waveforms, logic channels, serial-bus decodes, Bode plots, FFTs, measurements, segmented history and
screenshots, so Claude can analyse real numbers instead of guessing from a picture.

Part of the AE5VG amateur radio AI collection: <https://ae5vg.com>.

## Supported instruments

| Series | Models | Status |
|---|---|---|
| **R&S RTB2 / RTB2000** | RTB2002, RTB2004 | Supported and bench-tested (RTB2004, firmware 03.000, all options) |

The RTB2 is the rebranded RTB2000: identical hardware, a different enclosure color scheme, and firmware
that runs on either, so both names are covered. The project is structured per instrument family
(`src/rs_scope_mcp/data/<family>/`) so other R&S scopes can follow.

> Independent project, not affiliated with or endorsed by Rohde & Schwarz. R&S and RTB2000 are
> trademarks of Rohde & Schwarz GmbH & Co. KG.

## What you can ask

- "Measure the frequency and peak-to-peak on channel 1, then show me the screen."
- "Capture the I2C traffic on D0/D1 and list every address and byte."
- "Sweep this filter from 100 Hz to 1 MHz and tell me the -3 dB points."
- "Grab 40 single shots and find the one where the UART frame is corrupted."
- "Check my CW keying waveform for clicks." (always through an attenuator or dummy load)

Built for three kinds of work: **amateur radio** bench tests, **hardware reverse engineering**, and
**microcontroller / embedded / drone** development.

## Status

Pre-alpha. In place and bench-tested:

- **Complete SCPI foundation:** 748 documented command blocks (962 headers) from the manual, plus 415
  undocumented command families the firmware reports. Every command is validated before it is sent.
- **Reliable sessions:** rejected queries detected in about 2 ms (the instrument otherwise stays silent),
  the error queue read after every write, batching, and full-setup snapshot and restore.
- **Safety gate:** generator and pattern outputs, Bode sweeps, resets, self-alignment, file deletion and
  network settings need explicit confirmation.
- **Task tools:** `capture` (acquire and harvest analog channels, logic pods and bus decodes in one
  call), `measure`, `spectrum`, `bode`, `history` (walk segmented memory), `data` (drill into stored
  samples, edges and frames, or read-only SQL), `setup` (show, save, restore) and `session`.
- **Local data store:** every capture is kept in `RS_SCOPE_DATA_DIR` (SQLite catalog plus sample
  files and the scope's own setup), so Claude gets a compact summary and asks for details only when
  needed.
- **Pass/fail testing and demo mode:** `mask` learns a tolerance envelope from a good signal and
  counts failures (keeping only the failing acquisitions for analysis); `demo` plays a looping show
  of what the scope can do, analog only or with the logic probe ([docs/DEMO.md](docs/DEMO.md)).
- **Foundation tools:** `connection`, `scpi_search`, `scpi_describe`, `scpi`, `scpi_block`, `screenshot`.
- **Simulator** for tests and demos without hardware.

Next: running it as a live connector in Claude, packaging (`.mcpb`, PyPI, MCP Registry), and skills.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

```bash
uv sync
uv run python -m rs_scope_mcp.sim --port 5025        # simulated scope, or use a real one
uv run rs-scope --host 127.0.0.1 idn
uv run rs-scope --host 127.0.0.1 query "CHAN1:SCAL?" "TIM:SCAL?"
uv run rs-scope search "i2c frame data"
```

### Connect the real scope

On the scope: **Menu, Setup, Interface, Ethernet, Parameter**. Use DHCP or a fixed address and leave
*IP Port* at 5025. The scope also announces itself over mDNS (`_scpi-raw._tcp`).

```bash
uv run rs-scope --host <scope-ip> idn
uv run rs-scope --host <scope-ip> screenshot scope.png
uv run rs-scope --host <scope-ip> tree-diff          # compare the registry with your firmware
```

### Use it from Claude

```json
{
  "mcpServers": {
    "rs-scope": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/rs-scope-mcp", "rs-scope-mcp"],
      "env": { "RS_SCOPE_HOST": "192.168.1.50" }
    }
  }
}
```

Settings: `RS_SCOPE_HOST`, `RS_SCOPE_PORT` (5025), `RS_SCOPE_TIMEOUT` (10 s),
`RS_SCOPE_DATA_DIR` (`~/rs-scope-data`, where screenshots and captures are stored).

## Documentation

- [Demo mode](docs/DEMO.md): wiring for the analog and the full demo, and every scene.
- [SCPI API reference](docs/rs-scope-api.md) ([PDF](docs/rs-scope-api.pdf)): every command, with the
  bench-verified behaviour the manual does not mention.
- [Architecture](docs/ARCHITECTURE.md): the layer stack, the data store and the context strategy.
- [Bench notes](docs/BENCH-NOTES.md): what was measured on the RTB2004.
- [Field Guide screenshots](docs/field-guide/screenshots/): captured by `scripts/field_guide_shots.py`.

The command registry is generated from your own copy of the R&S RTB2 user manual:

```bash
uv run --extra extract python scripts/extract_scpi.py "/path/to/R&S RTB2 User Manual.pdf"
uv run python scripts/gen_api_docs.py
```

## License: free for hobbyists, licensed for business

**Free for hobbyists.** Personal use, amateur radio, education and nonprofit use are free under the
[PolyForm Noncommercial License 1.0.0](LICENSE). The source is open to read, change and share.

**Commercial use needs a license.** Any use by or for a business, including internal use on a
company test bench, needs a commercial license. Support for more instruments, custom skills and
integrations, and support plans are available too. See [COMMERCIAL.md](COMMERCIAL.md), and buy
through **Westlake Professionals**: <https://westlakeprofessionals.com>.

Paid work is what keeps the free version going.
