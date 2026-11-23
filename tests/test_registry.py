import pytest

from rs_scope_mcp.registry import (
    Registry,
    check_value,
    enum_match,
    header_regex,
    parse_number,
    short_form,
    split_args,
)


@pytest.fixture(scope="module")
def reg():
    return Registry.default()


def test_registry_is_complete(reg):
    # every command listed in the manual's subsection indexes was found in the body
    assert reg.meta["missing_from_body"] == 0
    assert reg.meta["index_entries"] >= 930
    assert len(reg) >= 740
    hdrs = {h for c in reg.commands for h in c.all_headers}
    assert len(hdrs) >= 950


def test_all_headers_compile(reg):
    for c in reg.commands:
        for h in c.all_headers:
            header_regex(h)


@pytest.mark.parametrize("hdr,short", [("CHANnel", "CHAN"), ("I2C", "I2C"), ("FRAMe", "FRAM"),
                                       ("RXValue", "RXV"), ("DATA", "DATA")])
def test_short_form(hdr, short):
    assert short_form(hdr) == short


@pytest.mark.parametrize("line,header,suffixes", [
    ("chan2:scal 0.5", "CHANnel<m>:SCALe", {"m": 2}),
    ("CHANNEL3:SCALE?", "CHANnel<m>:SCALe", {"m": 3}),
    (":CHAN:SCAL?", "CHANnel<m>:SCALe", {}),
    ("FORM REAL,32", "FORMat[:DATA]", {}),
    ("FORMAT:DATA?", "FORMat[:DATA]", {}),
    ("BUS1:I2C:FRAM2:DATA?", "BUS<b>:I2C:FRAMe<n>:DATA?", {"b": 1, "n": 2}),
    ("bus2:uart:rx:fram10:word3:val?", "BUS<b>:UART:FRAMe<n>:WORD<o>:VALue?", {"b": 2, "n": 10, "o": 3}),
    ("DIG15:DATA?", "LOGic<p>:DATA?", {"m": 15}),
    ("*IDN?", "*IDN?", {}),
    ("MEAS2:RES?", "MEASurement<m>:RESult[:ACTual]?", {"m": 2}),
    ("BPL:GAIN:DATA?", "BPLot:GAIN:DATA?", {}),
    ("HCOP", "HCOPy[:IMMediate]", {}),
])
def test_resolve(reg, line, header, suffixes):
    r = reg.resolve(line)
    assert r is not None, line
    assert header in r.command.all_headers
    assert r.suffixes == suffixes
    assert r.ok, r.errors


def test_suffix_out_of_range(reg):
    r = reg.resolve("CHAN5:SCAL 1")
    assert not r.ok and "suffix" in r.errors[0]


def test_query_only_cannot_be_set(reg):
    r = reg.resolve("BUS1:I2C:FCOunt 3")
    assert r is None or not r.ok


def test_unknown_header(reg):
    assert reg.resolve("FOO:BAR 1") is None


def test_enum_warning(reg):
    r = reg.resolve("TRIG:A:MODE SOMETIMES")
    assert r.ok and r.warnings
    r = reg.resolve("TRIG:A:MODE norm")
    assert r.ok and not r.warnings


def test_enums_with_ranges_are_expanded(reg):
    c = reg.by_header("MEASurement<m>:SOURce")
    assert "D15" in c.parameters[0]["enum"] and "CH4" in c.parameters[0]["enum"]
    c = reg.by_header("MEASurement<m>:MAIN")
    assert {"FREQuency", "RMS", "PHASe", "BWIDth"} <= set(c.parameters[0]["enum"])


def test_search(reg):
    hits = [c.header for c in reg.search("i2c frame data")]
    assert "BUS<b>:I2C:FRAMe<n>:DATA?" in hits[:5]
    assert any(h.startswith("BPLot:GAIN:DATA") for h in [c.header for c in reg.search("bode gain data")][:5])


@pytest.mark.parametrize("s,v", [("5", 5), ("5mV", 5e-3), ("10 MHz", 10e6), ("2us", 2e-6),
                                 ("1e-3", 1e-3), ("3MA", 3e6), ("ON", None)])
def test_parse_number(s, v):
    assert parse_number(s) == (pytest.approx(v) if v is not None else None)


def test_split_args():
    assert split_args('REAL,32') == ["REAL", "32"]
    assert split_args('"a,b",1') == ['"a,b"', "1"]
    assert split_args("") == []


def test_enum_match():
    assert enum_match("asc", ["ASCii", "REAL"]) == "ASCii"
    assert enum_match("ascii", ["ASCii", "REAL"]) == "ASCii"
    assert enum_match("as", ["ASCii", "REAL"]) is None


def test_check_value_range():
    w = []
    check_value({"name": "<x>", "range": "-5 to 5"}, "7", w)
    assert w


def test_firmware_tree_overlay(reg):
    fw = reg.meta["firmware_trees"]["03.000"]
    assert fw["headers"] > 3000 and fw["undocumented_added"] > 300
    r = reg.resolve("CHAN2:HIST:ATIC:ALL?")
    assert r and r.ok and r.command.kind == "undocumented" and r.suffixes == {"a": 2}
    assert reg.resolve("CHAN1:SCAL?").command.firmware == {"03.000"}


def test_overrides(reg):
    c = reg.by_header("DEVice:MODE")
    assert "BPL" in c.parameters[0]["enum"] and c.notes
    assert "03.000" in reg.by_header("TRIGger:A:HYSTeresis").absent_in
    assert any("TYPE" in n for n in reg.by_header("BUS<b>:I2C:FRAMe<n>:DATA?").notes)


def test_manual_case_typos_repaired(reg):
    # the manual prints SPI frame queries as 'FRAME<n>'; the firmware short form is FRAM
    r = reg.resolve("BUS1:SPI:FRAM2:DATA:MOSI?")
    assert r and r.ok and r.command.header == "BUS<b>:SPI:FRAMe<n>:DATA:MOSI?"
    assert reg.resolve("WGEN:MOD:ENAB ON").command.header == "WGENerator:MODulation[:ENABle]"


def test_logic_threshold_split(reg):
    assert reg.resolve("LOG1:THR USER").command.header == "LOGic<p>:THReshold"
    r = reg.resolve("LOG2:THR:UDL2 0.5")
    assert r.command.header == "LOGic<p>:THReshold:UDLevel<n>" and r.command.source == "bench"
    assert r.suffixes == {"p": 2, "n": 2}


def test_documented_commands_win_over_undocumented(reg):
    # LOG<a>:THR:UDL<b> is in the firmware tree; the bench command must be chosen
    r = reg.resolve("LOG1:THR:UDL 0.5")
    assert r.command.source == "bench"
    assert not any(c.header == "LOG<a>:THR:UDL<b>" for c in reg.commands)
