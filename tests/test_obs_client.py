"""obs_client against a fake OBS that speaks obs-websocket v5 (incl. auth)."""
import json
import queue
import time

import pytest

import obs_client

PASSWORD = 'pool-secret'
SALT, CHALLENGE = 'c2FsdA==', 'Y2hhbGxlbmdl'


class FakeOBS:
    """One fake OBS process; each connect() gives a new FakeSocket."""
    def __init__(self, password=PASSWORD, refuse_first=0):
        self.password = password
        self.refuse_left = refuse_first
        self.attempts = 0
        self.streaming = False
        self.sockets = []

    def connect(self, url):
        self.attempts += 1
        if self.refuse_left > 0:
            self.refuse_left -= 1
            raise ConnectionRefusedError('[Errno 111] Connection refused')
        s = FakeSocket(self)
        self.sockets.append(s)
        return s


class FakeSocket:
    def __init__(self, obs):
        self.obs = obs
        self.out = queue.Queue()
        self.closed = False
        hello = {'rpcVersion': 1}
        if obs.password:
            hello['authentication'] = {'salt': SALT, 'challenge': CHALLENGE}
        self.out.put(json.dumps({'op': 0, 'd': hello}))

    def receive(self, timeout=None):
        if self.closed:
            raise ConnectionError('closed')
        try:
            m = self.out.get(timeout=min(timeout or 1, 0.2))
        except queue.Empty:
            if self.closed:
                raise ConnectionError('closed')
            return None
        if m is None:
            raise ConnectionError('closed')
        return m

    def send(self, raw):
        if self.closed:
            raise ConnectionError('closed')
        msg = json.loads(raw)
        d = msg['d']
        if msg['op'] == 1:
            if self.obs.password:
                want = obs_client.auth_response(self.obs.password, SALT, CHALLENGE)
                if d.get('authentication') != want:
                    self.close()
                    return
            self.out.put(json.dumps({'op': 2, 'd': {'negotiatedRpcVersion': 1}}))
        elif msg['op'] == 6:
            rt, rid = d['requestType'], d['requestId']
            ok, data = True, {}
            if rt == 'GetStreamStatus':
                data = {'outputActive': self.obs.streaming, 'outputDuration': 65000,
                        'outputBytes': 1000000, 'outputTotalFrames': 1000, 'outputSkippedFrames': 3}
            elif rt == 'GetRecordStatus':
                data = {'outputActive': False}
            elif rt == 'StartStream':
                if self.obs.streaming:
                    ok = False
                else:
                    self.obs.streaming = True
                    self.out.put(json.dumps({'op': 5, 'd': {'eventType': 'StreamStateChanged',
                                                             'eventData': {'outputActive': True}}}))
            elif rt == 'StopStream':
                self.obs.streaming = False
                self.out.put(json.dumps({'op': 5, 'd': {'eventType': 'StreamStateChanged',
                                                         'eventData': {'outputActive': False}}}))
            status = {'result': ok, 'code': 100 if ok else 500}
            if not ok:
                status['comment'] = 'Stream already active'
            self.out.put(json.dumps({'op': 7, 'd': {'requestType': rt, 'requestId': rid,
                                                     'requestStatus': status, 'responseData': data}}))

    def close(self):
        self.closed = True
        self.out.put(None)


def wait_for(pred, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(obs_client, 'RECONNECT_S', 0.1)
    monkeypatch.setattr(obs_client, 'POLL_S', 0.1)


def make(fake, password=PASSWORD):
    c = obs_client.OBSClient(lambda: ('ws://fake:4455', password), ws_factory=fake.connect, log=lambda *a: None)
    c.start()
    return c


def test_auth_handshake_start_stop_and_stats():
    fake = FakeOBS()
    c = make(fake)
    assert wait_for(lambda: c.status()['link'] == 'up')
    c.start_stream()
    assert wait_for(lambda: c.status()['streaming'])
    assert wait_for(lambda: c.status()['seconds'] == 65 and c.status()['droppedPct'] == 0.3)
    with pytest.raises(RuntimeError, match='already active'):
        c.start_stream()
    c.stop_stream()
    assert wait_for(lambda: not c.status()['streaming'])


def test_boot_before_obs_keeps_retrying():
    # The regression from the Node draft: OBS not running yet at boot.
    fake = FakeOBS(refuse_first=5)
    c = make(fake)
    assert wait_for(lambda: c.status()['link'] == 'up', timeout=5)
    assert fake.attempts >= 6


def test_obs_dies_and_comes_back():
    fake = FakeOBS()
    c = make(fake)
    assert wait_for(lambda: c.status()['link'] == 'up')
    fake.sockets[-1].close()
    assert wait_for(lambda: c.status()['link'] == 'down' or len(fake.sockets) > 1)
    assert wait_for(lambda: c.status()['link'] == 'up' and len(fake.sockets) > 1)


def test_wrong_or_missing_password_stays_down_without_raising():
    fake = FakeOBS()
    c = make(fake, password='nope')
    time.sleep(0.5)
    assert c.status()['link'] == 'down'
    with pytest.raises(RuntimeError, match='not connected'):
        c.start_stream()
    c2 = make(FakeOBS(), password='')
    assert wait_for(lambda: 'password' in (c2.status()['error'] or ''))


def test_no_password_server():
    c = make(FakeOBS(password=''), password='')
    assert wait_for(lambda: c.status()['link'] == 'up')


def test_routes_degrade_safely():
    import CTS_Scoreboard as C
    client = C.app.test_client()
    st = client.get('/api/obs').get_json()
    assert st['link'] == 'down' and 'obs_password' not in st
    r = client.post('/api/obs/stream', json={'action': 'start'})
    assert r.status_code == 502 and 'not connected' in r.get_json()['error']
    assert client.post('/api/obs/stream', json={'action': 'dance'}).status_code == 400


def test_blank_password_field_keeps_saved_password(monkeypatch):
    import CTS_Scoreboard as C
    monkeypatch.setattr(C, 'save_settings', lambda: None)
    monkeypatch.setattr(C.obs, 'reconnect', lambda: None)
    client = C.app.test_client()
    client.post('/settings', data={'obs_url_form': '1', 'obs_url': 'ws://127.0.0.1:4455', 'obs_password': 'abc'})
    assert C.settings['obs_password'] == 'abc'
    client.post('/settings', data={'obs_url_form': '1', 'obs_url': 'ws://127.0.0.1:4455', 'obs_password': ''})
    assert C.settings['obs_password'] == 'abc'
    client.post('/settings', data={'obs_url_form': '1', 'obs_url': 'ws://127.0.0.1:4455', 'obs_password': '',
                                   'obs_password_clear': 'on'})
    assert C.settings['obs_password'] == ''
