"""Minimal obs-websocket v5 client for meet-board: stream start/stop + status.

Port of the design in the project doc "OBS stream control" (written for the
old Node/Docker board) to this Python app. The load-bearing rules carry over:

  - It NEVER throws into the board's main path. If OBS is closed, crashed or
    the password is wrong, the boards and the overlay keep working and the
    status just reads link "down".
  - It reconnects forever, every 5 s - including when meet-board starts before
    OBS, which is the normal case at boot (the Node draft's bug: a refused
    first connection never retried).
  - Requests time out at 5 s rather than hanging an HTTP request.

OBS side: Tools > WebSocket Server Settings > Enable WebSocket server, port
4455, set a password. meet-board side: Settings > OBS (obs_url, obs_password).
"""
import base64
import collections
import hashlib
import itertools
import json
import socket
import threading
import time
from urllib.parse import urlsplit

RECONNECT_S = 5.0
REQUEST_TIMEOUT_S = 5.0
POLL_S = 2.0


def _sha256b64(s):
    return base64.b64encode(hashlib.sha256(s.encode('utf-8')).digest()).decode('ascii')


DEFAULT_URL = 'ws://127.0.0.1:4455'

# obs-websocket v5 close codes (WebSocketCloseCode) that explain a refusal
CLOSE_CODES = {
    4000: 'unknown reason',
    4002: 'message could not be decoded',
    4003: 'missing a required field',
    4004: 'invalid data type',
    4005: 'unknown OpCode',
    4006: 'sent a message before identifying',
    4007: 'already identified',
    4008: 'identify took too long',
    4009: 'authentication failed - wrong password',
    4010: 'unsupported RPC version',
    4011: 'session invalidated (kicked from OBS)',
    4012: 'unsupported feature',
}


def normalize_url(url):
    """'localhost', '192.168.1.5:4455', 'ws://host:4455' -> 'ws://host:port/'.
    The trailing '/' matters: without a path the WebSocket request line is
    'GET  HTTP/1.1', which the client library rejects ('Illegal target
    characters') - that was 0.2.9's connection bug with the default URL."""
    u = (url or '').strip() or DEFAULT_URL
    if '://' not in u:
        u = 'ws://' + u
    parts = urlsplit(u)
    scheme = parts.scheme.lower()
    if scheme in ('http', 'https'):
        scheme = 'ws' if scheme == 'http' else 'wss'
    host = parts.hostname or '127.0.0.1'
    try:
        port = parts.port or 4455
    except ValueError:
        port = 4455
    if ':' in host:                     # IPv6 literal
        host = '[%s]' % host
    path = parts.path or '/'
    return '%s://%s:%d%s' % (scheme, host, port, path)


def explain(exc):
    """(message, hint) for a connection failure, in plain words."""
    code = getattr(exc, 'reason', None)
    if isinstance(code, int) and code in CLOSE_CODES:
        msg = 'OBS closed the connection: %s (code %d)' % (CLOSE_CODES[code], code)
        hint = ('Re-type the password on this page to match OBS > Tools > WebSocket Server Settings > Show Connect Info.'
                if code == 4009 else '')
        return msg, hint
    if isinstance(exc, ConnectionRefusedError):
        return ('Connection refused - nothing is listening on that port',
                'Is OBS running? In OBS: Tools > WebSocket Server Settings > Enable WebSocket server, port 4455.')
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ('Timed out connecting',
                'Wrong IP address, or a firewall is blocking the port. If OBS is on this PC use 127.0.0.1.')
    if isinstance(exc, socket.gaierror):
        return ('Host name not found', 'Use an IP address such as 127.0.0.1 (same PC) or the OBS PC\'s LAN address.')
    text = str(exc) or exc.__class__.__name__
    if 'Illegal target' in text:
        return ('Bad WebSocket URL (%s)' % text, 'Use ws://127.0.0.1:4455/')
    return (text, '')


def auth_response(password, salt, challenge):
    """obs-websocket v5 authentication string."""
    return _sha256b64(_sha256b64(password + salt) + challenge)


