"""obs_client over real TCP against a fake obs-websocket v5 server.
Catches transport bugs the in-memory fake can't (0.2.9 failed with the
default URL because 'ws://127.0.0.1:4455' has no path)."""
import json
import socket
import threading
import time

import pytest
from websockets.sync.server import serve

import obs_client

PASSWORD = 'lanes'
SALT, CHALLENGE = 'c2FsdA==', 'Y2hhbGxlbmdl'


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def fake_obs():
    port = free_port()

    def handler(ws):
        ws.send(json.dumps({'op': 0, 'd': {'obsWebSocketVersion': '5.5.2', 'rpcVersion': 1,
                                           'authentication': {'salt': SALT, 'challenge': CHALLENGE}}}))
        for raw in ws:
            m = json.loads(raw)
            if m['op'] == 1:
                if m['d'].get('authentication') != obs_client.auth_response(PASSWORD, SALT, CHALLENGE):
                    ws.close(4009, 'Authentication failed.')
                    return
                ws.send(json.dumps({'op': 2, 'd': {'negotiatedRpcVersion': 1}}))
            elif m['op'] == 6:
                rt = m['d']['requestType']
                data = {'GetVersion': {'obsVersion': '31.0.2', 'platformDescription': 'Windows 11'},
                        'GetStreamServiceSettings': {'streamServiceType': 'rtmp_common',
                                                     'streamServiceSettings': {'service': 'YouTube - RTMPS', 'key': 'x'}},
                        'GetStreamStatus': {'outputActive': False}}.get(rt, {})
                ws.send(json.dumps({'op': 7, 'd': {'requestType': rt, 'requestId': m['d']['requestId'],
                                                   'requestStatus': {'result': True, 'code': 100}, 'responseData': data}}))

    server = serve(handler, '127.0.0.1', port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield 'ws://127.0.0.1:%d' % port        # NOTE: no trailing slash, like the default setting
    server.shutdown()


@pytest.mark.parametrize('raw,want', [
    ('', 'ws://127.0.0.1:4455/'), ('localhost', 'ws://localhost:4455/'),
    ('10.0.0.5:4455', 'ws://10.0.0.5:4455/'), ('ws://127.0.0.1:4455', 'ws://127.0.0.1:4455/'),
    ('http://10.0.0.5', 'ws://10.0.0.5:4455/')])
def test_normalize_url(raw, want):
    assert obs_client.normalize_url(raw) == want


def test_background_link_connects_with_url_without_slash(fake_obs):
    c = obs_client.OBSClient(lambda: (fake_obs, PASSWORD), log=lambda *a: None)
    c.start()
    end = time.time() + 5
    while time.time() < end and c.status()['link'] != 'up':
        time.sleep(0.05)
    st = c.status()
    assert st['link'] == 'up', st
    assert st['ws_version'] == '5.5.2' and st['auth_required'] is True
    assert any('Connected' in m for _, m in c.events)


def test_diagnose_all_steps_pass(fake_obs):
    steps = obs_client.diagnose(fake_obs, PASSWORD)
    assert all(s['ok'] for s in steps), steps
    names = [s['step'] for s in steps]
    assert 'Authenticate' in names and 'OBS version' in names and 'Stream destination' in names


def test_diagnose_wrong_password(fake_obs):
    steps = obs_client.diagnose(fake_obs, 'nope')
    assert steps[-1]['step'] == 'Authenticate' and not steps[-1]['ok']
    assert 'password' in steps[-1]['detail']


def test_diagnose_missing_password(fake_obs):
    steps = obs_client.diagnose(fake_obs, '')
    assert steps[-1]['step'] == 'Authenticate' and not steps[-1]['ok'] and 'none is saved' in steps[-1]['detail']


def test_diagnose_nothing_listening():
    steps = obs_client.diagnose('ws://127.0.0.1:%d' % free_port(), 'x')
    assert steps[-1]['step'].startswith('TCP connect') and not steps[-1]['ok']
    assert 'Enable WebSocket server' in steps[-1]['hint']


def test_background_link_explains_refusal():
    port = free_port()
    c = obs_client.OBSClient(lambda: ('ws://127.0.0.1:%d' % port, 'x'), log=lambda *a: None)
    c.start()
    time.sleep(0.5)
    st = c.status()
    assert st['link'] == 'down' and 'refused' in st['error'] and 'Enable WebSocket server' in st['hint']
