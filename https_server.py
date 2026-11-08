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
    threading.Thread(target=srv.serve_forever, name='https', daemon=True).start()
    print('  Phone mic:  https://<this-pc>:%d/listen' % int(port))
