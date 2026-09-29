import CTS_Scoreboard as C


def test_admin_pages_need_no_login():
    client = C.app.test_client()
    for path in ('/settings', '/team_colors', '/software_update', '/combine_events', '/schedule_preview'):
        r = client.get(path)
        assert r.status_code == 200, path


def test_login_redirects_safely():
    client = C.app.test_client()
    assert client.get('/login?next=/team_colors').headers['Location'].endswith('/team_colors')
    assert client.get('/login?next=//evil.example').headers['Location'].endswith('/settings')