class OBSClient:
    def __init__(self, get_config, ws_factory=None, log=print):
        """get_config() -> (url, password), read on every (re)connect so a
        change on the Settings page takes effect without a restart.
        ws_factory(url) -> object with send(str), receive(timeout), close();
        defaults to simple_websocket.Client (already a dependency)."""
        self._get_config = get_config
        self._ws_factory = ws_factory
        self._log = log
        self._lock = threading.Lock()
        self._ws = None
        self._seq = itertools.count(1)
        self._pending = {}                 # requestId -> [event, response]
        self._last_bytes = 0
        self._last_bytes_at = 0.0
        self._started = False
        self._wake = threading.Event()     # reconnect now (settings changed)
        self.state = {'link': 'down', 'streaming': False, 'recording': False,
                      'seconds': 0, 'kbps': 0, 'droppedPct': 0.0,
                      'error': None, 'hint': '', 'url': '', 'attempts': 0,
                      'last_attempt': None, 'connected_since': None,
                      'obs_version': '', 'ws_version': '', 'auth_required': None}
        self.events = collections.deque(maxlen=60)     # (epoch, text) connection log

    def note(self, text):
        self.events.append((time.time(), text))

    def diagnostics(self):
        return {'status': self.status(),
                'log': [{'t': time.strftime('%H:%M:%S', time.localtime(t)), 'msg': m} for t, m in list(self.events)]}

    # ------------------------------------------------------------ public
    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, name='obs-link', daemon=True).start()
        threading.Thread(target=self._poll_loop, name='obs-poll', daemon=True).start()

    def status(self):
        with self._lock:
            return dict(self.state)

    def reconnect(self):
        """Drop the link and reconnect with fresh settings."""
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._wake.set()

    def start_stream(self):
        return self.call('StartStream')

    def stop_stream(self):
        return self.call('StopStream')

    def call(self, request_type, request_data=None, timeout=REQUEST_TIMEOUT_S):
        """Send a request, wait for its response. Raises RuntimeError on
        not connected, timeout, or OBS rejecting it."""
        ws = self._ws
        if self.state['link'] != 'up' or ws is None:
            raise RuntimeError('OBS is not connected')
        rid = 'r%d' % next(self._seq)
        slot = [threading.Event(), None]
        self._pending[rid] = slot
        msg = {'op': 6, 'd': {'requestType': request_type, 'requestId': rid}}
        if request_data is not None:
            msg['d']['requestData'] = request_data
        try:
            ws.send(json.dumps(msg))
        except Exception:
            self._pending.pop(rid, None)
            raise RuntimeError('OBS disconnected')
        if not slot[0].wait(timeout):
            self._pending.pop(rid, None)
            raise RuntimeError('OBS request timed out')
        d = slot[1]
        if d is None:
            raise RuntimeError('OBS disconnected')
        status = d.get('requestStatus') or {}
        if status.get('result'):
            return d.get('responseData') or {}
        raise RuntimeError(status.get('comment') or 'OBS rejected the request (code %s)' % status.get('code'))

    # ------------------------------------------------------------ link
    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _go_down(self, why):
        if self.state['link'] != 'down':
            self._log('[obs] link down - %s' % why)
            self.note('Disconnected: %s' % why)
        self._ws = None
        self._set(link='down', streaming=False, recording=False, kbps=0, connected_since=None)
        for rid, slot in list(self._pending.items()):
            slot[1] = None
            slot[0].set()
        self._pending.clear()

    def _factory(self, url):
        if self._ws_factory:
            return self._ws_factory(url)
        import simple_websocket
        return simple_websocket.Client(url)

    def _run(self):
        while True:
            try:
                self._session()
            except Exception as e:           # refused, reset, bad handshake...
                msg, hint = explain(e)
                if self.state.get('error') != msg:
                    self.note('Connect failed: %s' % msg)
                self._set(error=msg, hint=hint)
                self._go_down(msg)
            # Always come back around: this is what the Node draft got wrong.
            self._wake.wait(RECONNECT_S)
            self._wake.clear()

    def _session(self):
        url, password = self._get_config()
        url = normalize_url(url)
        with self._lock:
            self.state['url'] = url
            self.state['attempts'] += 1
            self.state['last_attempt'] = time.strftime('%H:%M:%S')
        ws = self._factory(url)
        try:
            self._handshake(ws, password or '')
            self._ws = ws
            self._last_bytes, self._last_bytes_at = 0, 0.0
            self._set(link='up', error=None, hint='', connected_since=time.strftime('%H:%M:%S'))
            self._log('[obs] connected to %s' % url)
            self.note('Connected to %s' % url)
            threading.Thread(target=self._refresh, daemon=True).start()
            threading.Thread(target=self._fetch_version, daemon=True).start()
            while True:
                raw = ws.receive(timeout=30)
                if raw is None:
                    continue                 # idle; OBS sends events when things change
                self._on_message(raw)
        finally:
            try:
                ws.close()
            except Exception:
                pass
            if self._ws is ws or self._ws is None:
                self._go_down('socket closed')

    def _handshake(self, ws, password):
        hello = self._recv_json(ws, REQUEST_TIMEOUT_S)
        if not hello or hello.get('op') != 0:
            raise RuntimeError('not an obs-websocket v5 server (no Hello)')
        d = {'rpcVersion': 1, 'eventSubscriptions': 1 | 64}   # General + Outputs
        hd = hello.get('d') or {}
        auth = hd.get('authentication')
        self._set(ws_version=hd.get('obsWebSocketVersion', ''), auth_required=bool(auth))
        if auth:
            if not password:
                raise NeedsPassword()
            d['authentication'] = auth_response(password, auth['salt'], auth['challenge'])
        ws.send(json.dumps({'op': 1, 'd': d}))
        ident = self._recv_json(ws, REQUEST_TIMEOUT_S)
        if not ident or ident.get('op') != 2:
            raise WrongPassword()

    @staticmethod
    def _recv_json(ws, timeout):
        try:
            raw = ws.receive(timeout=timeout)
        except Exception as e:
            code = getattr(e, 'reason', None)
            if code == 4009:
                raise WrongPassword()
            raise
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        op, d = msg.get('op'), msg.get('d') or {}
        if op == 5:                                   # Event: instant UI updates
            et, data = d.get('eventType'), d.get('eventData') or {}
            if et == 'StreamStateChanged':
                self._set(streaming=bool(data.get('outputActive')))
            elif et == 'RecordStateChanged':
                self._set(recording=bool(data.get('outputActive')))
        elif op == 7:                                 # RequestResponse
            slot = self._pending.pop(d.get('requestId'), None)
            if slot:
                slot[1] = d
                slot[0].set()

    def _fetch_version(self):
        try:
            v = self.call('GetVersion')
            self._set(obs_version=v.get('obsVersion', ''))
        except Exception:
            pass

    # ------------------------------------------------------------ polling
    def _poll_loop(self):
        while True:
            time.sleep(POLL_S)
            if self.state['link'] == 'up':
                self._refresh()

    def _refresh(self):
        try:
            s = self.call('GetStreamStatus')
            now = time.time()
            byts = s.get('outputBytes') or 0
            kbps = self.state['kbps']
            if self._last_bytes_at and byts >= self._last_bytes:
                dt = now - self._last_bytes_at
                if dt > 0.5:
                    kbps = int(round((byts - self._last_bytes) * 8 / 1000.0 / dt))
            self._last_bytes, self._last_bytes_at = byts, now
            total = s.get('outputTotalFrames') or 0
            skipped = s.get('outputSkippedFrames') or 0
            self._set(streaming=bool(s.get('outputActive')),
                      seconds=int((s.get('outputDuration') or 0) / 1000),
                      kbps=kbps if s.get('outputActive') else 0,
                      droppedPct=round(skipped * 100.0 / total, 2) if total else 0.0)
        except Exception:
            pass                                      # one failed poll is not an outage
        try:
            r = self.call('GetRecordStatus')
            self._set(recording=bool(r.get('outputActive')))
        except Exception:
            pass


