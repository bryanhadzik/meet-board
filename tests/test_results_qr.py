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
