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
    on = kpis(at)
    at.toggle(key="follow_up").set_value(False).run()
    assert not at.exception
    off = kpis(at)
    assert on["followups"] == "2" and off["followups"] == "0"    # follow-up questions asked: 2 -> 0
    assert on["confidence"] != off["confidence"]                  # confidence changes without them


def kpis(at):
    import re
    block = next(m.value for m in at.markdown if 'class="kpis"' in m.value)
    return dict(re.findall(r'data-(\w+)="([^"]*)"', block))


def test_crash_and_paste_paths():
    at = app().run()
    at.selectbox(key="crash").set_value("infrastructure_agent").run()
    assert not at.exception
    assert any("infrastructure_agent did not report" in m.value for m in at.markdown)
    at = app().run()
    at.radio[0].set_value("Paste or upload telemetry").run()
    at.text_area[0].input('{"device_id": "SW_1", "metrics": {broken').run()
    assert not at.exception
