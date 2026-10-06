"""Settings stays tidy; the troubleshooting tools live on /debug."""
import CTS_Scoreboard as C


def test_settings_and_debug_pages_render():
    c = C.app.test_client()
    s = c.get('/settings')
    d = c.get('/debug')
    assert s.status_code == 200 and d.status_code == 200
    s, d = s.get_data(as_text=True), d.get_data(as_text=True)
    # diagnostics and the simulator moved off Settings
    assert 'id="ser_raw"' not in s and 'id="dbg_heat"' not in s
    assert 'id="ser_raw"' in d and 'id="dbg_heat"' in d and 'id="ser_fmt"' in d
    # Settings keeps the console status, port, update rate and go-black controls
    for needle in ('id="cs_pill"', 'name="serial_port"', 'id="ser_rate"', 'id="bk_auto"', 'href="/debug'):
        assert needle in s, needle
    # nothing loaded from a CDN: the pool PC may be offline
    for page in (s, d):
        assert 'bootstrapcdn' not in page and 'googleapis' not in page
