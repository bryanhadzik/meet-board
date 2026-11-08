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


def _ca_and_leaf(ips):
    import datetime, ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    cak = ec.generate_private_key(ec.SECP256R1())
    can = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test Home Lab CA')])
    ca = (x509.CertificateBuilder().subject_name(can).issuer_name(can).public_key(cak.public_key())
          .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(cak, hashes.SHA256()))
    lk = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'meet-board-pool')]))
            .issuer_name(can).public_key(lk.public_key()).serial_number(2)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
            .sign(cak, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    key = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem(ca), pem(leaf), key(lk), key(cak)


def test_install_ca_cert_and_serve_it(tmp_path):
    folder = str(tmp_path / 'https')
    port = _free_port()
    https_server.STATE.update(port=0, error='')
    https_server.start(C.app, port, folder)            # starts with the made-up cert
    assert https_server.cert_info(folder)['self_signed'] is True
    ca, leaf, key, other_key = _ca_and_leaf(['127.0.0.1'])
    try:
        https_server.install(folder, leaf, other_key, ca)
        assert False, 'mismatched key accepted'
    except ValueError:
        pass
    assert https_server.cert_info(folder)['self_signed'] is True     # unchanged after a bad install
    info = https_server.install(folder, leaf, key, ca)
    assert info['self_signed'] is False and info['issuer'] == 'Test Home Lab CA' and '127.0.0.1' in info['names']
    assert open(os.path.join(folder, 'ca.pem'), 'rb').read() == ca
    # the restarted listener now serves the CA-signed cert, and it verifies against the CA
    cafile = tmp_path / 'ca.pem'; cafile.write_bytes(ca)
    ctx = ssl.create_default_context(cafile=str(cafile))
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT        # the throwaway test CA is minimal; a real CA's certs are complete
    body, err = '', None
    for _ in range(50):
        try:
            body = urllib.request.urlopen('https://127.0.0.1:%d/listen' % port, context=ctx, timeout=2).read().decode()
            break
        except OSError as e:
            err = e; time.sleep(0.1)
    assert 'Listen from the stands' in body, err


def test_cert_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(C, '_https_folder', lambda: str(tmp_path / 'h2'))
    client = C.app.test_client()
    ca, leaf, key, _ = _ca_and_leaf(['127.0.0.1'])
    r = client.post('/https/cert', data={'cert': leaf.decode(), 'key': key.decode(), 'ca': ca.decode()})
    assert r.status_code == 200 and r.get_json()['issuer'] == 'Test Home Lab CA', r.get_data(as_text=True)
    assert client.get('/https/ca.crt').data == ca
    assert client.get('/https/info').get_json()['present'] is True
    assert client.post('/https/cert', data={'cert': leaf.decode()}).status_code == 400
