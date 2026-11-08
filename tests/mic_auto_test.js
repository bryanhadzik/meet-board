// node tests/mic_auto_test.js  - synthetic rooms through MicAuto
const MicAuto = require('../static/mic_auto.js');
let fails = 0;
const ok = (c, m) => { console.log((c ? '  ok  ' : '  FAIL ') + m); if (!c) fails++; };
function run(a, secs, room, voice, opts = {}) {
  let r, t = opts.t0 || 0;
  for (let i = 0; i < secs * 20; i++, t += 50) {
    const talkingNow = voice != null && (i % 40) < 28;        // 1.4 s talk, 0.6 s pause
    const pre = (talkingNow ? voice : room) + (Math.random() - 0.5) * 3;
    let spec = null;
    if (opts.howl && i > 20) { spec = new Float32Array(512).fill(-90); spec[50] = -20; }
    r = a.step(pre, spec, 46.875, t);
  }
  r.t = t; return r;
}
console.log('quiet room, soft talker');
let a = new MicAuto(); a.reset(0);
let r = run(a, 40, -70, -45);
ok(r.scene === 'quiet', 'scene quiet (' + r.scene_label + ')');
ok(r.gain_db > 10 && r.gain_db <= 24, 'gain rose for a soft talker: ' + r.gain_db);
ok(r.low_cut_hz === 80 && r.duck_db === 8, 'quiet low cut / dip');
ok(r.duck_threshold_db > -70 + r.gain_db && r.duck_threshold_db < -45 + r.gain_db, 'talking threshold sits between room and voice: ' + r.duck_threshold_db);

console.log('loud room, same talker');
a = new MicAuto(); a.reset(0);
r = run(a, 40, -40, -22);
ok(r.scene === 'loud', 'scene loud');
ok(-40 + r.gain_db <= -46, 'room noise kept down: floor+gain = ' + (-40 + r.gain_db));
ok(r.low_cut_hz === 150 && r.duck_db === 15, 'loud low cut / dip');

console.log('busy');
a = new MicAuto(); a.reset(0);
r = run(a, 30, -55, -30);
ok(r.scene === 'busy', 'scene busy');

console.log('nobody talking: gain holds');
a = new MicAuto(); a.reset(6);
r = run(a, 30, -60, null);
ok(r.gain_db === 6, 'gain unchanged with no talking: ' + r.gain_db);

console.log('loud talker comes down');
a = new MicAuto(); a.reset(20);
r = run(a, 30, -70, -10);
ok(r.gain_db <= -6, 'gain came down: ' + r.gain_db);

console.log('feedback howl');
a = new MicAuto(); a.reset(10);
r = run(a, 3, -60, null, {howl: true});
ok(r.feedback_cut_db >= 12, 'howl cut the gain: ' + r.feedback_cut_db + ' dB');
ok(r.gain_db <= 10 - 12, 'effective gain lowered: ' + r.gain_db);
r = run(a, 60, -60, null, {t0: r.t});
ok(r.feedback_cut_db === 0, 'recovers when quiet');

console.log('scene hysteresis');
a = new MicAuto(); a.reset(0);
run(a, 20, -50, null);
r = run(a, 20, -47, null, {t0: 20000});
ok(r.scene === 'busy', 'stays busy 1 dB past the edge (' + r.floor_db + ')');
console.log(fails ? fails + ' FAILED' : 'all passed'); process.exit(fails ? 1 : 0);
