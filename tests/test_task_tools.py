"""Task-level tools end to end: simulator -> scope layer -> store -> summaries."""
import numpy as np
import pytest

from rs_scope_mcp import analysis as an
from rs_scope_mcp import server, sim
from rs_scope_mcp.store import Store


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    s, port = sim.start()
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    server._store_box.clear()
    server._state.update(inst=None)
    assert server.connection("connect", "127.0.0.1", port)["ok"]
    yield server
    server.connection("disconnect")
    s.shutdown()
    s.server_close()


def test_capture_harvests_analog_logic_and_decode(srv):
    srv.scpi(["LOG1:STAT ON", "BUS1:TYPE I2C", "BUS2:TYPE UART"])
    r = srv.capture(["CH1", "POD1", "BUS1", "BUS2"], note="t")
    assert r["ok"], r
    s = r["sources"]
    assert s["CH1"]["vpp"] > 1.5
    assert "D0" in s["D0-D7"]["active"]
    assert s["BUS1"]["frames"] == 3 and s["BUS1"]["status"] == {"OK": 3}
    assert s["BUS2"]["ascii"].startswith("$GPGGA")
    cap = srv.data("capture", capture_id=r["capture"])
    assert cap["ok"] and {x["source"] for x in cap["series"]} == {"CH1", "D0-D7"}


def test_drill_down(srv):
    srv.scpi(["LOG1:STAT ON"])
    cid = srv.capture(["CH1", "POD1", "BUS1"])["capture"]
    win = srv.data("series", capture_id=cid, source="CH1", max_points=10)
    assert win["ok"] and len(win["rows"]) <= 10
    edges = srv.data("edges", capture_id=cid, source="D0-D7", limit=4)
    assert len(edges["active"]["D0"]["edges_s"]) == 4
    fr = srv.data("frames", capture_id=cid, bus=1, contains="45 9E")
    assert fr["total"] == 1
    q = srv.data("sql", sql="select count(*) as n from frames")
    assert q["rows"][0][0] == 3


def test_sql_is_read_only(srv):
    assert not srv.data("sql", sql="delete from frames")["ok"]
    assert not srv.data("sql", sql="select 1; drop table frames")["ok"]


def test_decode_wrong_bus_type_is_reported(srv):
    srv.scpi(["BUS1:TYPE CAN"])  # the simulator has no CAN frames: count query is rejected
    r = srv.capture(["BUS1"])
    assert "error" in r["sources"]["BUS1"] or r["sources"]["BUS1"]["frames"] == 0


def test_bode_is_guarded_and_analysed(srv):
    assert srv.bode(10, 1e5)["error"] == "guarded"
    r = srv.bode(10, 1e5, confirm=True)
    assert r["ok"] and r["shape"] == "low-pass"
    assert 2500 < r["minus_3db_hz"] < 3500  # simulated 2nd-order low-pass at 3 kHz


def test_spectrum_peaks(srv):
    r = srv.spectrum("CH1", 0, 2e5)
    assert r["ok"]
    f = [p["freq_hz"] for p in r["peaks"]]
    assert any(abs(x - 1e4) < 500 for x in f) and any(abs(x - 3e4) < 500 for x in f)


def test_history_harvest(srv):
    srv.scpi(["ACQ:NSIN:COUN 4", "RUNS"])
    info = srv.history("info")
    assert info["available"] == 4 and info["first"] == -3
    h = srv.history("harvest", ["CH1", "BUS1"], max_segments=10)
    assert h["harvested"] == 4 and [row["segment"] for row in h["segments"]] == [0, -1, -2, -3]


def test_setup_save_and_guarded_restore(srv):
    srv.scpi(["CHAN1:SCAL 0.2"])
    cid = srv.setup("save")["capture"]
    srv.scpi(["CHAN1:SCAL 1"])
    assert srv.setup("restore", capture_id=cid)["error"] == "guarded"
    assert srv.setup("restore", capture_id=cid, confirm=True)["ok"]
    assert srv.scpi("CHAN1:SCAL?")["results"][0]["response"] == "0.2"


def test_analysis_frequency_and_bode():
    t = np.arange(10000) * 1e-6
    s = an.analog_summary(np.sin(2 * np.pi * 1000 * t).astype(np.float32), 1e-6)
    assert abs(s["frequency_hz"] - 1000) < 5 and abs(s["vpp"] - 2) < 0.01
    f = np.logspace(1, 5, 60)
    g = -10 * np.log10(1 + (f / 1000) ** 2)
    b = an.bode_summary(f, g, -np.degrees(np.arctan(f / 1000)))
    assert abs(b["minus_3db_hz"] - 1000) < 60 and b["shape"] == "low-pass"


