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
import hashlib
import itertools
import json
import threading
import time

RECONNECT_S = 5.0
REQUEST_TIMEOUT_S = 5.0
POLL_S = 2.0


def _sha256b64(s):
    return base64.b64encode(hashlib.sha256(s.encode('utf-8')).digest()).decode('ascii')


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
                      'error': None, 'url': ''}

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
        self._ws = None
        self._set(link='down', streaming=False, recording=False, kbps=0)
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
                self._go_down(str(e) or e.__class__.__name__)
            # Always come back around: this is what the Node draft got wrong.
            self._wake.wait(RECONNECT_S)
            self._wake.clear()

    def _session(self):
        url, password = self._get_config()
        url = (url or 'ws://127.0.0.1:4455').strip()
        self._set(url=url)
        ws = self._factory(url)
        try:
            self._handshake(ws, password or '')
            self._ws = ws
            self._last_bytes, self._last_bytes_at = 0, 0.0
            self._set(link='up', error=None)
            self._log('[obs] connected to %s' % url)
            threading.Thread(target=self._refresh, daemon=True).start()
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
        auth = (hello.get('d') or {}).get('authentication')
        if auth:
            if not password:
                self._set(error='OBS requires a password - set it on the Settings page')
                raise RuntimeError('OBS requires a password')
            d['authentication'] = auth_response(password, auth['salt'], auth['challenge'])
        ws.send(json.dumps({'op': 1, 'd': d}))
        ident = self._recv_json(ws, REQUEST_TIMEOUT_S)
        if not ident or ident.get('op') != 2:
            self._set(error='OBS refused the connection - check the password')
            raise RuntimeError('identify failed (wrong password?)')

    @staticmethod
    def _recv_json(ws, timeout):
        raw = ws.receive(timeout=timeout)
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
