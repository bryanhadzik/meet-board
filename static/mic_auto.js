/* Auto mode for the mic channel strip (Music page).
 *
 * Listens to the mic every tick (~50 ms) and works out what kind of room it is
 * in from the noise between words, then sets the things that depend on that:
 *
 *   quiet  - empty pool, before the meet        low cut  80 Hz, music dips  8 dB
 *   busy   - warm-up, crowd arriving            low cut 120 Hz, music dips 12 dB
 *   loud   - racing, cheering                   low cut 150 Hz, music dips 15 dB
 *
 *   gain        rides the talker's level toward TARGET_DB, slowly (down faster
 *               than up), only while someone is talking, and never so high that
 *               the room noise itself comes up past NOISE_CEIL_DB
 *   talking at  noise floor + 9 dB, so the music dip follows the room
 *   feedback    a single tone that stands way above everything else for 300 ms
 *               (howl) pulls the gain down 6 dB at once (up to 18), then lets it
 *               back slowly once it has stayed quiet
 *
 * Pure logic, no Web Audio: step() is fed levels and a spectrum, so it can be
 * tested without a browser.  Levels are dBFS measured BEFORE the gain stage.
 */
(function (root) {
  'use strict';
  var TICK_MS = 50, HIST = 200, SPEECH = 80;      // 10 s of room, last ~4 s of talking
  var TARGET_DB = -18, NOISE_CEIL_DB = -48, GAIN_MIN = -20, GAIN_MAX = 24;
  var SCENES = {
    quiet: {label: 'Quiet room',                    low_cut_hz: 80,  duck_db: 8},
    busy:  {label: 'Busy (warm-up, crowd arriving)', low_cut_hz: 120, duck_db: 12},
    loud:  {label: 'Loud (racing, cheering)',        low_cut_hz: 150, duck_db: 15}
  };

  function pct(arr, p) {
    if (!arr.length) return null;
    var s = arr.slice().sort(function (a, b) { return a - b; });
    return s[Math.min(s.length - 1, Math.max(0, Math.floor(p * (s.length - 1))))];
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function MicAuto() { this.reset(); }
  MicAuto.prototype.reset = function (startGain) {
    this.hist = []; this.speech = [];
    this.gain = startGain == null ? 0 : startGain;
    this.fbCut = 0; this.fbBin = -1; this.fbCount = 0; this.fbAt = -1e9;
    this.lastTalk = -1e9; this.lastAdj = 0; this.scene = ''; this.started = false;
  };

  /* preDb: mic level before gain (dBFS). spec: Float32Array of dB per FFT bin
     (or null). binHz: Hz per bin. now: ms. Returns the settings to use. */
  MicAuto.prototype.step = function (preDb, spec, binHz, now) {
    if (!this.started) { this.started = true; this.lastAdj = now; }
    this.hist.push(preDb); if (this.hist.length > HIST) this.hist.shift();
    var floor = this.hist.length >= 40 ? pct(this.hist, 0.1) : null;
    var talking = floor != null && preDb > floor + 9 && preDb > -75;
    if (talking) { this.speech.push(preDb); if (this.speech.length > SPEECH) this.speech.shift(); this.lastTalk = now; }
    var voice = this.speech.length >= 10 ? pct(this.speech, 0.85) : null;

    // room -> scene, with 3 dB of hysteresis so it doesn't flicker on the edge
    if (floor != null) {
      var s = this.scene, f = floor;
      var want = f < -62 ? 'quiet' : f < -48 ? 'busy' : 'loud';
      if (!s) s = want;
      else if (want !== s) {
        var edge = (s === 'quiet' || want === 'quiet') ? -62 : -48;
        if (Math.abs(f - edge) >= 3) s = want;
      }
      this.scene = s;
    }

    // feedback: one bin far above the rest, the same bin, for 300 ms
    if (spec && spec.length && binHz) {
      var lo = Math.max(1, Math.floor(200 / binHz)), hi = Math.min(spec.length - 1, Math.ceil(8000 / binHz));
      var maxV = -Infinity, maxI = -1, band = [];
      for (var i = lo; i <= hi; i++) { var v = spec[i]; band.push(v); if (v > maxV) { maxV = v; maxI = i; } }
      var med = pct(band, 0.5);
      if (maxV - med > 35 && maxV > -45) {
        if (Math.abs(maxI - this.fbBin) <= 1) this.fbCount++; else { this.fbBin = maxI; this.fbCount = 1; }
        if (this.fbCount >= 300 / TICK_MS) { this.fbCut = Math.min(18, this.fbCut + 6); this.fbCount = 0; this.fbAt = now; }
      } else { this.fbCount = 0; this.fbBin = -1; }
    }

    // every half second: ride the gain, let a feedback cut recover
    if (now - this.lastAdj >= 500) {
      this.lastAdj = now;
      if (voice != null && now - this.lastTalk < 1500) {
        var want = TARGET_DB - voice;
        if (floor != null) want = Math.min(want, NOISE_CEIL_DB - floor);
        want = clamp(want, GAIN_MIN, GAIN_MAX);
        this.gain = clamp(this.gain + clamp(want - this.gain, -1.5, 0.75), GAIN_MIN, GAIN_MAX);
      }
      if (this.fbCut > 0 && now - this.fbAt > 5000) this.fbCut = Math.max(0, this.fbCut - 0.5);
    }

    var sc = SCENES[this.scene] || SCENES.busy;
    var eff = this.gain - this.fbCut;
    return {
      ready: floor != null,
      scene: this.scene || '', scene_label: this.scene ? sc.label : 'Listening…',
      gain_db: Math.round(eff * 2) / 2,
      low_cut_hz: sc.low_cut_hz,
      duck_db: sc.duck_db,
      duck_threshold_db: floor == null ? null : Math.round(clamp(floor + eff + 9, -70, -10)),
      floor_db: floor == null ? null : Math.round(floor),
      voice_db: voice == null ? null : Math.round(voice),
      feedback_cut_db: this.fbCut,
      talking: talking
    };
  };
  MicAuto.SCENES = SCENES;
  MicAuto.TARGET_DB = TARGET_DB;
  if (typeof module !== 'undefined' && module.exports) module.exports = MicAuto;
  else root.MicAuto = MicAuto;
})(this);
