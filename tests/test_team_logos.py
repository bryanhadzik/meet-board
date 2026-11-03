

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
