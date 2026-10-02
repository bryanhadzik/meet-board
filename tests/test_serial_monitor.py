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
