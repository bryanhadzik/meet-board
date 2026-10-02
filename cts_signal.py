"""Undo an inverted CTS scoreboard signal in software.

Some CTS consoles (seen on a Gen7, firmware 1.44) drive the scoreboard line
with the opposite polarity to what a PC serial port expects. The port then
frames each character on the wrong bit: every received byte is the real one
shifted left one bit, inverted, with bit 0 always 1:

    rx = ~((real << 1) | start_bit) & 0xFF,  i.e.  real & 0x7F == ~(rx >> 1) & 0x7F

Bit 7 of the real byte (set on the first byte of each CTS record) is lost
into the stop/parity slot. It's rebuilt from the record structure: a record
is a start byte plus up to 8 data bytes whose digit positions (bits 4-6) run
strictly up or strictly down. Tested on a clean console recording with bit 7
stripped: ~97% of bytes come back exactly; the rest are re-synced on the next
record.

The proper fix is hardware (an inverting adapter, or an FTDI USB-serial
adapter with RXD inverted in FT_PROG); this keeps the boards working meanwhile.
"""


def is_inverted(fmt):
    return (fmt or '').upper().endswith('-INV')


def base_format(fmt):
    return fmt[:-4] if is_inverted(fmt) else fmt


class Reframer:
    """feed(raw_byte) -> list of real CTS bytes (bit 7 restored)."""

    def __init__(self, inverted):
        self.inverted = inverted
        self._reset()

    def _reset(self):
        self.n_data = 0
        self.last = None        # last data position, -1 right after a start byte, None = need a start
        self.direction = 0

    def feed(self, rx):
        if not self.inverted:
            return [rx]
        if not rx & 1:
            # Not framed the expected way (gap or noise): drop it, resync on the next byte
            self._reset()
            return []
        b = (~(rx >> 1)) & 0x7F
        p = (b >> 4) & 7
        if self.last is None or self.n_data >= 8:
            start = True
        elif self.n_data == 0:
            start = False                      # a start byte is always followed by data
        elif self.direction == 0:
            start = p == self.last
        elif self.direction > 0:
            start = not p > self.last
        else:
            start = not p < self.last
        if start:
            self.n_data, self.last, self.direction = 0, -1, 0
            return [b | 0x80]
        if self.n_data >= 1:
            self.direction = 1 if p > self.last else -1
        self.n_data += 1
        self.last = p
        return [b]


def _clock_seconds(rec):
    """Race-time record -> seconds (None when blank or unreadable)."""
    pos = {}
    for b in rec[1:]:
        d = (b & 0x0F) ^ 0x0F
        pos[(b >> 4) & 7] = d if d <= 9 else None
    digits = [pos.get(i) for i in range(2, 8)]
    if all(d is None for d in digits):
        return None
    m10, m1, s10, s1, t, h = [d or 0 for d in digits]
    return (m10 * 10 + m1) * 60 + s10 * 10 + s1 + t / 10.0 + h / 100.0


class Confirmer:
    """Pass a CTS record to the parser only once it has been seen twice in a
    row for its channel. The console repeats every display line several times
    a second, so real values arrive at once; a one-off corrupted record (the
    inverted-signal decode rebuilds ~3% of bytes wrongly) never gets through.
    The race clock changes every tenth while running, so a clock record is
    also accepted when it is within 2 s of the last accepted clock."""

    CLOCK = 0

    def __init__(self):
        self.last = {}          # channel -> last record seen (bytes)
        self.clock_t = None

    def accept(self, rec):
        if not rec:
            return False
        c = rec[0]
        ch = ((c & 0x3E) >> 1) ^ 0x1F
        key = (ch, c & 0x01)                    # display-format records are their own stream
        prev = self.last.get(key)
        cur = tuple(rec)
        self.last[key] = cur
        if prev == cur:
            if ch == self.CLOCK and not c & 0x01:
                self.clock_t = _clock_seconds(rec)
            return True
        if ch == self.CLOCK and not c & 0x01:
            t = _clock_seconds(rec)
            if t is not None and self.clock_t is not None and abs(t - self.clock_t) <= 2.0:
                self.clock_t = t
                return True
        return False
