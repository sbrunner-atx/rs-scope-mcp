# Changelog

## 0.1.0 (unreleased)

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
- `rs-scope` command line for bench checks, the demo and screenshots; a registry-driven simulator.
- Licensed under PolyForm Noncommercial 1.0.0: free for hobbyists; commercial licensing through
  Westlake Professionals.
