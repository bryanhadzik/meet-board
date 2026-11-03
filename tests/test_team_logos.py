

def test_upload_with_a_typed_code_renames_the_file(tmp_path):
    import team_logos as T
    png = b'\x89PNG\r\n\x1a\nfake'
    saved, skipped = T.save_uploads(str(tmp_path), [
        ('Timpanogos_High_School_Logo.jpg', png, 'timp'),   # any name, typed code wins
        ('SHS.png', png, ''),                               # blank box: the file name is the code
        ('random name.png', png, ''),                       # nothing to go on: skipped
        ('x.png', png, 'TOO-LONG-CODE'),                    # bad code: skipped
    ])
    assert sorted(saved) == ['SHS', 'TIMP']
    assert skipped == ['random name.png', 'x.png']
    assert T.find_logo(str(tmp_path), 'TIMP').endswith('TIMP.jpg')


def test_unnamed_saved_row_does_not_shadow_an_alias():
    """Pool PC: SHS/DPHS got saved as nameless pink/orange rows, hiding
    Stansbury's and Deseret Peak's colors."""
    import meet_board as M
    settings = {'team_colors': {'SHS': {'name': '', 'color': '#e57ab8'}, 'DPHS': {'name': '', 'color': '#f2a33a'},
                                'THS': {'name': 'Timp', 'color': '#ffe066'}}}
    canon = M.canonical_codes(settings, ['SHS', 'DPHS', 'THS'])
    assert canon == {'SHS': 'STAN', 'DPHS': 'DPEAK', 'THS': 'THS'}
    tc = M.team_colors(settings, ['SHS', 'DPHS', 'THS'])
    assert tc['SHS']['alias_of'] == 'STAN' and tc['SHS']['color'] == M.DEFAULT_TEAM_COLORS['STAN']['color']
    # a row the user named stays its own school
    settings['team_colors']['SHS']['name'] = 'Sky High'
    assert M.canonical_codes(settings, ['SHS'])['SHS'] == 'SHS'
