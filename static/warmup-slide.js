/* Warm-up slide: the meet, the teams and the start time, shown before the
   meet on the spectator boards (full screen) and on the stream (a band along
   the bottom, so the camera still shows warm-ups).

   Turned on/off on Settings -> Screens or the "Warm-up slide" button in the
   tab bar, and taken down automatically when the first race starts.

     mbWarmupSlide(socket, {mode: 'full'})                     meet boards
     mbWarmupSlide(socket, {mode: 'band', bandh: 392,           stream overlay
                            onShow: function (shown) {...}})
*/
window.mbWarmupSlide = function (socket, opts) {
  opts = opts || {};
  var MODE = opts.mode === 'band' ? 'band' : 'full';
  var BANDH = opts.bandh || 392;
  var data = null, timer = null;

  var css = document.createElement('style');
  css.textContent = [
    '.wus{position:fixed;z-index:2147483000;font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;color:#fff;',
    '  --u:calc(100vw/1920);opacity:0;transition:opacity .5s ease;pointer-events:none;display:flex}',
    '.wus.on{opacity:1}',
    '.wus.full{inset:0;flex-direction:column;justify-content:space-between;padding:calc(70*var(--u)) calc(90*var(--u)) calc(60*var(--u));',
    '  background:radial-gradient(120% 90% at 20% 0%,#0f6f80 0%,#0a3f4a 45%,#06171b 100%)}',
    '.wus.full::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.12;',
    '  background:repeating-linear-gradient(180deg,transparent 0 calc(118*var(--u)),#7fe0ff calc(118*var(--u)) calc(122*var(--u)))}',
    '.wus .wus-kick{display:inline-block;font-weight:800;letter-spacing:.22em;font-size:calc(30*var(--u));color:#0b1618;',
    '  background:#3ddc84;padding:calc(8*var(--u)) calc(20*var(--u));border-radius:calc(8*var(--u))}',
    '.wus .wus-ttl{font-weight:800;line-height:1.02;letter-spacing:-.01em}',
    '.wus.full .wus-ttl{font-size:calc(108*var(--u));margin-top:calc(26*var(--u))}',
    '.wus .wus-sub{color:#bfe6ec;font-weight:500}',
    '.wus.full .wus-sub{font-size:calc(40*var(--u));margin-top:calc(10*var(--u))}',
    '.wus .wus-teams{display:grid;gap:calc(22*var(--u))}',
    '.wus.full .wus-teams{grid-template-columns:repeat(var(--cols),minmax(0,1fr));margin:calc(40*var(--u)) 0}',
    '.wus .wus-team{display:flex;align-items:center;gap:calc(18*var(--u));background:rgba(255,255,255,.07);min-width:0;',
    '  border-radius:calc(14*var(--u));padding:calc(16*var(--u)) calc(20*var(--u));border-bottom:calc(6*var(--u)) solid var(--tc,#3a4d52)}',
    '.wus .wus-logo{flex:0 0 auto;background:#fff;border-radius:calc(12*var(--u));display:flex;align-items:center;justify-content:center;',
    '  padding:calc(6*var(--u));box-shadow:0 0 0 calc(3*var(--u)) var(--tc,transparent)}',
    '.wus .wus-logo img{max-width:100%;max-height:100%;object-fit:contain;display:block}',
    '.wus .wus-logo.wus-code{background:var(--tc,#2a3b40);color:#fff;font-weight:800;font-family:Consolas,monospace}',
    '.wus.full .wus-logo{width:calc(110*var(--u));height:calc(110*var(--u));font-size:calc(30*var(--u))}',
    '.wus .wus-tname{font-weight:700;line-height:1.1;min-width:0;overflow-wrap:break-word;hyphens:auto}',
    '.wus.full .wus-tname{font-size:calc(40*var(--u))}',
    '.wus .wus-when{display:flex;align-items:baseline;gap:calc(26*var(--u));flex-wrap:wrap}',
    '.wus .wus-when .wus-k{font-weight:700;letter-spacing:.16em;color:#9fd4dc}',
    '.wus .wus-when .wus-t{font-weight:800;font-family:Consolas,"Cascadia Mono",monospace;color:#ffd60a}',
    '.wus .wus-when .wus-c{color:#bfe6ec;font-weight:600}',
    '.wus.full .wus-when .wus-k{font-size:calc(34*var(--u))} .wus.full .wus-when .wus-t{font-size:calc(92*var(--u))} .wus.full .wus-when .wus-c{font-size:calc(44*var(--u))}',
    /* band (stream): along the bottom, clear of the player controls */
    '.wus.band{left:0;right:0;bottom:0;height:calc(var(--bh)*var(--u));background:#091113;align-items:stretch;',
    '  padding:calc(24*var(--u)) calc(44*var(--u)) calc(84*var(--u));gap:calc(40*var(--u))}',
    '.wus.band::before{content:"";position:absolute;left:0;right:0;top:calc(-5*var(--u));height:calc(5*var(--u));background:#3ddc84}',
    '.wus.band .wus-left{flex:0 0 calc(540*var(--u));display:flex;flex-direction:column;justify-content:center;min-width:0}',
    '.wus.band .wus-kick{align-self:flex-start;font-size:calc(20*var(--u))}',
    '.wus.band .wus-ttl{font-size:calc(50*var(--u));margin-top:calc(12*var(--u))}',
    '.wus.band .wus-sub{font-size:calc(24*var(--u));margin-top:calc(4*var(--u))}',
    '.wus.band .wus-teams{flex:1 1 0;min-width:0;grid-template-columns:repeat(var(--cols),minmax(0,1fr));align-content:center;gap:calc(12*var(--u))}',
    '.wus.band .wus-team{flex-direction:column;justify-content:flex-start;text-align:center;padding:calc(8*var(--u)) calc(6*var(--u));gap:calc(6*var(--u));border-bottom-width:calc(4*var(--u))}',
    '.wus.band .wus-logo{width:calc(var(--lg)*var(--u));height:calc(var(--lg)*var(--u));font-size:calc(16*var(--u))}',
    '.wus.band .wus-tname{font-size:calc(var(--tn)*var(--u));display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.wus.band .wus-when .wus-t{white-space:nowrap}',
    '.wus.band .wus-teams.wus-short .wus-tname{-webkit-line-clamp:1}',
    '.wus.band .wus-teams.wus-short .wus-team{padding:calc(5*var(--u)) calc(6*var(--u));gap:calc(3*var(--u))}',
    '.wus.band .wus-teams.wus-short{gap:calc(8*var(--u))}',
    '.wus.band .wus-when{flex:0 0 auto;min-width:max-content;flex-wrap:nowrap;flex-direction:column;justify-content:center;align-items:flex-end;gap:calc(4*var(--u));text-align:right}',
    '.wus.band .wus-when .wus-k{font-size:calc(20*var(--u))} .wus.band .wus-when .wus-t{font-size:calc(64*var(--u))} .wus.band .wus-when .wus-c{font-size:calc(26*var(--u))}'
  ].join('\n');
  document.head.appendChild(css);

  var root = document.createElement('div');
  root.className = 'wus ' + MODE;
  root.style.setProperty('--bh', BANDH);
  function attach() { (document.body || document.documentElement).appendChild(root); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);

  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
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
    var d = new Date(+p[0], +p[1] - 1, +p[2]), now = new Date();
    if (d.toDateString() === now.toDateString()) return '';      // it's today: no need to say so
    return d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'});
  }

  function draw() {
    root.textContent = '';
    var left = el('div', 'wus-left');
    left.appendChild(el('div', 'wus-kick', 'WARM-UPS'));
    left.appendChild(el('div', 'wus-ttl', data.title || 'Swim meet'));
    var sub = [data.subtitle, dateLine()].filter(Boolean).join(' · ');
    if (sub) left.appendChild(el('div', 'wus-sub', sub));

    var teams = el('div', 'wus-teams');
    var list = data.teams || [];
    var cols = MODE === 'band' ? Math.max(1, list.length > 7 ? Math.ceil(list.length / 2) : list.length)
                               : (list.length <= 4 ? Math.max(list.length, 1) : list.length <= 6 ? 3 : 4);
    teams.style.setProperty('--cols', cols);
    // the band is a fixed height: one row of teams gets big logos, two rows smaller ones
    var twoRows = MODE === 'band' && list.length > 7;
    var short = MODE === 'band' && BANDH < 340;          // e.g. the strip layout's 284 px band
    teams.style.setProperty('--lg', twoRows ? (short ? 40 : 54) : (short ? 56 : 80));
    teams.style.setProperty('--tn', twoRows ? (short ? 15 : 18) : (short ? 18 : 22));
    if (short) teams.classList.add('wus-short');
    list.forEach(function (t) {
      var row = el('div', 'wus-team');
      if (t.color) row.style.setProperty('--tc', t.color);
      var lg = el('div', 'wus-logo' + (t.logo ? '' : ' wus-code'));
      if (t.logo) { var im = el('img'); im.alt = ''; im.src = '/team_logo/' + encodeURIComponent(t.code) + '?v=' + t.logo; lg.appendChild(im); }
      else lg.textContent = t.code;
      row.appendChild(lg);
      row.appendChild(el('div', 'wus-tname', t.name));
      teams.appendChild(row);
    });

    var when = el('div', 'wus-when');
    var sd = startDate();
    if (sd) {
      when.appendChild(el('span', 'wus-k', 'MEET STARTS'));
      when.appendChild(el('span', 'wus-t', clock(sd)));
      when.appendChild(el('span', 'wus-c', countdown()));
    }
    if (MODE === 'full') {
      root.appendChild(left);
      if (list.length) root.appendChild(teams);
      root.appendChild(when);
    } else {
      root.appendChild(left);
      root.appendChild(teams);
      if (sd) root.appendChild(when);
    }
  }

  function show(on) {
    root.classList.toggle('on', on);
    if (opts.onShow) opts.onShow(on);
    clearInterval(timer);
    if (on) timer = setInterval(function () {
      var c = root.querySelector('.wus-when .wus-c'); if (c) c.textContent = countdown();
    }, 15000);
  }

  if (/[?&]nowarmup\b/.test(location.search)) return;
  socket.on('warmup_slide', function (d) {
    data = d || {};
    if (data.on) draw();
    show(!!data.on);
  });
};
