/* Warm-up slide: the meet, the start time, and a slow listing of every event
   and the swimmers in each heat. Shown before the meet on the spectator
   boards (full screen, the heats scroll slowly up like credits) and on the
   stream (a band along the bottom that steps through the heats one at a time,
   so the camera still shows warm-ups).

   After the heats it lists each school's swimmers and the events each one
   is swimming, then loops back to the first event.

   Turned on/off with the "Warm-up slide" button in the tab bar or on
   Settings -> Warm-up slide, and taken down automatically when the first race
   starts.

     mbWarmupSlide(socket, {mode: 'full'})                     meet boards
     mbWarmupSlide(socket, {mode: 'band', bandh: 392,           stream overlay
                            onShow: function (shown) {...}})

   Scroll speed and band hold time come from Settings -> Warm-up slide (sent
   with the slide, applied live). URL options on a page override them:
   ?wuspeed=30 (full: scroll speed, px/s at 1080p), ?wuhold=7 (band: seconds
   per heat), ?nowarmup (never show it here).
*/
window.mbWarmupSlide = function (socket, opts) {
  opts = opts || {};
  var MODE = opts.mode === 'band' ? 'band' : 'full';
  var BANDH = opts.bandh || 392;
  var q = new URLSearchParams(location.search);
  function clamp(v, lo, hi) { v = parseFloat(v); return isFinite(v) ? Math.max(lo, Math.min(hi, v)) : null; }
  var SPEED_URL = clamp(q.get('wuspeed'), 5, 200), HOLD_URL = clamp(q.get('wuhold'), 3, 60);
  function speed() { return SPEED_URL || clamp(data && data.speed, 5, 200) || 30; }   // px per second at 1920 wide
  function hold() { return HOLD_URL || clamp(data && data.hold, 3, 60) || 7; }        // band: seconds per heat
  var data = null, tick = null, raf = null, rotate = null, sigShown = '';

  var css = document.createElement('style');
  css.textContent = [
    '.wus{position:fixed;z-index:2147483000;font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;color:#fff;',
    '  --u:calc(100vw/1920);opacity:0;transition:opacity .5s ease;pointer-events:none;display:flex;box-sizing:border-box}',
    '.wus *{box-sizing:border-box}',
    '.wus.on{opacity:1}',
    '.wus-kick{display:inline-block;font-weight:800;letter-spacing:.22em;color:#0b1618;background:#3ddc84;border-radius:calc(8*var(--u))}',
    '.wus-ttl{font-weight:800;line-height:1.02;letter-spacing:-.01em}',
    '.wus-sub{color:#bfe6ec;font-weight:500}',
    '.wus-when{display:flex;flex-direction:column;align-items:flex-end;text-align:right;min-width:max-content}',
    '.wus-k{font-weight:700;letter-spacing:.16em;color:#9fd4dc}',
    '.wus-t{font-weight:800;font-family:Consolas,"Cascadia Mono",monospace;color:#ffd60a;white-space:nowrap;line-height:1}',
    '.wus-c{color:#bfe6ec;font-weight:600}',
    '.wus-ln{flex:0 0 auto;display:flex;align-items:center;justify-content:center;font-weight:800;',
    '  background:var(--tc,#2a3b40);color:var(--ink,#fff);font-family:Consolas,"Cascadia Mono",monospace}',
    '.wus-nm{flex:1;min-width:0;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.wus-nm:has(.wus-legs){white-space:normal}',
    '.wus-legs{display:block;font-weight:400;color:#bfd2d6;white-space:normal;line-height:1.15}',
    '.wus-tm{flex:0 0 auto;font-family:Consolas,"Cascadia Mono",monospace;font-weight:700;color:var(--tc,#9fb3b8);letter-spacing:.04em}',
    '.wus-evno{font-weight:800;letter-spacing:.12em;color:#3ddc84;white-space:nowrap}',
    '.wus-evname{font-weight:700;line-height:1.15}',

    /* ---- full screen (meet boards): header, then the heats scrolling up ---- */
    '.wus.full{inset:0;flex-direction:column;padding:calc(46*var(--u)) calc(70*var(--u)) 0;',
    '  background:radial-gradient(120% 90% at 20% 0%,#0f6f80 0%,#0a3f4a 45%,#06171b 100%)}',
    '.wus.full .wus-head{display:flex;align-items:flex-end;justify-content:space-between;gap:calc(40*var(--u));flex:0 0 auto}',
    '.wus.full .wus-kick{font-size:calc(24*var(--u));padding:calc(6*var(--u)) calc(16*var(--u))}',
    '.wus.full .wus-ttl{font-size:calc(80*var(--u));margin-top:calc(14*var(--u))}',
    '.wus.full .wus-sub{font-size:calc(30*var(--u));margin-top:calc(6*var(--u))}',
    '.wus.full .wus-k{font-size:calc(26*var(--u))} .wus.full .wus-t{font-size:calc(80*var(--u));margin-top:calc(6*var(--u))} .wus.full .wus-c{font-size:calc(32*var(--u));margin-top:calc(6*var(--u))}',
    '.wus.full .wus-teams{display:flex;flex-wrap:wrap;gap:calc(10*var(--u)) calc(14*var(--u));margin:calc(22*var(--u)) 0 calc(18*var(--u));flex:0 0 auto;',
    '  max-height:calc(110*var(--u));overflow:hidden}',
    '.wus-chip{display:flex;align-items:center;gap:calc(10*var(--u));background:rgba(255,255,255,.07);border-radius:999px;',
    '  padding:calc(4*var(--u)) calc(16*var(--u)) calc(4*var(--u)) calc(4*var(--u));border:calc(2*var(--u)) solid var(--tc,#3a4d52)}',
    '.wus-chip .wus-logo{width:calc(40*var(--u));height:calc(40*var(--u));border-radius:50%;background:#fff;display:flex;align-items:center;',
    '  justify-content:center;overflow:hidden;padding:calc(3*var(--u))}',
    '.wus-chip .wus-logo img{max-width:100%;max-height:100%;object-fit:contain;display:block}',
    '.wus-chip .wus-logo.wus-code{background:var(--tc,#2a3b40);color:#fff;font:800 calc(12*var(--u)) Consolas,monospace}',
    '.wus-chip span{font-weight:700;font-size:calc(22*var(--u));white-space:nowrap}',
    '.wus.full .wus-view{flex:1;min-height:0;overflow:hidden;position:relative;',
    '  -webkit-mask-image:linear-gradient(180deg,transparent 0,#000 calc(40*var(--u)),#000 calc(100% - 70*var(--u)),transparent 100%);',
    '  mask-image:linear-gradient(180deg,transparent 0,#000 calc(40*var(--u)),#000 calc(100% - 70*var(--u)),transparent 100%)}',
    '.wus.full .wus-track{position:absolute;left:0;right:0;top:0;will-change:transform}',
    '.wus.full .wus-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:calc(18*var(--u));align-items:start;',
    '  padding:calc(30*var(--u)) 0 calc(18*var(--u))}',
    '.wus-heat{background:rgba(4,20,24,.55);border:1px solid rgba(255,255,255,.08);border-radius:calc(14*var(--u));',
    '  padding:calc(14*var(--u)) calc(16*var(--u)) calc(10*var(--u));min-width:0}',
    '.wus.full .wus-heat .wus-evno{font-size:calc(18*var(--u))}',
    '.wus.full .wus-heat .wus-evname{font-size:calc(24*var(--u));margin:calc(2*var(--u)) 0 calc(8*var(--u))}',
    '.wus.full .wus-lane{display:flex;align-items:center;gap:calc(10*var(--u));padding:calc(5*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06)}',
    '.wus.full .wus-ln{width:calc(34*var(--u));height:calc(34*var(--u));border-radius:calc(8*var(--u));font-size:calc(20*var(--u))}',
    '.wus.full .wus-nm{font-size:calc(23*var(--u))}',
    '.wus.full .wus-legs{font-size:calc(16*var(--u))}',
    '.wus.full .wus-tm{font-size:calc(15*var(--u))}',
    '.wus.full .wus-empty{font-size:calc(34*var(--u));color:#bfe6ec;margin-top:calc(60*var(--u))}',
    /* phone results QR (both modes) */
    '.wus-qr{display:flex;align-items:center;gap:calc(18*var(--u));flex:0 1 auto;min-width:0}',
    '.wus-qr .wus-qrimg{background:#fff;border-radius:calc(10*var(--u));flex:0 0 auto;display:flex}',
    '.wus-qr .wus-qrimg img{display:block;width:100%;height:100%}',
    '.wus-qr .wus-qrt{display:flex;flex-direction:column;min-width:0}',
    '.wus-qr .wus-qk{font-weight:800;letter-spacing:.14em;color:#3ddc84}',
    '.wus-qr .wus-qu{font-weight:800;font-family:Consolas,"Cascadia Mono",monospace;color:#fff;white-space:nowrap}',
    '.wus-qr .wus-qs{color:#bfe6ec;font-weight:500;line-height:1.25}',
    /* full: the QR sits at the far right of the top area and runs from the top of the screen down to the scrolling heats */
    '.wus.full .wus-top{display:flex;align-items:stretch;gap:calc(40*var(--u));flex:0 0 auto;margin-top:calc(-26*var(--u))}',
    '.wus.full .wus-top .wus-head{margin-top:calc(26*var(--u))}',
    '.wus.full .wus-topl{flex:1 1 auto;min-width:0;display:flex;flex-direction:column}',
    '.wus.full .wus-top .wus-teams{margin-bottom:calc(18*var(--u))}',
    '.wus.full .wus-qr{gap:calc(30*var(--u));align-self:stretch;align-items:center;flex:0 1 auto;min-width:0}',
    '.wus.full .wus-qrt .wus-when{align-items:flex-start;text-align:left;margin-bottom:calc(22*var(--u))}',
    '.wus.full .wus-qrt .wus-t{font-size:calc(66*var(--u))}',
    '.wus.full .wus-qrimg{align-self:stretch;box-sizing:border-box;aspect-ratio:1/1;min-height:calc(350*var(--u));padding:calc(18*var(--u))}',
    '.wus.full .wus-qk{font-size:calc(26*var(--u))} .wus.full .wus-qu{font-size:calc(46*var(--u));margin-top:calc(6*var(--u))}',
    '.wus.full .wus-qs{font-size:calc(25*var(--u));margin-top:calc(10*var(--u));max-width:calc(500*var(--u))}',
    '.wus.band .wus-qr{flex:0 0 auto;align-self:center;gap:calc(12*var(--u))}',
    '.wus.band .wus-qrimg{width:calc(150*var(--u));height:calc(150*var(--u));padding:calc(8*var(--u))}',
    '.wus.band .wus-qk{font-size:calc(15*var(--u))} .wus.band .wus-qu{font-size:calc(20*var(--u));margin-top:calc(3*var(--u))}',
    '.wus.band .wus-qs{display:none}',   /* the band needs the room for swimmer names */

    /* ---- band (stream): one heat at a time, clear of the player controls ---- */
    '.wus.band{left:0;right:0;bottom:0;height:calc(var(--bh)*var(--u));background:#091113;align-items:stretch;',
    '  padding:calc(22*var(--u)) calc(44*var(--u)) calc(84*var(--u));gap:calc(36*var(--u))}',
    '.wus.band::before{content:"";position:absolute;left:0;right:0;top:calc(-5*var(--u));height:calc(5*var(--u));background:#3ddc84}',
    '.wus.band .wus-left{flex:0 1 auto;max-width:calc(440*var(--u));display:flex;flex-direction:column;justify-content:center;min-width:0}',
    '.wus.band .wus-kick{align-self:flex-start;font-size:calc(18*var(--u));padding:calc(5*var(--u)) calc(14*var(--u))}',
    '.wus.band .wus-ttl{font-size:calc(42*var(--u));margin-top:calc(10*var(--u))}',
    '.wus.band .wus-sub{font-size:calc(22*var(--u));margin-top:calc(4*var(--u))}',
    '.wus.band .wus-when{justify-content:center}',
    '.wus.band .wus-k{font-size:calc(18*var(--u))} .wus.band .wus-t{font-size:calc(56*var(--u));margin-top:calc(4*var(--u))} .wus.band .wus-c{font-size:calc(24*var(--u));margin-top:calc(4*var(--u))}',
    '.wus.band .wus-stage{flex:1 1 0;min-width:0;display:flex;flex-direction:column;justify-content:center;transition:opacity .45s ease}',
    '.wus.band .wus-stage.fade{opacity:0}',
    '.wus.band .wus-hdr{display:flex;align-items:baseline;gap:calc(16*var(--u));min-width:0;margin-bottom:calc(10*var(--u))}',
    '.wus.band .wus-evno{font-size:calc(20*var(--u))}',
    '.wus.band .wus-evname{font-size:calc(30*var(--u));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}',
    '.wus.band .wus-hn{font-size:calc(20*var(--u));color:#9fd4dc;font-weight:700;white-space:nowrap;margin-left:auto}',
    '.wus.band .wus-lanes{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:calc(8*var(--u)) calc(14*var(--u))}',
    '.wus.band .wus-lane{display:flex;align-items:center;gap:calc(10*var(--u));min-width:0}',
    '.wus.band .wus-ln{width:calc(36*var(--u));height:calc(36*var(--u));border-radius:calc(8*var(--u));font-size:calc(20*var(--u))}',
    '.wus.band .wus-nm{font-size:calc(var(--nm,22)*var(--u));white-space:normal;line-height:1.08;overflow-wrap:normal;',
    '  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2}',   /* long names wrap to a second line */
    '.wus.band .wus-nm:has(.wus-legs){display:block;-webkit-line-clamp:none}',
    '.wus.band .wus-legs{font-size:calc(14*var(--u))}',
    '.wus.band .wus-tm{display:none}',
    /* swimmers by school: name over the events they swim */
    '.wus-sw{display:flex;align-items:stretch;gap:calc(10*var(--u));min-width:0}',
    '.wus-sw .wus-bar{flex:0 0 calc(6*var(--u));border-radius:calc(3*var(--u));background:var(--tc,#3a4d52)}',
    '.wus-sw .wus-swt{flex:1;min-width:0;display:flex;flex-direction:column}',
    '.wus-swn{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.wus-swe{color:#bfd2d6;font-weight:500;line-height:1.2}',
    '.wus.band .wus-swg{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:calc(10*var(--u)) calc(22*var(--u))}',
    '.wus.band .wus-swn{font-size:calc(22*var(--u))}',
    '.wus.band .wus-swe{font-size:calc(16*var(--u));max-height:2.4em;overflow:hidden}',
    '.wus.band .wus-school .wus-evno{color:var(--tc,#3ddc84)}',
    '.wus.full .wus-sect{grid-column:1/-1;font-weight:800;letter-spacing:.16em;color:#3ddc84;font-size:calc(26*var(--u));',
    '  padding-top:calc(24*var(--u));border-top:1px solid rgba(255,255,255,.15);margin-top:calc(12*var(--u))}',
    '.wus.full .wus-scard{border-top:calc(5*var(--u)) solid var(--tc,#3a4d52)}',
    '.wus.full .wus-scard .wus-evname{font-size:calc(26*var(--u));margin:0 0 calc(8*var(--u))}',
    '.wus.full .wus-sw{padding:calc(5*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06)}',
    '.wus.full .wus-swn{font-size:calc(22*var(--u))} .wus.full .wus-swe{font-size:calc(16*var(--u))}'
  ].join('\n');
  document.head.appendChild(css);

  var root = document.createElement('div');
  root.className = 'wus ' + MODE;
  root.style.setProperty('--bh', BANDH);
  function attach() { (document.body || document.documentElement).appendChild(root); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);

  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function lin(c) { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function inkOf(hex) {
    if (!/^#[0-9a-f]{6}$/i.test(hex || '')) return '#ffffff';
    var L = 0.2126 * lin(parseInt(hex.slice(1, 3), 16)) + 0.7152 * lin(parseInt(hex.slice(3, 5), 16)) + 0.0722 * lin(parseInt(hex.slice(5, 7), 16));
    return (L + 0.05) / 0.05 > 1.05 / (L + 0.05) ? '#0b1618' : '#ffffff';
  }
  function colorOf(team) { return ((data && data.colors) || {})[team] || ''; }
  function startDate() {
    var m = /^(\d{1,2}):(\d{2})$/.exec((data && data.start) || '');
    if (!m) return null;
    var d = new Date(); d.setHours(+m[1], +m[2], 0, 0); return d;
  }
  function clock(d) {
    var h = d.getHours(), m = d.getMinutes();
    return ((h % 12) || 12) + ':' + (m < 10 ? '0' : '') + m + (h < 12 ? ' AM' : ' PM');
  }
  function countdown() {
    var d = startDate(); if (!d) return '';
    var mins = Math.ceil((d - new Date()) / 60000);
    if (mins <= 0) return 'Starting soon';
    if (mins < 60) return 'in ' + mins + ' min';
    return 'in ' + Math.floor(mins / 60) + ' h ' + (mins % 60) + ' min';
  }
  function dateLine() {
    if (!data.date) return '';
    var p = data.date.split('-'); if (p.length !== 3) return '';
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    if (d.toDateString() === new Date().toDateString()) return '';   // it's today: no need to say so
    return d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'});
  }
  function heats() {
    var out = [];
    (data.events || []).forEach(function (ev) {
      (ev.heats || []).forEach(function (h) { out.push({ev: ev, heat: h}); });
    });
    return out;
  }

  // "Girls 200 Yard Medley Relay" -> "200 MR", "Boys 50 Yard Freestyle" -> "50 Free"
  function shortEv(n) {
    return String(n || '').replace(/^(girls|boys|women|men|mixed)\s+/i, '')
      .replace(/\s*\b(yards?|meters?|yd)\b/gi, '')
      .replace(/medley relay/i, 'MR').replace(/free(style)? relay/i, 'FR')
      .replace(/individual medley/i, 'IM').replace(/freestyle/i, 'Free').replace(/backstroke/i, 'Back')
      .replace(/breaststroke/i, 'Breast').replace(/butterfly/i, 'Fly').replace(/\s+/g, ' ').trim();
  }
  function cleanName(n) {
    n = String(n || '').trim();
    var m = /^([^,]+),\s*(.+)$/.exec(n);           // "Last, First" -> "First Last"
    return m ? m[2] + ' ' + m[1] : n;
  }
  function lastName(n) { var p = n.split(/\s+/); return (p[p.length - 1] + ' ' + n).toLowerCase(); }
  // Each school (in schedule order) with its swimmers and the events each one swims, relays included.
  function rosters() {
    var so = data.school_of || {}, groups = {}, order = [], legs = [];
    function group(code, team) {
      var k = so[code] || code;
      if (!groups[k]) { groups[k] = {team: team || {code: code, name: code}, sw: {}}; order.push(k); }
      return groups[k];
    }
    (data.teams || []).forEach(function (t) { group(t.code, t); });
    (data.events || []).forEach(function (ev) {
      (ev.heats || []).forEach(function (h) {
        h.lanes.forEach(function (l) {
          if (!l.team) return;
          var g = group(l.team);
          if (l.legs && l.legs.length) { legs.push({g: g, ev: ev, legs: l.legs.map(cleanName)}); return; }
          var n = cleanName(l.name); if (!n) return;
          var s = g.sw[n] || (g.sw[n] = {name: n, evs: []});
          if (s.evs.indexOf(ev) < 0) s.evs.push(ev);
        });
      });
    });
    // Relay legs are often last names only: give the relay to the swimmer with
    // that last name on the team (two Andersons on one relay both get it).
    legs.forEach(function (r) {
      r.legs.forEach(function (n) {
        if (!n) return;
        var key = n.toLowerCase(), hits = Object.keys(r.g.sw).filter(function (full) {
          var f = full.toLowerCase();
          return f === key || f.slice(-key.length - 1) === ' ' + key;
        });
        var same = r.legs.filter(function (x) { return (x || '').toLowerCase() === key; }).length;
        if (!hits.length) hits = [n];
        else if (hits.length > 1 && hits.length !== same) return;   // can't tell which one
        hits.forEach(function (full) {
          var s = r.g.sw[full] || (r.g.sw[full] = {name: full, evs: []});
          if (s.evs.indexOf(r.ev) < 0) s.evs.push(r.ev);
        });
      });
    });
    // keep each swimmer's events in meet order
    var evOrder = (data.events || []).map(function (e) { return e; });
    Object.keys(groups).forEach(function (k) {
      Object.keys(groups[k].sw).forEach(function (n) {
        groups[k].sw[n].evs.sort(function (a, b) { return evOrder.indexOf(a) - evOrder.indexOf(b); });
      });
    });
    return order.map(function (k) {
      var g = groups[k], list = Object.keys(g.sw).map(function (n) { return g.sw[n]; });
      list.sort(function (a, b) { return lastName(a.name) < lastName(b.name) ? -1 : 1; });
      return {team: g.team, color: g.team.color || colorOf(g.team.code), swimmers: list};
    }).filter(function (g) { return g.swimmers.length; });
  }
  function swimmerRow(s, color) {
    var row = el('div', 'wus-sw');
    if (color) row.style.setProperty('--tc', color);
    row.appendChild(el('div', 'wus-bar'));
    var t = el('div', 'wus-swt');
    t.appendChild(el('div', 'wus-swn', s.name));
    t.appendChild(el('div', 'wus-swe', s.evs.map(function (ev) { return shortEv(ev.name); }).join(' · ')));
    row.appendChild(t);
    return row;
  }
  // Long names: shrink the type a little, then shorten the first name to an initial.
  function fitNames(scope) {
    requestAnimationFrame(function () {
      scope.querySelectorAll('.wus-nm, .wus-swn').forEach(function (n) {
        if (n.querySelector('.wus-legs') || !n.clientWidth) return;
        var base = parseFloat(getComputedStyle(n).fontSize), fs = base;
        while (n.scrollWidth > n.clientWidth + 1 && fs > base * 0.76) { fs -= base * 0.04; n.style.fontSize = fs + 'px'; }
        if (n.scrollWidth > n.clientWidth + 1) {
          var p = n.textContent.trim().split(/\s+/);
          if (p.length > 1 && p[0].length > 2) n.textContent = p[0][0] + '. ' + p.slice(1).join(' ');
        }
      });
    });
  }

  function titleBlock() {
    var left = el('div', 'wus-left');
    left.appendChild(el('div', 'wus-kick', 'WARM-UPS'));
    left.appendChild(el('div', 'wus-ttl', data.title || 'Swim meet'));
    var sub = [data.subtitle, dateLine()].filter(Boolean).join(' · ');
    if (sub) left.appendChild(el('div', 'wus-sub', sub));
    return left;
  }
  function whenBlock() {
    var sd = startDate(); if (!sd) return null;
    var when = el('div', 'wus-when');
    when.appendChild(el('span', 'wus-k', 'MEET STARTS'));
    when.appendChild(el('span', 'wus-t', clock(sd)));
    when.appendChild(el('span', 'wus-c', countdown()));
    return when;
  }
  function qrBlock(imageRight) {
    var url = data && data.results_url; if (!url) return null;
    var box = el('div', 'wus-qr');
    var img = el('div', 'wus-qrimg'), im = el('img'); im.alt = ''; im.src = '/results_qr.svg?u=' + encodeURIComponent(url);
    img.appendChild(im); if (!imageRight) box.appendChild(img);
    var t = el('div', 'wus-qrt');
    t.appendChild(el('span', 'wus-qk', data.stream ? 'RESULTS & LIVE STREAM' : 'LIVE RESULTS'));
    t.appendChild(el('span', 'wus-qu', url.replace(/^https?:\/\//, '').replace(/\/$/, '')));
    t.appendChild(el('span', 'wus-qs', data.stream
      ? (MODE === 'full' ? 'Scan for heats, lanes and results. Family can\u2019t make it? The YouTube live stream link is on that page \u2014 send it to them.'
                         : 'Heats and results on your phone. Share the live stream link from that page.')
      : 'Scan for heats, lanes and results on your phone.'));
    box.appendChild(t);
    if (imageRight) box.appendChild(img);
    return box;
  }
  function laneRow(l) {
    var row = el('div', 'wus-lane');
    var c = colorOf(l.team);
    var ln = el('div', 'wus-ln', String(l.lane));
    if (c) { ln.style.setProperty('--tc', c); ln.style.setProperty('--ink', inkOf(c)); row.style.setProperty('--tc', c); }
    row.appendChild(ln);
    var nm = el('div', 'wus-nm', l.name || l.team || '');
    if (l.legs && l.legs.length) nm.appendChild(el('span', 'wus-legs', l.legs.join(' · ')));
    row.appendChild(nm);
    if (l.team) row.appendChild(el('div', 'wus-tm', l.team));
    return row;
  }
  function heatCard(x) {
    var card = el('div', 'wus-heat');
    card.appendChild(el('div', 'wus-evno', 'EVENT ' + x.ev.event + ' · HEAT ' + x.heat.heat + (x.heat.count > 1 ? ' OF ' + x.heat.count : '')));
    card.appendChild(el('div', 'wus-evname', x.ev.name));
    x.heat.lanes.forEach(function (l) { card.appendChild(laneRow(l)); });
    return card;
  }

  // ---- full screen: header + team chips + heats scrolling up, looping ----
  function drawFull() {
    root.textContent = '';
    var top = el('div', 'wus-top'), left = el('div', 'wus-topl');
    var head = el('div', 'wus-head');
    head.appendChild(titleBlock());
    var w = whenBlock();
    left.appendChild(head);
    top.appendChild(left);
    var qr = qrBlock(true);
    if (qr) {                       // start time sits above the results caption, beside the big QR
      top.appendChild(qr);
      if (w) qr.querySelector('.wus-qrt').insertBefore(w, qr.querySelector('.wus-qrt').firstChild);
    } else if (w) head.appendChild(w);
    root.appendChild(top);
    var teams = el('div', 'wus-teams');
    (data.teams || []).forEach(function (t) {
      var chip = el('div', 'wus-chip');
      if (t.color) chip.style.setProperty('--tc', t.color);
      var lg = el('div', 'wus-logo' + (t.logo ? '' : ' wus-code'));
      if (t.logo) { var im = el('img'); im.alt = ''; im.src = '/team_logo/' + encodeURIComponent(t.code) + '?v=' + t.logo; lg.appendChild(im); }
      else lg.textContent = t.code;
      chip.appendChild(lg); chip.appendChild(el('span', '', t.name));
      teams.appendChild(chip);
    });
    if ((data.teams || []).length) left.appendChild(teams);
    if (qr) requestAnimationFrame(function () {         // square: as wide as the top area is tall
      var im = qr.querySelector('.wus-qrimg'); if (im && im.offsetHeight) im.style.width = im.offsetHeight + 'px';
    });
    var view = el('div', 'wus-view'), track = el('div', 'wus-track');
    view.appendChild(track); root.appendChild(view);
    var list = heats();
    if (!list.length) { track.appendChild(el('div', 'wus-empty', 'Start lists will appear here once they are loaded.')); return; }
    var grid = el('div', 'wus-grid');
    list.forEach(function (x) { grid.appendChild(heatCard(x)); });
    var rs = rosters();
    if (rs.length) grid.appendChild(el('div', 'wus-sect', 'SWIMMERS BY SCHOOL'));
    rs.forEach(function (g) {
      var card = el('div', 'wus-heat wus-scard');
      if (g.color) card.style.setProperty('--tc', g.color);
      card.appendChild(el('div', 'wus-evname', g.team.name || g.team.code));
      g.swimmers.forEach(function (s) { card.appendChild(swimmerRow(s, g.color)); });
      grid.appendChild(card);
    });
    track.appendChild(grid);
    fitNames(grid);
    // Scroll only if it doesn't all fit; a second copy makes the loop seamless.
    requestAnimationFrame(function () {
      if (grid.offsetHeight <= view.clientHeight) return;
      track.appendChild(grid.cloneNode(true));
      var loop = grid.offsetHeight, y = 0, last = null;
      function step(t) {
        // speed is read every frame, so a change in Settings applies smoothly
        if (last !== null) { y += (t - last) / 1000 * speed() * window.innerWidth / 1920; if (y >= loop) y -= loop; }
        last = t;
        track.style.transform = 'translateY(' + (-y).toFixed(1) + 'px)';
        raf = requestAnimationFrame(step);
      }
      raf = requestAnimationFrame(step);
    });
  }

  // ---- band: one heat at a time, fading every HOLD seconds ----
  function drawBand() {
    root.textContent = '';
    root.appendChild(titleBlock());
    var stage = el('div', 'wus-stage');
    root.appendChild(stage);
    var qr = qrBlock(); if (qr) root.appendChild(qr);
    var w = whenBlock(); if (w) root.appendChild(w);
    var list = heats(), i = 0, PER = 6;
    rosters().forEach(function (g) {
      var pages = Math.ceil(g.swimmers.length / PER);
      for (var p = 0; p < pages; p++)
        list.push({school: g, page: p + 1, pages: pages, swimmers: g.swimmers.slice(p * PER, (p + 1) * PER)});
    });
    function show() {
      stage.textContent = '';
      stage.classList.remove('wus-school');
      if (!list.length) return;
      var x = list[i % list.length];
      if (x.school) {
        stage.classList.add('wus-school');
        if (x.school.color) stage.style.setProperty('--tc', x.school.color); else stage.style.removeProperty('--tc');
        var sh = el('div', 'wus-hdr');
        sh.appendChild(el('span', 'wus-evno', 'SWIMMERS'));
        sh.appendChild(el('span', 'wus-evname', x.school.team.name || x.school.team.code));
        if (x.pages > 1) sh.appendChild(el('span', 'wus-hn', x.page + ' OF ' + x.pages));
        stage.appendChild(sh);
        var sg = el('div', 'wus-swg');
        x.swimmers.forEach(function (s) { sg.appendChild(swimmerRow(s, x.school.color)); });
        stage.appendChild(sg);
        fitNames(sg);
        return;
      }
      var hdr = el('div', 'wus-hdr');
      hdr.appendChild(el('span', 'wus-evno', 'EVENT ' + x.ev.event));
      hdr.appendChild(el('span', 'wus-evname', x.ev.name));
      hdr.appendChild(el('span', 'wus-hn', 'HEAT ' + x.heat.heat + (x.heat.count > 1 ? ' OF ' + x.heat.count : '')));
      stage.appendChild(hdr);
      var grid = el('div', 'wus-lanes');
      var relay = x.heat.lanes.some(function (l) { return l.legs && l.legs.length; });
      grid.style.setProperty('--nm', relay ? 20 : 22);
      x.heat.lanes.forEach(function (l) { grid.appendChild(laneRow(l)); });
      stage.appendChild(grid);
      fitNames(grid);
    }
    show();
    if (list.length > 1) rotate = setInterval(function () {
      stage.classList.add('fade');
      setTimeout(function () { i++; show(); stage.classList.remove('fade'); }, 480);
    }, hold() * 1000);
  }

  function stop() { cancelAnimationFrame(raf); raf = null; clearInterval(rotate); rotate = null; }
  function draw() { stop(); if (MODE === 'full') drawFull(); else drawBand(); }

  function show(on) {
    root.classList.toggle('on', on);
    if (opts.onShow) opts.onShow(on);
    clearInterval(tick);
    if (on) tick = setInterval(function () {
      var c = root.querySelector('.wus-c'); if (c) c.textContent = countdown();
    }, 15000);
    else stop();
  }

  if (q.has('nowarmup')) return;
  socket.on('warmup_slide', function (d) {
    data = d || {};
    if (data.on) {
      // the server re-sends on every heat change; only redraw (and restart the
      // scroll) when what's on the slide actually changed
      // (speed is read live by the scroll; the band's hold time needs a restart)
      var sig = JSON.stringify([data.title, data.subtitle, data.start, data.date, data.teams, data.events, data.school_of, data.results_url, data.stream,
                                MODE === 'band' ? hold() : 0]);
      if (sig !== sigShown || !root.classList.contains('on')) { sigShown = sig; draw(); }
    } else sigShown = '';
    show(!!data.on);
  });
};
