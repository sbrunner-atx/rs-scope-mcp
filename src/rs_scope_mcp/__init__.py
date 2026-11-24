"""rs-scope-mcp: control and data harvesting for Rohde & Schwarz oscilloscopes.

Supported and bench-tested: RTB2 / RTB2000 series (RTB2002, RTB2004).

Layers (bottom up):
  transport  - raw SCPI socket, IEEE 488.2 block data, timeouts
  registry   - every documented SCPI command, validation, search
  instrument - connection, sync, error queue, safety gate, batching
  sim        - a simulated RTB2 that speaks SCPI on a local socket (tests, demos)
  server     - the MCP server
"""

__version__ = "0.1.3"
