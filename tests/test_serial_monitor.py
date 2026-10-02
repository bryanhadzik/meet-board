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
