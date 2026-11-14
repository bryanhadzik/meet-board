"""Event 99 on the console is a test race: ignored by the board, overlay and race history."""
import CTS_Scoreboard as C


def rec_event_heat(event, heat):
    # channel 12 record: digits at positions 0-2 (event) and 5-7 (heat), low nybble inverted
    digits = ("%3s" % event)[:3] + "  " + ("%3s" % heat)[:3]
    out = [0x26]
    for i, d in enumerate(digits):
        v = 0x0F if d == " " else int(d)
        out.append((i << 4) | (v ^ 0x0F))
    return out


def test_event_99_is_ignored(monkeypatch):
    sent, emits = [], []
    monkeypatch.setattr(C, "send_event_info", lambda *a, **k: sent.append(C.last_event_sent))
    monkeypatch.setattr(C.socketio, "emit", lambda *a, **k: emits.append(a))
    monkeypatch.setattr(C, "_last_emit", 0.0)
    C.last_event_sent = (5, 1)
    C.parse_line(rec_event_heat(99, 1))
    assert C.test_race and C.last_event_sent == (5, 1) and not sent and not emits
    C.parse_line(rec_event_heat(6, 1))
    assert not C.test_race and C.last_event_sent == (6, 1) and sent
