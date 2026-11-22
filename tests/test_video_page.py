import CTS_Scoreboard as C


def test_video_page_and_api():
    c = C.app.test_client()
    r = c.get('/video')
    assert r.status_code == 200 and b'Video timing' in r.data
    j = c.get('/api/video').get_json()
    assert 'enabled' in j and 'clips' in j
    j = c.post('/api/video', json={'geom': {'finish': [[0.1, i / 10] for i in range(9)], 'turn': [[0.9, i / 10] for i in range(9)]}}).get_json()
    assert len(j['geom']['finish']) == 9
    assert c.post('/api/video', json={'geom': {'finish': [[2, 2]]}}).status_code == 400
    assert c.get('/api/video/clip/../settings.json').status_code == 404


def test_mask():
    import video_timing
    assert video_timing.mask('rtsp://admin:pw@1.2.3.4:554/x') == 'rtsp://admin:***@1.2.3.4:554/x'
