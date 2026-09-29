import io
import os
import zipfile

import pytest

import scb_loader
from hytek_event_loader import HytekEventLoader
from hytek_parser.hy3.enums import GenderAge

HERE = os.path.dirname(os.path.abspath(__file__))
SCB_DIR = os.path.join(HERE, '..', 'samples', 'scb')


def read(name):
    with open(os.path.join(SCB_DIR, name), 'rb') as f:
        return f.read().decode('latin-1')


def test_parse_e003_heats_and_lanes():
    ev = scb_loader.parse_scb(read('E003.scb'), 'E003.scb')
    assert ev['event_number'] == 3
    assert ev['event_name'] == 'Women 200 Freestyle'
    assert sorted(ev['heats']) == [1, 2, 3, 4, 5]
    assert ev['heats'][1] == {3: ('UNDERWOOD, ELLA', 'GHS'), 4: ('BLACKWOOD, WREN', 'CDRV'),
                              5: ('DELANEY, CLARA', 'MILL')}
    assert len(ev['heats'][2]) == 7
    assert ev['heats'][3][1] == ('MERRIWEATHER, NORA R', 'UHS')   # truncated 20-char name
    assert ev['heats'][5][8] == ('RADCLIFFE, OLIVE', 'TOOEL')
    m = ev['meta']
    assert (m['stroke_code'], m['distance'], m['relay'], m['sex_codes']) == (1, 200, False, [2])
    assert m['gender_age'] == GenderAge.WOMEN_S


def test_parse_e010():
    ev = scb_loader.parse_scb(read('E010.scb'), 'E010.scb')
    assert ev['event_number'] == 10
    assert ev['event_name'] == 'Men 100 Butterfly'
    assert ev['meta']['stroke_code'] == 4 and ev['meta']['sex_codes'] == [1]
    assert sorted(ev['heats']) == [1, 2, 3, 4]
    assert ev['heats'][4][7] == ('MERRIWEATHER, FINLEY', 'UHS')


@pytest.mark.parametrize('raw,expected', [
    ('UNDERWOOD, ELLA', 'Ella Underwood'),
    ('NASH, JANE C', 'Jane C Nash'),
    ('MCALLISTER, BENJAMIN', 'Benjamin McAllister'),
    ("O'BRIEN, SEAN", "Sean O'Brien"),
    ('SMITH-JONES, ANNA', 'Anna Smith-Jones'),
    ('TOOELE  A', 'Tooele A'),
    ('', ''),
])
def test_name_format(raw, expected):
    assert scb_loader.format_swimmer_name(raw) == expected


def test_name_styles():
    assert scb_loader.format_swimmer_name('OAKLEY, OLIVE', 'last_first') == 'Oakley, Olive'
    assert scb_loader.format_swimmer_name('OAKLEY, OLIVE', 'raw') == 'OAKLEY, OLIVE'


@pytest.mark.parametrize('raw,name,meta', [
    ('GIRLS 200 MEDLEY RELAY', 'Girls 200 Medley Relay', {'relay': True, 'stroke_code': 5, 'distance': 200}),
    ('BOYS 13-14 100 BACK', 'Boys 13-14 100 Backstroke', {'age_min': 13, 'age_max': 14, 'distance': 100, 'stroke_code': 2}),
    ('MIXED 8 & UNDER 25 FREE', 'Mixed 8 & Under 25 Freestyle', {'age_max': 8, 'distance': 25, 'is_mixed': True}),
    ('WOMEN 200 IM', 'Women 200 IM', {'stroke_code': 5, 'distance': 200}),
    ('MEN 100 BREAST', 'Men 100 Breaststroke', {'stroke_code': 3}),
])
def test_event_names(raw, name, meta):
    got_name, got = scb_loader.parse_event_name(raw)
    assert got_name == name
    for k, v in meta.items():
        assert got[k] == v, k


def test_bad_file():
    with pytest.raises(ValueError):
        scb_loader.parse_scb('not a start list\n', 'x.scb')


def test_crlf_and_trailing_blank_lines():
    text = read('E010.scb').replace('\r\n', '\n').replace('\n', '\r\n') + '\r\n\r\n'
    ev = scb_loader.parse_scb(text, 'E010.scb')
    assert sorted(ev['heats']) == [1, 2, 3, 4]


def test_load_into_loader_and_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('export/E003.scb', read('E003.scb'))
        z.writestr('export/E010.scb', read('E010.scb'))
        z.writestr('export/readme.txt', 'ignore me')
    texts = scb_loader.expand_uploads([('startlists.zip', buf.getvalue())])
    assert [t[0] for t in texts] == ['E003.scb', 'E010.scb']

    loader = HytekEventLoader()
    n_events, n_heats, errs = scb_loader.load_scb_into(loader, texts)
    assert (n_events, n_heats, errs) == (2, 9, [])
    assert loader.get_event_name(3) == 'Women 200 Freestyle'
    assert loader.get_display_string(3, 1, 4) == 'Wren Blackwood'
    assert loader.get_team_code(3, 1, 4) == 'CDRV'
    assert loader.get_display_string(3, 1, 1) == ''
    assert loader.get_seed_time(3, 1, 4) is None
    assert loader.has_names

    # Survives the settings.json round trip
    other = HytekEventLoader()
    other.from_object(loader.to_object())
    assert other.get_display_string(10, 4, 7) == 'Finley Merriweather'


def test_reload_keeps_combined_heats():
    loader = HytekEventLoader()
    texts = scb_loader.read_scb_folder(SCB_DIR)
    scb_loader.load_scb_into(loader, texts)
    loader.combine_events({(3, 1): (3, 2)})
    assert loader.get_display_string(3, 2, 3) == 'Ella Underwood*'
    scb_loader.load_scb_into(loader, texts)   # re-export after a deck change
    assert loader.combined == {(3, 1): (3, 2)}
    assert loader.get_display_string(3, 2, 3) == 'Ella Underwood*'


def test_folder_signature_changes(tmp_path):
    (tmp_path / 'E003.scb').write_text(read('E003.scb'))
    a = scb_loader.folder_signature(str(tmp_path))
    assert len(a) == 1
    (tmp_path / 'E010.scb').write_text(read('E010.scb'))
    assert scb_loader.folder_signature(str(tmp_path)) != a
    assert scb_loader.folder_signature(str(tmp_path / 'missing')) is None
