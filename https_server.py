"""A second, HTTPS listener for pages a phone opens that need its microphone.

Browsers only hand a page the microphone over HTTPS (or on localhost). The
pool PC has no real certificate, so meet-board makes its own self-signed one
on first start (https/cert.pem + key.pem next to the exe). The phone shows a
"not private" warning once; tap through it (Advanced -> Proceed) and the mic
works. Everything else keeps using plain http on the usual port.
"""
import datetime
import ipaddress
import os
import socket
import threading

try:                    # imported here (not inside ensure_cert) so PyInstaller bundles it
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
except ImportError:     # pragma: no cover - the listener just won't start
    x509 = None


def local_ips():
    ips = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    try:                                    # the address the LAN actually sees
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    ips.discard('0.0.0.0')
    return sorted(ips)


def ensure_cert(folder):
    """(cert_path, key_path), made once and reused."""
    os.makedirs(folder, exist_ok=True)
    cert, key = os.path.join(folder, 'cert.pem'), os.path.join(folder, 'key.pem')
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    if x509 is None:
        raise RuntimeError('the cryptography package is missing')
    pk = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or 'meet-board'
    alt = [x509.DNSName('localhost'), x509.DNSName(host)]
    for ip in set(local_ips()) | {'127.0.0.1'}:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'meet-board ' + host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1))
           .not_valid_after(now + datetime.timedelta(days=3650))
           .add_extension(x509.SubjectAlternativeName(alt), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .sign(pk, hashes.SHA256()))
    with open(key, 'wb') as f:
        f.write(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()))
    with open(cert, 'wb') as f:
        f.write(crt.public_bytes(serialization.Encoding.PEM))
    return cert, key


STATE = {'port': 0, 'error': ''}
_srv = {'server': None, 'app': None, 'folder': None}


def _load(cert_pem):
    certs = x509.load_pem_x509_certificates(cert_pem if isinstance(cert_pem, bytes) else cert_pem.encode())
    if not certs:
        raise ValueError('no certificate in that file')
    return certs


def cert_info(folder):
    """What the listener is serving: who it's for, who signed it, when it runs out."""
    path = os.path.join(folder, 'cert.pem')
    if x509 is None or not os.path.exists(path):
        return {'present': False}
    try:
        c = _load(open(path, 'rb').read())[0]
    except Exception as e:
        return {'present': True, 'error': str(e)}
    try:
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in san.get_values_for_type(x509.IPAddress)] + san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    cn = lambda n: (n.get_attributes_for_oid(NameOID.COMMON_NAME) or [None])[0]
    subj, iss = cn(c.subject), cn(c.issuer)
    end = c.not_valid_after_utc
    ips = local_ips()
    return {'present': True, 'subject': subj.value if subj else '', 'issuer': iss.value if iss else '',
            'self_signed': c.subject == c.issuer, 'expires': end.strftime('%Y-%m-%d'),
            'days_left': (end - datetime.datetime.now(datetime.timezone.utc)).days,
            'names': names, 'this_pc': ips, 'covers': [ip for ip in ips if ip in names]}


def install(folder, cert_pem, key_pem, ca_pem=None):
    """Use a real certificate (e.g. from the home-lab CA) instead of the made-up
    one. Checks the key belongs to the certificate before replacing anything."""
    if x509 is None:
        raise RuntimeError('the cryptography package is missing')
    certs = _load(cert_pem)
    key_b = key_pem if isinstance(key_pem, bytes) else key_pem.encode()
    try:
        pk = serialization.load_pem_private_key(key_b, password=None)
    except Exception:
        raise ValueError("that key file can't be read (it must be an unencrypted PEM key)")
    pub = lambda k: k.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    if pub(pk.public_key()) != pub(certs[0].public_key()):
        raise ValueError("the key doesn't belong to that certificate")
    chain = [certs[0]] + certs[1:]
    if ca_pem:
        for c in _load(ca_pem):
            if all(c.fingerprint(hashes.SHA256()) != x.fingerprint(hashes.SHA256()) for x in chain):
                chain.append(c)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'key.pem'), 'wb') as f:
        f.write(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()))
    with open(os.path.join(folder, 'cert.pem'), 'wb') as f:      # leaf first, then the CA (full chain)
        f.write(b''.join(c.public_bytes(serialization.Encoding.PEM) for c in chain))
    if len(chain) > 1:
        with open(os.path.join(folder, 'ca.pem'), 'wb') as f:
            f.write(chain[-1].public_bytes(serialization.Encoding.PEM))
    restart()
    return cert_info(folder)


def restart():
    """Pick up a new certificate without restarting meet-board."""
    srv, app, folder = _srv['server'], _srv['app'], _srv['folder']
    port = STATE.get('port')
    if srv is None or not port:
        return
    try:
        srv.shutdown(); srv.server_close()
    except Exception:
        pass
    _srv['server'] = None
    start(app, port, folder)


def start(app, port, folder):
    """Serve the same Flask app over HTTPS on `port`, in the background.
    Never raises: a failure only shows up in STATE['error']."""
    if not port:
        return
    try:
        cert, key = ensure_cert(folder)
        from werkzeug.serving import make_server
        srv = make_server('0.0.0.0', int(port), app, threaded=True, ssl_context=(cert, key))
    except (Exception, SystemExit) as e:    # port in use (werkzeug exits!), no cryptography, ...
        STATE.update(port=0, error=str(e))
        print('HTTPS listener not started:', e)
        return
    STATE.update(port=int(port), error='')
    _srv.update(server=srv, app=app, folder=folder)
    threading.Thread(target=srv.serve_forever, name='https', daemon=True).start()
    print('  Phone mic:  https://<this-pc>:%d/listen' % int(port))
