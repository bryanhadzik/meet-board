"""School logos for the meet board and overlay.

Logos are files in the "logos" folder next to meet-board.exe, named by the
Meet Manager team code: TOOEL.png, SHS.png, ... Upload them on the Team Colors
page (single images or a .zip). They are not shipped in the repo: they are the
schools' marks, used only on the pool's own displays.
"""
import io
import os
import re
import zipfile

LOGO_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg')
MAX_BYTES = 2 * 1024 * 1024
_CODE = re.compile(r'^[A-Z0-9]{1,8}$')


def logo_dir(data_path):
    d = data_path('logos')
    os.makedirs(d, exist_ok=True)
    return d


def code_from_filename(name):
    """'tooel.PNG' -> 'TOOEL'; anything that isn't CODE.ext -> None."""
    base, ext = os.path.splitext(os.path.basename(name or ''))
    code = base.strip().upper()
    if ext.lower() not in LOGO_EXT or not _CODE.match(code):
        return None
    return code


def valid_code(code):
    return bool(_CODE.match((code or '').upper()))


def find_logo(folder, code):
    """Path of the logo file for a team code, or None."""
    code = (code or '').upper()
    if not valid_code(code):
        return None
    try:
        for n in os.listdir(folder):
            if code_from_filename(n) == code:
                return os.path.join(folder, n)
    except OSError:
        pass
    return None


def list_logos(folder):
    """{code: mtime_int} for every logo; the mtime busts browser caches."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        code = code_from_filename(n)
        if code:
            try:
                out[code] = int(os.path.getmtime(os.path.join(folder, n)))
            except OSError:
                pass
    return out


def save_logo(folder, code, ext, data):
    """Replace a team's logo (any old file for that code is removed)."""
    code = code.upper()
    ext = ext.lower()
    if not valid_code(code) or ext not in LOGO_EXT:
        raise ValueError('logo files must be named CODE.png (or .jpg/.webp/.gif/.svg)')
    if len(data) > MAX_BYTES:
        raise ValueError('%s is over 2 MB' % code)
    remove_logo(folder, code)
    with open(os.path.join(folder, code + ext), 'wb') as f:
        f.write(data)


def remove_logo(folder, code):
    while True:
        p = find_logo(folder, code)
        if not p:
            return
        os.remove(p)


def save_uploads(folder, uploads):
    """uploads: [(filename, bytes)], images and/or .zip files of images.
    Returns (saved_codes, skipped_names)."""
    saved, skipped = [], []

    def one(name, data):
        code = code_from_filename(name)
        if not code:
            skipped.append(os.path.basename(name))
            return
        save_logo(folder, code, os.path.splitext(name)[1], data)
        saved.append(code)

    for name, data in uploads:
        if name.lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir() or os.path.basename(info.filename).startswith(('.', '__MACOSX')):
                        continue
                    one(info.filename, z.read(info))
        else:
            one(name, data)
    return saved, skipped
