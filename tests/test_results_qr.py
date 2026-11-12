"""Phone-results QR code for the meet boards."""
import CTS_Scoreboard as cs


def test_results_qr_info_and_svg():
    c = cs.app.test_client()
    cs.settings['results_url'] = ''
    d = c.get('/api/results_qr').get_json()
    assert d['url'] == '' and d['mode'] == 'slide'
    assert c.get('/results_qr.svg').status_code == 404
    cs.settings.update(results_url='https://bryanhadzik.github.io/swim-results/', results_qr='corner', results_every='abc', results_secs='500')
    d = c.get('/api/results_qr').get_json()
    d.pop('stream')
    assert d == {'url': 'https://bryanhadzik.github.io/swim-results/', 'mode': 'corner', 'every': 4, 'secs': 120}
    r = c.get('/results_qr.svg')
    assert r.status_code == 200 and r.mimetype == 'image/svg+xml' and b'<svg' in r.data and b'viewBox' in r.data and b'width=' not in r.data.split(b'>')[0]
    cs.settings['results_url'] = ''


def test_stream_link():
    c = cs.app.test_client()
    for given, want in [('@tooeleswim', 'https://www.youtube.com/@tooeleswim/live'),
                        ('tooeleswim', 'https://www.youtube.com/@tooeleswim/live'),
                        ('https://www.youtube.com/@Pool.Live/featured', 'https://www.youtube.com/@Pool.Live/live'),
                        ('https://youtube.com/channel/UCabcdefghijklmnopqrstuv', 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live'),
                        ('', '')]:
        cs.settings['youtube_channel'] = given
        d = c.get('/api/stream_link').get_json()
        assert d['url'] == want and d['live'] is False
    cs.settings['youtube_channel'] = ''


def test_break_slide_manual_and_auto(monkeypatch):
    c = cs.app.test_client()
    cs.settings['break_slide'] = {'after': 8, 'auto': False, 'delay': 120}
    monkeypatch.setattr(cs, '_break_results', lambda: {'meet': {'name': 'Fremont @ Tooele'}, 'scores': [{'name': 'Tooele', 'total': 66}],
        'events': [{'no': 1, 'label': 'Girls 200 Medley Relay', 'rows': [{'place': 1, 'name': 'Tooele "A"', 'team': 'TOOEL', 'result': '2:13.18'}]},
                   {'no': 9, 'label': 'Girls 100 Fly', 'rows': []}]})
    d = c.get('/api/break_slide').get_json()
    assert d['on'] is False and d['auto'] is False and d['after'] == 8
    d = c.post('/api/break_slide', json={'on': True}).get_json()
    assert d['on'] and d['scores'][0]['total'] == 66 and d['done'][0]['top'][0]['time'] == '2:13.18' and d['coming'][0]['no'] == 9
    cs._race_started()                                   # the next race takes it down
    assert c.get('/api/break_slide').get_json()['on'] is False
    # automatic: only after the last heat of the 'after' event
    c.post('/api/break_slide', json={'auto': True, 'delay': 0, 'after': 8})
    monkeypatch.setattr(cs.meet_board, 'heat_count', lambda ei, e: 2)
    cs._break_race_over('8', '1')
    assert cs.BREAK['timer'] is None
    cs._break_race_over('8', '2')
    import time; time.sleep(0.3)
    assert cs.BREAK['on'] is True
    cs._break_set(False)
    cs.settings['break_slide'] = {'after': 8, 'auto': False, 'delay': 120}
