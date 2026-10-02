import time

import music_mute


class FakeOBS:
    def __init__(self):
        self.mute = {'Pool Mic': False, 'Desktop Audio': False, 'Announcer': True}
        self.calls = []

    def call(self, rt, data=None):
        self.calls.append((rt, data))
        if rt == 'GetInputList':
            return {'inputs': [{'inputName': n, 'inputKind': 'wasapi_input_capture'} for n in self.mute] +
                              [{'inputName': 'Logo', 'inputKind': 'image_source'}]}
        if rt == 'GetInputMute':
            if data['inputName'] not in self.mute:
                raise RuntimeError('no audio')
            return {'inputMuted': self.mute[data['inputName']]}
        if rt == 'SetInputMute':
            self.mute[data['inputName']] = data['inputMuted']
            return {}
        raise RuntimeError(rt)


def make(**kw):
    obs, settings = FakeOBS(), dict({'music_obs_mute': True, 'music_obs_inputs': ['Pool Mic', 'Announcer'],
                                     'music_unmute_delay': 0}, **kw)
    return obs, settings, music_mute.StreamMute(obs, settings, lambda: None, log=lambda *a: None)


def test_mutes_while_playing_and_restores_only_what_it_muted():
    obs, settings, sm = make()
    assert [i['name'] for i in sm.audio_inputs()] == ['Pool Mic', 'Desktop Audio', 'Announcer']   # image skipped
    sm.music_changed('playing')
    assert obs.mute['Pool Mic'] is True
    assert sm.muted_by_us == ['Pool Mic']          # Announcer was already muted by the operator: not ours
    sm.music_changed('stopped'); sm.tick('stopped')
    assert obs.mute['Pool Mic'] is False and obs.mute['Announcer'] is True   # operator's mute left alone
    assert sm.muted_by_us == []
    assert obs.mute['Desktop Audio'] is False      # never ticked


def test_delay_and_resume_cancel_unmute():
    obs, settings, sm = make(music_unmute_delay=5)
    sm.music_changed('playing'); sm.music_changed('paused'); sm.tick('paused')
    assert obs.mute['Pool Mic'] is True            # still inside the delay
    sm.music_changed('playing'); sm._unmute_at = time.time() - 1; sm.tick('playing')
    assert obs.mute['Pool Mic'] is True            # resumed: stays muted


def test_restart_recovery_and_disabled():
    obs, settings, sm = make(music_obs_muted_by_us=['Pool Mic'])
    obs.mute['Pool Mic'] = True                    # left muted by a crash mid-song
    sm.tick('stopped')
    assert obs.mute['Pool Mic'] is False and settings['music_obs_muted_by_us'] == []
    obs, settings, sm = make(music_obs_mute=False)
    sm.music_changed('playing')
    assert obs.mute['Pool Mic'] is False
