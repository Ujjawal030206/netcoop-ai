import json
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "streamlit_app.py")
MANIFEST = json.loads((ROOT / "data" / "scenarios" / "manifest.json").read_text(encoding="utf-8"))


def app():
    return st_testing.AppTest.from_file(APP, default_timeout=60)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_every_scenario_renders(name):
    at = app().run()
    at.selectbox(key="scenario").set_value(name).run()
    assert not at.exception
    headings = " ".join(h.value for h in at.markdown)
    assert "Insufficient" in headings or "Likely" in headings or "Probable" in headings or "Possible" in headings \
        or "No fault" in headings or "could not be processed" in headings


def test_follow_up_switch_changes_the_ambiguity_result():
    at = app().run()
    at.selectbox(key="scenario").set_value("conflicting_wifi_vs_congestion.json").run()
    on = [m.value for m in at.metric]
    at.toggle(key="follow_up").set_value(False).run()
    assert not at.exception
    off = [m.value for m in at.metric]
    assert on[3] == "2" and off[3] == "0"          # follow-up questions asked: 2 -> 0
    assert on[0] != off[0]                         # confidence changes without them


def test_crash_and_paste_paths():
    at = app().run()
    at.selectbox(key="crash").set_value("infrastructure_agent").run()
    assert not at.exception
    assert any("infrastructure_agent did not report" in m.value for m in at.markdown)
    at = app().run()
    at.radio[0].set_value("Paste or upload telemetry").run()
    at.text_area[0].input('{"device_id": "SW_1", "metrics": {broken').run()
    assert not at.exception
