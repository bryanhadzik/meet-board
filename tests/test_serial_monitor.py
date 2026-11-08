from serial_monitor import SerialMonitor, channel_of


def test_verdicts():
    m = SerialMonitor()
    m.reset('COM3'); m.failed('COM3', 'could not open port')
    v, hint = m._verdict(m.snapshot()); assert v == 'bad' and "Can't open COM3" in hint
    m.reset('COM3'); m.opened('COM3')
    v, hint = m._verdict(m.snapshot()); assert v == 'bad' and 'nothing has arrived' in hint
    for _ in range(300):                       # bytes, but no CTS records
        m.byte(0x41)
    assert m.snapshot()['verdict'] == 'bad'
    rec = [0xBE, 0x70, 0x6A, 0x5A, 0x40, 0x30, 0x20]   # a real clock record
    for _ in range(40):
        for b in rec:
            m.byte(b)
        m.record(rec)
    s = m.snapshot()
    assert s['verdict'] == 'ok' and s['records_total'] == 40 and s['channels'][0]['name'] == 'clock'
    assert s['raw_hex'][-1] == '20' and s['recent'][0]['ch'] == 0


def test_channel_decode():
    assert channel_of(0xBE) == 0       # clock
    assert channel_of(0xFB) == 2       # lane 2


def test_decode_record_words():
    from serial_monitor import decode_record
    assert decode_record([0xBE, 0x70, 0x6A, 0x5A, 0x40, 0x30, 0x20]) == 'race time 5.5'
    assert decode_record([0xFA, 0x0D, 0x10]) == 'lane 2: running'
    assert decode_record([0xFB, 0x0F]).endswith('display format/control')
    # lane 4 finished: place 2, 1:05.23  (digits are stored inverted)
    inv = lambda pos, d: (pos << 4) | (d ^ 0x0F)
    rec = [0xB6, inv(0, 4), inv(1, 2), 0x20, inv(3, 1), inv(4, 0), inv(5, 5), inv(6, 2), inv(7, 3)]
    assert decode_record(rec) == 'lane 4: place 2, time 1:05.23'


def test_detail_log_and_garbled_records(monkeypatch):
    import serial_monitor as sm
    now = [1000.0]
    monkeypatch.setattr(sm.time, 'time', lambda: now[0])
    m = SerialMonitor()
    m.reset('COM3')
    m.opening('COM3'); m.failed('COM3', OSError('could not open port'))
    m.opening('COM3'); m.failed('COM3', OSError('could not open port'))   # repeats fold together
    m.opening('COM3'); m.opened('COM3', {'baudrate': 9600, 'format': '9600-8N1'})
    m.chunk(7)
    for b in (0xBE, 0x70): m.byte(b)
    now[0] += 3.5                                     # a 3.5 s silence
    m.byte(0x6A)
    m.record([0xBE, 0x2A, 0x3F, 0x40])
    m.record([0x80, 0x1E, 0x0E])                      # positions out of order
    m.record([0x0E])                                  # no start byte
    d = m.detail()
    assert d['bad_total'] == 2 and 'out of order' in d['bad'][1]['why'] and 'no start byte' in d['bad'][0]['why']
    assert d['len_hist'] == {'1': 1, '3': 1, '4': 1} and d['max_chunk'] == 7 and d['gaps'] == 1 and d['max_gap'] == 3.5
    assert d['settings']['baudrate'] == 9600 and d['clock_trace'][0]['text'].startswith('race time')
    kinds = [e['kind'] for e in d['events']][::-1]
    assert kinds[:4] == ['reset', 'reset', 'opening', 'error'] and d['events'][-4]['n'] == 2 and 'open' in kinds and 'resumed' in kinds
    m.reset('COM3')                                   # the log survives a counter reset
    assert any(e['kind'] == 'error' for e in m.detail()['events'])
    now[0] += 10
    assert m.detail()['events'][0]['kind'] in ('reset', 'stalled')


def test_detail_endpoints():
    import CTS_Scoreboard as C
    c = C.app.test_client()
    d = c.get('/api/serial/detail').get_json()
    for k in ('events', 'bad', 'channels', 'clock_trace', 'race_book', 'versions', 'port_info'):
        assert k in d
    assert 'log' in d['race_book'] and 'racing' in d['race_book']
    r = c.get('/api/serial/debug.json')
    assert r.status_code == 200 and 'attachment' in r.headers['Content-Disposition']
    assert set(r.get_json()) == {'summary', 'detail', 'settings'}
