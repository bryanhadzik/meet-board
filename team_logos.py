"""School logos for the meet board and overlay.

Logos are files in the "logos" folder next to meet-board.exe, named by the
Meet Manager team code: TOOEL.png, SHS.png, ... Upload them on the Team Colors
page (single images or a .zip).

Built-in logos ship in static/logos and are copied into the logos folder the
first time a version that has them starts. Each one is copied once: a logo
someone uploaded is never replaced, and one they removed doesn't come back.
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
    """uploads: [(filename, bytes)] or [(filename, bytes, code)], images
    and/or .zip files of images. A code given with an image is the team it's
    saved as (so the file can be named anything); otherwise the file name is
    the code. Returns (saved_codes, skipped_names)."""
    saved, skipped = [], []

    def one(name, data, code=None):
        ext = os.path.splitext(name)[1].lower()
        code = (code or '').strip().upper() or code_from_filename(name)
        if not code or not valid_code(code) or ext not in LOGO_EXT:
            skipped.append(os.path.basename(name))
            return
        save_logo(folder, code, ext, data)
        saved.append(code)

    for item in uploads:
        name, data = item[0], item[1]
        code = item[2] if len(item) > 2 else None
        if name.lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir() or os.path.basename(info.filename).startswith(('.', '__MACOSX')):
                        continue
                    one(info.filename, z.read(info))
        else:
            one(name, data, code)
    return saved, skipped


# code -> sha256 of a built-in logo that no longer belongs to that code
RETIRED_BUILTIN = {
    'CCHS': '20282df5b73053a70acfae17eb881ce11f581007a86109185fa5b85848a29cab',  # Crimson Cliffs (now CRIM; CCHS = Cedar City)
    'TIMP': '8f86023e2aa3193a9af985d6d0be049e6a359e22b8bec93935bf2362042632db',  # Timpanogos (now THS; TIMP = Timpview)
}

RETIRED_SAME_AS = {'TIMP': 'THS', 'CCHS': 'CRIM'}


def _sha256(path):
    import hashlib
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def seed_builtin(folder, builtin_folder, already):
    """Copy built-in logos for codes that have no logo yet and weren't seeded
    before. already: codes seeded on earlier starts. Returns the codes copied
    and the updated 'already' list."""
    import shutil
    done = set(c.upper() for c in (already or []))
    copied = []
    # Built-in logos shipped under a code that turned out to belong to another
    # school: remove the old file (only if it is still the untouched built-in)
    # so the right school's logo can be seeded under that code.
    for code, digest in RETIRED_BUILTIN.items():
        path = find_logo(folder, code)
        try:
            if path and _sha256(path) == digest:
                os.remove(path)
                done.discard(code)
        except OSError:
            pass
    # ...or a copy of the logo the school now has under its new code (an
    # uploaded Timpanogos logo saved as both THS and TIMP)
    for code, new_code in RETIRED_SAME_AS.items():
        path, other = find_logo(folder, code), find_logo(folder, new_code)
        try:
            if path and other and _sha256(path) == _sha256(other):
                os.remove(path)
                done.discard(code)
        except OSError:
            pass
    try:
        names = sorted(os.listdir(builtin_folder))
    except OSError:
        return copied, sorted(done)
    for n in names:
        code = code_from_filename(n)
        if not code or code in done:
            continue
        done.add(code)
        if find_logo(folder, code):
            continue
        shutil.copyfile(os.path.join(builtin_folder, n), os.path.join(folder, code + os.path.splitext(n)[1].lower()))
        copied.append(code)
    return copied, sorted(done)
