"""Live diagnostics for the CTS serial input: is the port open, are bytes
arriving, do they look like CTS scoreboard data? Shown on Settings ->
Serial input, and at /api/serial."""
import collections
import threading
import time

# CTS channels (from the scoreboard protocol): 0 = running time / event-heat,
# 1-10 = lanes, 0x14/0x15 = team scores. Anything else is shown as "ch NN".
_CH_NAMES = {0: 'clock', 0x0C: 'event/heat', 0x14: 'scores', 0x15: 'scores'}


def channel_of(first_byte):
    return ((first_byte & 0x3E) >> 1) ^ 0x1F


class SerialMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset('')

    def reset(self, port, state='closed', source='serial'):
        with getattr(self, '_lock', threading.Lock()):
            self.port = port
            self.source = source          # serial | replay
            self.state = state            # closed | opening | open | error | replay
            self.error = ''
            self.opened_at = None
            self.bytes_total = 0
            self.high_bytes = 0           # bytes with the top bit set (CTS record starts)
            self.records_total = 0
            self.by_channel = collections.Counter()
            self.last_byte_at = None
            self.last_record_at = None
            self.last_clock_at = None     # last channel-0 (race time) record
            self.raw = collections.deque(maxlen=96)        # last raw bytes
            self.recent = collections.deque(maxlen=12)     # last decoded records
            self._rate = collections.deque()               # (t, nbytes) in the last 5 s
            self._rec_rate = collections.deque()           # t of records in the last 5 s
            self.attempts = 0

    # ---- called from the reader thread
    def opening(self, port):
        with self._lock:
            self.port, self.state, self.source = port, 'opening', 'serial'
            self.attempts += 1

    def opened(self, port):
        with self._lock:
            self.port, self.state, self.error = port, 'open', ''
            self.opened_at = time.time()

    def failed(self, port, err):
        with self._lock:
            self.port, self.state, self.error = port, 'error', str(err)

    def replay(self, name):
        with self._lock:
            self.port, self.state, self.source, self.error = name, 'replay', 'replay', ''
            self.opened_at = self.opened_at or time.time()

    def byte(self, c):
        now = time.time()
        with self._lock:
            self.bytes_total += 1
            if c & 0x80:
                self.high_bytes += 1
            self.last_byte_at = now
            self.raw.append(c)
            if self._rate and now - self._rate[-1][0] < 0.25:
                t, n = self._rate[-1]
                self._rate[-1] = (t, n + 1)
            else:
                self._rate.append((now, 1))

    def record(self, rec):
        if not rec:
            return
        now = time.time()
        ch = channel_of(rec[0])
        with self._lock:
            self.records_total += 1
            self.by_channel[ch] += 1
            self.last_record_at = now
            if ch == 0:
                self.last_clock_at = now
            self._rec_rate.append(now)
            self.recent.append({'t': round(now, 2), 'ch': ch, 'hex': ' '.join('%02X' % b for b in rec)})

    # ---- read side
    def snapshot(self):
        now = time.time()
        with self._lock:
            while self._rate and now - self._rate[0][0] > 5:
                self._rate.popleft()
            while self._rec_rate and now - self._rec_rate[0] > 5:
                self._rec_rate.popleft()
            bps = sum(n for _, n in self._rate) / 5.0
            rps = len(self._rec_rate) / 5.0
            idle = (now - self.last_byte_at) if self.last_byte_at else None
            chans = [{'ch': ch, 'name': _CH_NAMES.get(ch, 'lane %d' % ch if 1 <= ch <= 10 else 'ch %d' % ch), 'n': n}
                     for ch, n in sorted(self.by_channel.items())]
            snap = {
                'port': self.port, 'source': self.source, 'state': self.state, 'error': self.error,
                'attempts': self.attempts,
                'open_for': round(now - self.opened_at, 1) if self.opened_at and self.state in ('open', 'replay') else None,
                'bytes_total': self.bytes_total, 'records_total': self.records_total,
                'bytes_per_sec': round(bps, 1), 'records_per_sec': round(rps, 1),
                'idle_seconds': round(idle, 1) if idle is not None else None,
                'clock_age': round(now - self.last_clock_at, 1) if self.last_clock_at else None,
                'high_bit_pct': round(100.0 * self.high_bytes / self.bytes_total, 1) if self.bytes_total else None,
                'channels': chans,
                'raw_hex': ['%02X' % b for b in self.raw],
                'recent': list(self.recent)[::-1],
            }
        snap['verdict'], snap['hint'] = self._verdict(snap)
        return snap

    @staticmethod
    def _verdict(s):
        """(ok | warn | bad, plain-English explanation) for the Settings card."""
        if s['source'] == 'replay':
            return 'ok', 'Replaying a recorded file (started with --in), not the serial port.'
        if not s['port']:
            return 'bad', 'No serial port selected. Pick the USB serial adapter in Serial Port above and save.'
        if s['state'] == 'error':
            return 'bad', ("Can't open %s: %s. Check the USB-serial adapter is plugged in and the port number is right "
                           "(Windows Device Manager -> Ports). Retrying every 5 s." % (s['port'], s['error']))
        if s['state'] in ('closed', 'opening'):
            return 'warn', 'Opening %s...' % s['port']
        if not s['bytes_total']:
            return 'bad', ('%s is open but nothing has arrived. Is the console on and sending to the scoreboard? '
                           'Is the Y-cable in line with the scoreboard cable, and the DB-9 wired center->pin 2, '
                           'shield->pin 5?' % s['port'])
        if s['idle_seconds'] is not None and s['idle_seconds'] > 10:
            return 'warn', ('Data was arriving but has stopped for %d s. The console may be off or between sessions, '
                            'or the cable came loose.' % s['idle_seconds'])
        if s['bytes_total'] > 200 and not s['records_total']:
            return 'bad', ('Bytes are arriving but none look like CTS scoreboard data. Wrong device on this port, '
                           'or the wrong baud rate (CTS is 9600).')
        if s['bytes_total'] > 200 and s['high_bit_pct'] is not None and s['high_bit_pct'] < 3:
            return 'warn', ('Bytes are arriving but very few look like CTS record starts (%.1f%%). Possible noise, '
                            'wrong baud rate, or a ground (pin 5) problem.' % s['high_bit_pct'])
        return 'ok', 'Receiving CTS data from %s.' % s['port']


monitor = SerialMonitor()
