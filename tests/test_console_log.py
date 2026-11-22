import console_log


def test_records_only_started_heats(tmp_path):
    p = str(tmp_path / 'cl.json')
    c = console_log.ConsoleLog(p)
    c.lanes(12, 6, {'lane_time4': ' 49.73', 'lane_place4': '1'})
    assert c.get() == {}                       # never seen starting: results of the last heat, still on the board
    c.start(' 12', ' 6')
    c.lanes(12, 6, {'lane_time4': ' 49.73', 'lane_place4': '1', 'lane_time5': ' 54.79', 'lane_place5': ' ',
                    'lane_running3': True, 'lane_time3': '12.0'})
    assert c.get()['12:6']['lanes'] == {'4': {'time': '49.73', 'place': '1'}, '5': {'time': '54.79', 'place': ''}}
    assert '12:6' in console_log.ConsoleLog(p).get()
    c.start(12, 6)                             # a re-run starts clean
    assert c.get()['12:6']['lanes'] == {}
