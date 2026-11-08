"""Phone in the stands: the HTTPS listener and the /listen page."""
import os, socket, ssl, sys, threading, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import CTS_Scoreboard as C
import https_server


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def test_cert_made_once(tmp_path):
    c1, k1 = https_server.ensure_cert(str(tmp_path))
    m = os.path.getmtime(c1)
    c2, k2 = https_server.ensure_cert(str(tmp_path))
    assert (c1, k1) == (c2, k2) and os.path.getmtime(c2) == m
    assert open(c1).read().startswith('-----BEGIN CERTIFICATE-----')


def test_https_listener_serves_listen_page(tmp_path):
    port = _free_port()
    https_server.start(C.app, port, str(tmp_path))
    assert https_server.STATE['port'] == port, https_server.STATE
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    for _ in range(50):
        try:
            body = urllib.request.urlopen('https://127.0.0.1:%d/listen' % port, context=ctx, timeout=2).read().decode()
            break
        except OSError:
            time.sleep(0.1)
    assert 'Listen from the stands' in body and 'HTTPS_PORT = %d' % port in body


def test_listener_failure_does_not_raise(tmp_path):
    s = socket.socket(); s.bind(('0.0.0.0', 0)); s.listen(1); busy = s.getsockname()[1]
    https_server.STATE.update(port=0, error='')
    https_server.start(C.app, busy, str(tmp_path))
    assert https_server.STATE['port'] == 0 and https_server.STATE['error']
    s.close()


def test_listen_report_relayed(monkeypatch):
    sent = []
    monkeypatch.setattr(C.socketio, 'emit', lambda ev, data, **kw: sent.append((ev, data)))
    C.ws_listen_report({'snr': 11.6, 'room_db': -52.2, 'speech_db': -40})
    ev, d = sent[-1]
    assert ev == 'listen_status' and d['snr'] == 12 and d['room_db'] == -52 and d['at']
    n = len(sent)
    C.ws_listen_report({'snr': 'x'})
    assert len(sent) == n