def test_store_series_roundtrip(tmp_path):
    st = Store(tmp_path)
    cid = st.new_capture("capture")
    st.add_series(cid, "CH1", "analog", -1e-3, 1e-6, "V", np.arange(5, dtype=np.float32), {"vpp": 4})
    meta, arr = st.series(cid, "CH1")
    assert meta["unit"] == "V" and arr.tolist() == [0, 1, 2, 3, 4]


def test_demo_tool_guarded_blocks_other_tools_and_restores(srv):
    import time

    from rs_scope_mcp import demo as D
    assert srv.demo("start")["error"] == "guarded"
    assert {s["id"] for s in srv.demo("scenes", level="full")["scenes"]} >= {"heartbeat", "mask", "bus-i2c"}
    srv.scpi(["CHAN1:SCAL 0.2"])
    # analog scenes without the heartbeat (the simulator has no file system for the waveform upload)
    D_only = [s.id for s in D.scenes_for("analog") if not s.heart][:2]
    orig = D.SCENES[:]
    settles = {s.id: s.settle for s in orig}
    D.SCENES[:] = [s for s in orig if s.id in D_only]
    for s in D.SCENES:
        s.settle = 0.05
    try:
        r = srv.demo("start", level="analog", dwell_s=0.1, loops=1, confirm=True)
        assert r["ok"] and r["scenes"] == D_only
        blocked = srv.scpi("CHAN1:SCAL?")
        assert blocked["ok"] is False and "demo" in blocked["message"]
        for _ in range(100):
            if not srv.demo("status")["running"]:
                break
            time.sleep(0.1)
        st = srv.demo("status")
        assert not st["running"] and st["result"]["restored"] and st["result"]["scenes_played"] == 2
        assert srv.scpi("CHAN1:SCAL?")["results"][0]["response"] == "0.2"
    finally:
        D.SCENES[:] = orig
        for s in orig:
            s.settle = settles[s.id]


def test_envelope_am_depth_and_keying():
    dt = 1e-7
    t = np.arange(200000) * dt                     # 20 ms
    carrier = np.sin(2 * np.pi * 100e3 * t)
    am = (1 + 0.8 * np.sin(2 * np.pi * 1e3 * t)) * carrier
    e = an.envelope(am.astype(np.float32), dt)
    assert abs(e["am_depth"] - 0.8) < 0.03
    key = np.clip((t - 0.005) / 0.002, 0, 1)       # 2 ms linear key-down ramp
    e2 = an.envelope((key * carrier).astype(np.float32), dt, carrier_hz=100e3)
    assert abs(e2["rise_10_90_s"] - 0.0016) < 0.0003


def _pod(lines: dict, n: int) -> np.ndarray:
    out = np.zeros(n, dtype=np.uint8)
    for bit, arr in lines.items():
        out |= (arr.astype(np.uint8) & 1) << bit
    return out


def test_identify_uart_and_pwm():
    dt, n = 1e-6, 20000
    bit = round(1 / 115200 / dt * 100) / 100  # samples per bit (fractional)
    line = np.ones(n, dtype=np.uint8)
    pos = 100.0
    for byte in b"*idn?\n" * 3:
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        for b in bits:
            line[int(pos):int(pos + bit)] = b
            pos += bit
        pos += 3 * bit
    g = an.identify_bus(_pod({0: line}, n), 0, dt)
    assert g["guess"] == "UART" and g["baud_standard"] == 115200
    t = np.arange(n) * 1e-4                        # 2 s at 100 us
    pwm = ((t % 0.02) < 0.0015).astype(np.uint8)   # 50 Hz, 1.5 ms
    p = an.identify_bus(_pod({2: pwm}, n), 0, 1e-4)
    assert p["guess"].startswith("PWM on D2") and "servo" in p["guess"]


@pytest.mark.parametrize("name,guess,setup", [
    ("i2c", "I2C", {"CLOCk:SOURce": "D0", "DATA:SOURce": "D1"}),
    ("spi", "SPI", {"CLOCk:SOURce": "D0", "CS:SOURce": "D3", "CS:POLarity": "NEG"}),
    ("uart", "UART", {"BAUDrate": 115200, "POLarity": "IDLH"}),
    ("servo", "PWM on D0", {}),
])
def test_identify_on_real_rtb2004_captures(name, guess, setup):
    """Short (75 kSa) records from the RTB2004 pattern generator, starting mid-transfer."""
    import json as _json
    from pathlib import Path
    d = Path(__file__).parent / "data"
    meta = _json.loads((d / "pods.json").read_text())[name]
    r = an.identify_bus(np.load(d / f"pod1-{name}.npy"), meta["t0"], meta["dt"])
    assert r["guess"].startswith(guess), r["guess"]
    for k, v in setup.items():
        assert r["decoder_setup"][k] == v
