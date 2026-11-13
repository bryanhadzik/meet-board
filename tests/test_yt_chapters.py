import yt_chapters


def make(tmp_path):
    log = yt_chapters.StreamLog(str(tmp_path / "stream_log.json"))
    t0 = 1_800_000_000.0
    # stream started at t0 (seen 100 s in), races later
    log.stream(True, 100, now=t0 + 100)
    log.stream(True, 3000, now=t0 + 3000)
    log.race("1", "1", 2.0, now=t0 + 12)         # event 1 starts 10 s in -> clamps to 0:00
    log.race("1", "1", 5.0, now=t0 + 15)         # same start seen again: ignored
    log.race("2", "1", 0.5, now=t0 + 300.5)
    log.race("2", "2", 0.0, now=t0 + 420)
    log.race("3", "1", 0.0, now=t0 + 3700)       # after the stream: not in this session
    log.race("3", "1", 0.0, now=t0 + 1000)
    return log, t0


def test_chapters(tmp_path):
    log, t0 = make(tmp_path)
    names = {1: "Women 200 Yard Medley Relay", 2: "Men 200 Yard Individual Medley", "3": "Women 50 Yard Freestyle"}
    text, info = yt_chapters.chapters(log, names)
    assert text.splitlines() == ["0:00 Event 1 Women 200 Medley Relay",
                                 "4:30 Event 2 Men 200 IM",
                                 "16:10 Event 3 Women 50 Freestyle"]
    assert info["ok"] and info["events"] == 3 and info["races"] == 4


def test_warmups_and_spacing(tmp_path):
    log = yt_chapters.StreamLog(str(tmp_path / "s.json"))
    t0 = 1_800_000_000.0
    log.stream(True, 0, now=t0)
    log.stream(True, 4000, now=t0 + 4000)
    for i, at in enumerate((1200, 1235, 1236), 1):
        log.race(str(i), "1", 0, now=t0 + at)
    text, info = yt_chapters.chapters(log, {})
    lines = text.splitlines()
    assert lines[0] == "0:00 Warm-ups"
    secs = [int(l.split()[0].split(":")[0]) * 60 + int(l.split()[0].split(":")[1]) for l in lines]
    assert all(b - a >= 10 for a, b in zip(secs, secs[1:]))


def test_persist_and_sessions(tmp_path):
    log, t0 = make(tmp_path)
    again = yt_chapters.StreamLog(str(tmp_path / "stream_log.json"))
    assert again.sessions()[0]["races"] == 4
    assert yt_chapters.chapters(yt_chapters.StreamLog(str(tmp_path / "none.json")), {})[1]["error"]
