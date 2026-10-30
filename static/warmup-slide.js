/* Warm-up slide: the meet, the start time, and a slow listing of every event
   and the swimmers in each heat. Shown before the meet on the spectator
   boards (full screen, the heats scroll slowly up like credits) and on the
   stream (a band along the bottom that steps through the heats one at a time,
   so the camera still shows warm-ups).

   Turned on/off with the "Warm-up slide" button in the tab bar or on
   Settings -> Warm-up slide, and taken down automatically when the first race
   starts.

     mbWarmupSlide(socket, {mode: 'full'})                     meet boards
     mbWarmupSlide(socket, {mode: 'band', bandh: 392,           stream overlay
                            onShow: function (shown) {...}})

   URL options on the page: ?wuspeed=30 (full: scroll speed, px/s at 1080p),
   ?wuhold=7 (band: seconds per heat), ?nowarmup (never show it here).
*/
window.mbWarmupSlide = function (socket, opts) {
  opts = opts || {};
  var MODE = opts.mode === 'band' ? 'band' : 'full';
  var BANDH = opts.bandh || 392;
  var q = new URLSearchParams(location.search);
  var SPEED = Math.max(5, Math.min(200, parseFloat(q.get('wuspeed')) || 30));   // px per second at 1920 wide
  var HOLD = Math.max(3, Math.min(60, parseFloat(q.get('wuhold')) || 7));       // band: seconds per heat
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

    /* ---- band (stream): one heat at a time, clear of the player controls ---- */
    '.wus.band{left:0;right:0;bottom:0;height:calc(var(--bh)*var(--u));background:#091113;align-items:stretch;',
    '  padding:calc(22*var(--u)) calc(44*var(--u)) calc(84*var(--u));gap:calc(36*var(--u))}',
    '.wus.band::before{content:"";position:absolute;left:0;right:0;top:calc(-5*var(--u));height:calc(5*var(--u));background:#3ddc84}',
    '.wus.band .wus-left{flex:0 0 calc(440*var(--u));display:flex;flex-direction:column;justify-content:center;min-width:0}',
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
    '.wus.band .wus-nm{font-size:calc(var(--nm,22)*var(--u))}',
    '.wus.band .wus-legs{font-size:calc(14*var(--u))}',
    '.wus.band .wus-tm{display:none}'
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
    var head = el('div', 'wus-head');
    head.appendChild(titleBlock());
    var w = whenBlock(); if (w) head.appendChild(w);
    root.appendChild(head);
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
    if ((data.teams || []).length) root.appendChild(teams);
    var view = el('div', 'wus-view'), track = el('div', 'wus-track');
    view.appendChild(track); root.appendChild(view);
    var list = heats();
    if (!list.length) { track.appendChild(el('div', 'wus-empty', 'Start lists will appear here once they are loaded.')); return; }
    var grid = el('div', 'wus-grid');
    list.forEach(function (x) { grid.appendChild(heatCard(x)); });
    track.appendChild(grid);
    // Scroll only if it doesn't all fit; a second copy makes the loop seamless.
    requestAnimationFrame(function () {
      if (grid.offsetHeight <= view.clientHeight) return;
      track.appendChild(grid.cloneNode(true));
      var loop = grid.offsetHeight, y = 0, last = null, pxs = SPEED * window.innerWidth / 1920;
      function step(t) {
        if (last !== null) { y += (t - last) / 1000 * pxs; if (y >= loop) y -= loop; }
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
    var w = whenBlock(); if (w) root.appendChild(w);
    var list = heats(), i = 0;
    function show() {
      stage.textContent = '';
      if (!list.length) return;
      var x = list[i % list.length];
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
    }
    show();
    if (list.length > 1) rotate = setInterval(function () {
      stage.classList.add('fade');
      setTimeout(function () { i++; show(); stage.classList.remove('fade'); }, 480);
    }, HOLD * 1000);
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
      var sig = JSON.stringify([data.title, data.subtitle, data.start, data.date, data.teams, data.events]);
      if (sig !== sigShown || !root.classList.contains('on')) { sigShown = sig; draw(); }
    } else sigShown = '';
    show(!!data.on);
  });
};
