"""Inverted CTS signal (Gen7 console, captured at 9600-8E1): decode in software."""
import os

import cts_signal
from serial_monitor import decode_record, is_valid_record

CAP = os.path.join(os.path.dirname(__file__), '..', 'samples', 'console-gen7-inverted-8E1-INV.bin')


def records(raw, inverted):
    rf = cts_signal.Reframer(inverted)
    out, l = [], []
    for b in raw:
        for c in rf.feed(b):
            if (c & 0x80) or len(l) > 8:
                if l:
                    out.append(l)
                l = []
            l.append(c)
    return out


def test_inverted_capture_decodes():
    raw = open(CAP, 'rb').read()
    good = records(raw, True)
    texts = [decode_record(r) for r in good]
    assert 'race time 0.0' in texts                      # console in Reset: clock at zero
    assert 'event 1, heat 1' in texts
    assert sum(map(is_valid_record, good)) / len(good) > 0.85   # same as a clean recording (~90%)
    # read straight (not inverted), the same bytes are mostly junk
    bad = records(raw, False)
    assert sum(map(is_valid_record, bad)) / len(bad) < 0.5


def test_reframer_roundtrip_on_clean_stream():
    """Invert a clean recording the way the wire does, then undo it."""
    clean = [b for b in open(os.path.join(os.path.dirname(__file__), '..', 'samples', 'meet.bin'), 'rb').read()[:4000] if b]
    wire = [(~((b << 1) | 0) & 0xFF) | 1 for b in clean]   # shifted, inverted, bit 0 always 1
    rf = cts_signal.Reframer(True)
    back = [c for w in wire for c in rf.feed(w)]
    assert sum(a == b for a, b in zip(back, clean)) / len(clean) > 0.95


def test_format_names():
    assert cts_signal.is_inverted('9600-8E1-INV') and not cts_signal.is_inverted('9600-8E1')
    assert cts_signal.base_format('9600-8E1-INV') == '9600-8E1'
