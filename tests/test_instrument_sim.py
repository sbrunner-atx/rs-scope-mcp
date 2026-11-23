import struct

import pytest

from rs_scope_mcp import sim
from rs_scope_mcp.instrument import GuardedCommand, Instrument, ValidationError, split_responses
from rs_scope_mcp.transport import encode_block, parse_block


@pytest.fixture()
def inst():
    srv, port = sim.start()
    i = Instrument("127.0.0.1", port, timeout=5)
    i.connect()
    yield i
    i.close()
    srv.shutdown()
    srv.server_close()


def test_identity_and_options(inst):
    assert inst.idn["manufacturer"] == "Rohde&Schwarz"
    assert inst.idn["model"].startswith("RTB2")
    assert {"K1", "K36", "B6"} <= set(inst.options)


def test_set_and_query_roundtrip(inst):
    assert inst.write("CHAN2:SCAL 0.5").ok
    assert inst.query("CHANnel2:SCALe?").response == "0.5"
    inst.write("TRIG:A:MODE NORMal")
    assert inst.query("TRIG:A:MODE?").response == "NORM"  # short form, like the real unit


def test_rst_defaults_from_registry(inst):
    assert inst.query("TIM:SCAL?").response == "100e-6"
    assert inst.query("TRIG:A:MODE?").response == "AUTO"


def test_validation_blocks_bad_suffix(inst):
    with pytest.raises(ValidationError):
        inst.write("CHAN9:SCAL 1")
    with pytest.raises(ValidationError):
        inst.write("NOT:A:COMMand 1")


def test_instrument_errors_are_surfaced(inst):
    inst.validate = False
    res = inst.write("NOT:A:COMMand 1")
    assert not res.ok and res.instrument_errors[0].startswith("-113")


@pytest.mark.parametrize("line", ["*RST", "WGEN:OUTP ON", "PGEN:PATT:STAT ON", "MMEM:DEL 'x.csv'",
                                  "SYST:COMM:INT:ETH:DHCP OFF", "CAL"])
def test_guarded(inst, line):
    with pytest.raises(GuardedCommand):
        inst.write(line)


def test_guard_only_when_enabling(inst):
    assert inst.write("WGEN:OUTP OFF").ok
    assert inst.write("*RST", confirm=True).ok


def test_query_many_batches(inst):
    vals = inst.query_many(["CHAN1:SCAL?", "TIM:SCAL?", "BUS1:I2C:FCO?", "*IDN?"])
    assert vals[2] == "3" and vals[3].startswith("Rohde")


def test_i2c_decode(inst):
    n = int(inst.query("BUS1:I2C:FCOunt?").response)
    frames = [inst.query(f"BUS1:I2C:FRAMe{k}:DATA?").response for k in range(1, n + 1)]
    assert frames[1] == "69,158,174,161"
    assert inst.query("BUS1:I2C:FRAM2:ACC?").response == "READ"


def test_uart_decode(inst):
    n = int(inst.query("BUS2:UART:FRAM1:WCO?").response)
    text = bytes(int(inst.query(f"BUS2:UART:FRAM1:WORD{o}:VAL?").response) for o in range(1, n + 1))
    assert text.startswith(b"$GPGGA")


def test_waveform_real_and_uint(inst):
    inst.write("FORM REAL,32")
    data = inst.query_block("CHAN1:DATA?")
    _x0, _x1, n, _ = inst.query("CHAN1:DATA:HEAD?").response.split(",")
    assert len(data) == 4 * int(n)
    vals = struct.unpack(f">{int(n)}f", data)
    assert max(vals) == pytest.approx(1.0, abs=0.05)
    inst.write("FORM UINT,16")
    assert len(inst.query_block("CHAN1:DATA?")) == 2 * int(n)


def test_logic_channel(inst):
    inst.write("FORM ASC")
    bits = inst.query("DIG0:DATA?").response.split(",")
    assert set(bits) <= {"0", "1"} and len(bits) > 100


def test_screenshot_png(inst):
    png = inst.query_block("HCOP:DATA?")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_bode_arrays(inst):
    inst.write("FORM REAL,32")
    f = inst.query_block("BPL:FREQ:DATA?")
    g = inst.query_block("BPL:GAIN:DATA?")
    assert len(f) == len(g) > 0


def test_block_codec():
    payload = b"\x00\x01hello"
    blk = encode_block(payload)
    assert blk.startswith(b"#")
    assert parse_block(blk) == (payload, len(blk))


def test_split_responses():
    assert split_responses('1;"a;b";3') == ["1", '"a;b"', "3"]


def test_rejected_query_fails_fast_with_reason(inst):
    import time

    from rs_scope_mcp.instrument import InstrumentError
    inst.validate = False
    t0 = time.monotonic()
    with pytest.raises(InstrumentError) as e:
        inst.query("NOT:A:COMMand?")
    assert time.monotonic() - t0 < 1.0
    assert e.value.errors and e.value.errors[0].startswith("-113")


def test_query_many_keeps_positions_on_failure(inst):
    inst.validate = False
    vals = inst.query_many(["CHAN1:SCAL?", "NOT:A:COMMand?", "TIM:SCAL?"])
    assert vals[1] is None and vals[0] and vals[2]
    assert inst.last_errors[1][0].startswith("-113")


def test_firmware_absent_command_blocked(inst):
    inst.idn["firmware"] = "03.000"
    with pytest.raises(ValidationError):
        inst.query("TRIG:A:HYST?")