class NeedsPassword(RuntimeError):
    reason = None

    def __str__(self):
        return 'OBS requires a password - enter it on the Settings page'


class WrongPassword(RuntimeError):
    reason = 4009

    def __str__(self):
        return 'wrong password'


# ---------------------------------------------------------------- one-shot test
def diagnose(url, password, ws_factory=None, tcp_timeout=3.0):
    """Walk the connection one step at a time and say where it breaks.
    Independent of the background link. Returns a list of
    {step, ok, detail, hint}; stops at the first failure."""
    steps = []

    def add(step, ok, detail='', hint=''):
        steps.append({'step': step, 'ok': ok, 'detail': detail, 'hint': hint})
        return ok

    raw = (url or '').strip()
    norm = normalize_url(raw)
    parts = urlsplit(norm)
    host, port = parts.hostname, parts.port
    add('URL', True, norm + ('' if raw == norm else '  (entered: %s)' % (raw or 'blank - using default')))

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = sorted({i[4][0] for i in infos})
        add('Resolve host', True, ', '.join(addrs))
    except socket.gaierror as e:
        add('Resolve host', False, str(e), 'Use an IP address, e.g. 127.0.0.1 when OBS is on this PC.')
        return steps

    try:
        with socket.create_connection((host, port), timeout=tcp_timeout):
            pass
        add('TCP connect to port %d' % port, True, 'something is listening')
    except ConnectionRefusedError:
        add('TCP connect to port %d' % port, False, 'refused - nothing is listening',
            'Start OBS, then Tools > WebSocket Server Settings > tick "Enable WebSocket server" and check the port '
            '(4455). If OBS is on another PC, allow OBS through that PC\'s Windows Firewall.')
        return steps
    except (socket.timeout, TimeoutError):
        add('TCP connect to port %d' % port, False, 'timed out after %.0f s' % tcp_timeout,
            'Wrong IP, the other PC is off, or a firewall is dropping the port.')
        return steps
    except OSError as e:
        add('TCP connect to port %d' % port, False, str(e), '')
        return steps

    try:
        if ws_factory:
            ws = ws_factory(norm)
        else:
            import simple_websocket
            ws = simple_websocket.Client(norm)
        add('WebSocket handshake', True, 'upgraded')
    except Exception as e:
        add('WebSocket handshake', False, str(e) or e.__class__.__name__,
            'The port answered but not as a WebSocket - is something else using %d?' % port)
        return steps

    try:
        hello = OBSClient._recv_json(ws, REQUEST_TIMEOUT_S)
        if not hello or hello.get('op') != 0:
            add('obs-websocket Hello', False, 'no Hello within %d s' % REQUEST_TIMEOUT_S,
                'This is not obs-websocket v5 (OBS 28 or newer is required).')
            return steps
        hd = hello.get('d') or {}
        auth = hd.get('authentication')
        add('obs-websocket Hello', True, 'obs-websocket %s, rpc %s, %s' % (
            hd.get('obsWebSocketVersion', '?'), hd.get('rpcVersion', '?'),
            'password required' if auth else 'no password required'))
        d = {'rpcVersion': 1, 'eventSubscriptions': 0}
        if auth:
            if not password:
                add('Authenticate', False, 'OBS wants a password and none is saved here',
                    'OBS > Tools > WebSocket Server Settings > Show Connect Info shows the password. Enter it above and Save.')
                return steps
            d['authentication'] = auth_response(password, auth['salt'], auth['challenge'])
        ws.send(json.dumps({'op': 1, 'd': d}))
        try:
            ident = OBSClient._recv_json(ws, REQUEST_TIMEOUT_S)
        except WrongPassword:
            ident = None
        if not ident or ident.get('op') != 2:
            add('Authenticate', False, 'OBS rejected the password (close code 4009)' if auth else 'identify failed',
                'Re-enter the password exactly as shown in OBS > Show Connect Info (it is case-sensitive).')
            return steps
        add('Authenticate', True, 'password accepted' if auth else 'no password needed')

        def req(rt):
            ws.send(json.dumps({'op': 6, 'd': {'requestType': rt, 'requestId': rt}}))
            end = time.time() + REQUEST_TIMEOUT_S
            while time.time() < end:
                m = OBSClient._recv_json(ws, 1)
                if m and m.get('op') == 7 and (m.get('d') or {}).get('requestId') == rt:
                    return m['d']
            return None

        v = req('GetVersion')
        if v and (v.get('requestStatus') or {}).get('result'):
            rd = v.get('responseData') or {}
            add('OBS version', True, 'OBS %s on %s' % (rd.get('obsVersion', '?'), rd.get('platformDescription', rd.get('platform', '?'))))
        ss = req('GetStreamServiceSettings')
        if ss and (ss.get('requestStatus') or {}).get('result'):
            rd = ss.get('responseData') or {}
            st = rd.get('streamServiceType', '?')
            sset = rd.get('streamServiceSettings') or {}
            has_key = bool(sset.get('key'))
            service = sset.get('service') or sset.get('server') or st
            add('Stream destination', has_key or (st == 'rtmp_custom' and bool(sset.get('server'))),
                '%s%s' % (service, '' if has_key else ' - no stream key set'),
                '' if has_key else 'Start stream will fail until OBS > Settings > Stream has a service and stream key.')
        s2 = req('GetStreamStatus')
        if s2 and (s2.get('requestStatus') or {}).get('result'):
            add('Stream status', True, 'LIVE' if (s2.get('responseData') or {}).get('outputActive') else 'not streaming')
    except Exception as e:
        msg, hint = explain(e)
        add('obs-websocket session', False, msg, hint)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return steps
