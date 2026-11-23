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
