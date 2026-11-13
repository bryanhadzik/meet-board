/* Break slide: "Results so far" on the meet board TVs during the break between sessions -
   team scores, the top three of every event swum so far, and the events still to come.
   Turned on by hand (tab bar "Break slide" / Settings > Screens) or automatically a couple of
   minutes after the last heat of the session's last event. Comes down when the next race starts.
   Add ?nobreak to a page's URL to keep that screen off it.

   On the stream (the race overlay in OBS) it's a band along the bottom instead, so the camera
   still shows the pool: team scores on the left, then the events swum so far two at a time with
   their top three, changing every few seconds and looping, and what's still to come on the right.

     mbBreakSlide(socket)                                         meet boards (full screen)
     mbBreakSlide(socket, {mode: 'band', bandh: 284, onShow: fn})  stream overlay */
window.mbBreakSlide = function (socket, opts) {
  if (/[?&]nobreak\b/.test(location.search)) return;
  opts = opts || {};
  if (opts.mode === 'band') return breakBand(socket, opts);
  var css = document.createElement('style');
  css.textContent = [
    '#mbBreak{position:fixed;inset:0;z-index:2147483200;display:none;flex-direction:column;box-sizing:border-box;cursor:none;',
    '  font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;color:#fff;--u:calc(100vw/1920);',
    '  background:radial-gradient(120% 90% at 20% 0%,#0f6f80 0%,#0a3f4a 45%,#06171b 100%);padding:calc(40*var(--u)) calc(56*var(--u)) calc(30*var(--u))}',
    '#mbBreak.on{display:flex}',
    '#mbBreak .bs-hd{display:flex;align-items:flex-end;justify-content:space-between;gap:calc(30*var(--u));flex:0 0 auto}',
    '#mbBreak .bs-kick{display:inline-block;font-weight:800;letter-spacing:.22em;color:#0b1618;background:#ffd60a;border-radius:calc(8*var(--u));',
    '  font-size:calc(26*var(--u));padding:calc(6*var(--u)) calc(18*var(--u))}',
    '#mbBreak .bs-ttl{font-weight:800;font-size:calc(72*var(--u));line-height:1.05;margin-top:calc(12*var(--u))}',
    '#mbBreak .bs-sub{color:#bfe6ec;font-size:calc(28*var(--u));margin-top:calc(4*var(--u))}',
    '#mbBreak .bs-sc{display:flex;gap:calc(18*var(--u));flex-wrap:wrap;justify-content:flex-end}',
    '#mbBreak .bs-sci{background:rgba(4,20,24,.6);border:1px solid rgba(255,255,255,.12);border-radius:calc(14*var(--u));padding:calc(10*var(--u)) calc(22*var(--u));text-align:center;min-width:calc(200*var(--u))}',
    '#mbBreak .bs-sci .bs-n{font-size:calc(22*var(--u));color:#bfe6ec;font-weight:600;max-width:calc(320*var(--u));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#mbBreak .bs-sci .bs-p{font-size:calc(64*var(--u));font-weight:800;font-family:Consolas,"Cascadia Mono",monospace;color:#ffd60a;line-height:1.05}',
    '#mbBreak .bs-body{flex:1;min-height:0;display:flex;gap:calc(30*var(--u));margin-top:calc(24*var(--u))}',
    '#mbBreak .bs-view{flex:1;min-width:0;overflow:hidden;position:relative;',
    '  -webkit-mask-image:linear-gradient(180deg,#000 calc(100% - 60*var(--u)),transparent 100%);mask-image:linear-gradient(180deg,#000 calc(100% - 60*var(--u)),transparent 100%)}',
    '#mbBreak .bs-done{min-width:0;display:grid;will-change:transform;grid-template-columns:repeat(var(--cols,3),minmax(0,1fr));gap:calc(20*var(--u));align-content:start;overflow:hidden}',
    '#mbBreak .bs-ev{background:rgba(4,20,24,.55);border:1px solid rgba(255,255,255,.08);border-radius:calc(14*var(--u));padding:calc(14*var(--u)) calc(20*var(--u))}',
    '#mbBreak .bs-ev .bs-h{font-weight:700;font-size:calc(38*var(--u));margin-bottom:calc(6*var(--u));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#mbBreak .bs-ev .bs-h b{color:#3ddc84;margin-right:calc(8*var(--u))}',
    '#mbBreak .bs-r{display:flex;gap:calc(14*var(--u));align-items:baseline;font-size:calc(38*var(--u));padding:calc(5*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06)}',
    '#mbBreak .bs-r .bs-pl{font-weight:800;width:calc(38*var(--u));color:#ffd60a;flex:0 0 auto}',
    '#mbBreak .bs-r .bs-nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}',
    '#mbBreak .bs-r .bs-tm{font-family:Consolas,"Cascadia Mono",monospace;color:#bfe6ec;flex:0 0 auto;font-size:.88em}',
    '#mbBreak .bs-r .bs-tc{color:#8fb3ba;font-size:calc(22*var(--u));flex:0 0 auto}',
    '#mbBreak .bs-come{flex:0 0 calc(400*var(--u));background:rgba(4,20,24,.55);border:1px solid rgba(255,255,255,.08);border-radius:calc(12*var(--u));',
    '  padding:calc(14*var(--u)) calc(18*var(--u));overflow:hidden}',
    '#mbBreak .bs-come .bs-k{font-weight:800;letter-spacing:.14em;color:#3ddc84;font-size:calc(30*var(--u));margin-bottom:calc(8*var(--u))}',
    '#mbBreak .bs-come .bs-i{font-size:calc(30*var(--u));padding:calc(6*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#mbBreak .bs-come .bs-i b{color:#ffd60a;display:inline-block;min-width:calc(60*var(--u))}'
  ].join('\n');
  document.head.appendChild(css);
  var root = document.createElement('div');
  root.id = 'mbBreak';
  function attach() { (document.body || document.documentElement).appendChild(root); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }

  function draw(d) {
    root.textContent = '';
    var hd = el('div', 'bs-hd'), left = el('div');
    left.appendChild(el('span', 'bs-kick', d.final ? 'FINAL' : 'BREAK'));
    left.appendChild(el('div', 'bs-ttl', d.final ? 'Final results' : 'Results so far'));
    left.appendChild(el('div', 'bs-sub', d.final ? (d.meet ? d.meet + ' · ' : '') + 'thanks for coming!' : (d.meet ? d.meet + ' · ' : '') + 'the meet picks up after the break'));
    hd.appendChild(left);
    var sc = el('div', 'bs-sc');
    (d.scores || []).forEach(function (t) {
      var b = el('div', 'bs-sci'); b.appendChild(el('div', 'bs-n', t.name)); b.appendChild(el('div', 'bs-p', String(t.total))); sc.appendChild(b);
    });
    hd.appendChild(sc);
    root.appendChild(hd);
    var body = el('div', 'bs-body'), view = el('div', 'bs-view'), done = el('div', 'bs-done');
    view.appendChild(done);
    var evs = d.done || [];
    done.style.setProperty('--cols', d.final ? 3 : 2);   // big enough to read from the stands; it scrolls when it doesn't fit
    evs.forEach(function (ev) {
      var c = el('div', 'bs-ev'), h = el('div', 'bs-h');
      h.appendChild(el('b', '', 'E' + ev.no)); h.appendChild(document.createTextNode(ev.label || ''));
      c.appendChild(h);
      (ev.top || []).forEach(function (r) {
        var row = el('div', 'bs-r');
        row.appendChild(el('span', 'bs-pl', String(r.place)));
        row.appendChild(el('span', 'bs-nm', (r.name || '').replace(/ (High School|Swim Team)( Swimming)?(?= "|$)/, '')));
        row.appendChild(el('span', 'bs-tc', r.team || ''));
        row.appendChild(el('span', 'bs-tm', r.time || ''));
        c.appendChild(row);
      });
      done.appendChild(c);
    });
    if (!evs.length) done.appendChild(el('div', 'bs-sub', 'Results will show here once the results page has them.'));
    body.appendChild(view);
    if (!d.final && (d.coming || []).length) {
      var come = el('div', 'bs-come');
      come.appendChild(el('div', 'bs-k', 'STILL TO COME'));
      d.coming.forEach(function (ev) {
        var i = el('div', 'bs-i'); i.appendChild(el('b', '', String(ev.no))); i.appendChild(document.createTextNode(ev.label || '')); come.appendChild(i);
      });
      body.appendChild(come);
    }
    root.appendChild(body);
    // more events than fit: scroll them slowly upward, round and round (like the warm-up credits)
    cancelAnimationFrame(raf);
    requestAnimationFrame(function () {
      if (done.offsetHeight <= view.clientHeight + 4) return;
      var gap = parseFloat(getComputedStyle(done).rowGap) || 0;
      var copy = done.cloneNode(true); copy.style.marginTop = gap + 'px'; view.appendChild(copy);
      var loop = done.offsetHeight + gap, y = 0, last = null;
      function step(t) {
        if (last !== null) { y += (t - last) / 1000 * 30 * window.innerWidth / 1920; if (y >= loop) y -= loop; }
        last = t; done.style.transform = copy.style.transform = 'translateY(' + (-y).toFixed(1) + 'px)';
        raf = requestAnimationFrame(step);
      }
      raf = requestAnimationFrame(step);
    });
  }
  var raf = null;

  watch(socket, draw, root);
};

// Shared: follow the break slide's on/off and refresh its data every 30 s while it's up
// (results keep coming in during the break: late scoring).
function watch(socket, draw, root, onShow) {
  var last = null, lastSig = '';
  function set(on) {
    var was = root.classList.contains('on');
    root.classList.toggle('on', on);
    if (onShow && was !== on) onShow(on);
  }
  socket.on('break_slide', function (d) {
    last = d;
    var on = !!(d && d.on);
    if (on) { lastSig = JSON.stringify([d.done, d.scores, d.coming, d.final]); draw(d); }
    set(on);
  });
  // results keep coming in during the break (late scoring): refresh every 30 s while it's up
  setInterval(function () {
    if (!last || !last.on) return;
    fetch('/api/break_slide', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (d) {
      var sig = JSON.stringify([d.done, d.scores, d.coming, d.final]);
      if (d.on && sig !== lastSig) { lastSig = sig; draw(d); }
      last = d; set(!!d.on);
    }).catch(function () {});
  }, 30000);
}

// ---- stream band: same data, laid out to leave the camera picture above it ----
function breakBand(socket, opts) {
  var BANDH = opts.bandh || 392, HOLD = Math.max(3, +(new URLSearchParams(location.search).get('bkhold')) || 7);
  var css = document.createElement('style');
  css.textContent = [
    '#mbBreakBand{position:fixed;left:0;right:0;bottom:0;z-index:2147483200;display:flex;box-sizing:border-box;opacity:0;transition:opacity .5s ease;',
    '  pointer-events:none;font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;color:#fff;--u:calc(100vw/1920);',
    '  height:calc(' + BANDH + '*var(--u));background:#091113;align-items:stretch;gap:calc(34*var(--u));',
    '  padding:calc(22*var(--u)) calc(44*var(--u)) calc(84*var(--u))}',
    '#mbBreakBand *{box-sizing:border-box}',
    '#mbBreakBand.on{opacity:1}',
    '#mbBreakBand::before{content:"";position:absolute;left:0;right:0;top:calc(-5*var(--u));height:calc(5*var(--u));background:#ffd60a}',
    '#mbBreakBand .bb-left{flex:0 1 auto;max-width:calc(470*var(--u));display:flex;flex-direction:column;justify-content:center;min-width:0}',
    '#mbBreakBand .bb-kick{align-self:flex-start;font-weight:800;letter-spacing:.22em;color:#0b1618;background:#ffd60a;border-radius:calc(8*var(--u));',
    '  font-size:calc(18*var(--u));padding:calc(5*var(--u)) calc(14*var(--u))}',
    '#mbBreakBand .bb-ttl{font-weight:800;font-size:calc(38*var(--u));line-height:1.05;margin-top:calc(8*var(--u))}',
    '#mbBreakBand .bb-sc{display:flex;gap:calc(10*var(--u));margin-top:calc(10*var(--u));flex-wrap:wrap}',
    '#mbBreakBand .bb-sci{background:rgba(255,255,255,.07);border-radius:calc(10*var(--u));padding:calc(4*var(--u)) calc(12*var(--u));text-align:center}',
    '#mbBreakBand .bb-sci .bb-n{font-size:calc(15*var(--u));color:#bfe6ec;font-weight:700;letter-spacing:.06em}',
    '#mbBreakBand .bb-sci .bb-p{font-size:calc(30*var(--u));font-weight:800;font-family:Consolas,"Cascadia Mono",monospace;color:#ffd60a;line-height:1.05}',
    '#mbBreakBand .bb-stage{flex:1 1 0;min-width:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:calc(26*var(--u));align-content:center;transition:opacity .45s ease}',
    '#mbBreakBand .bb-stage.fade{opacity:0}',
    '#mbBreakBand .bb-ev{min-width:0}',
    '#mbBreakBand .bb-h{font-weight:700;font-size:calc(24*var(--u));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:calc(6*var(--u))}',
    '#mbBreakBand .bb-h b{color:#3ddc84;letter-spacing:.1em;margin-right:calc(10*var(--u));font-size:calc(19*var(--u))}',
    '#mbBreakBand .bb-r{display:flex;gap:calc(10*var(--u));align-items:baseline;font-size:calc(22*var(--u));padding:calc(3*var(--u)) 0;border-top:1px solid rgba(255,255,255,.07)}',
    '#mbBreakBand .bb-r .bb-pl{font-weight:800;width:calc(22*var(--u));color:#ffd60a;flex:0 0 auto}',
    '#mbBreakBand .bb-r .bb-nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}',
    '#mbBreakBand .bb-r .bb-tc{color:#8fb3ba;font-size:calc(16*var(--u));flex:0 0 auto}',
    '#mbBreakBand .bb-r .bb-tm{font-family:Consolas,"Cascadia Mono",monospace;color:#bfe6ec;flex:0 0 auto}',
    '#mbBreakBand .bb-empty{grid-column:1/-1;color:#bfe6ec;font-size:calc(24*var(--u))}',
    '#mbBreakBand .bb-come{flex:0 0 calc(330*var(--u));display:flex;flex-direction:column;justify-content:center;min-width:0}',
    '#mbBreakBand .bb-come .bb-k{font-weight:800;letter-spacing:.14em;color:#3ddc84;font-size:calc(17*var(--u));margin-bottom:calc(6*var(--u))}',
    '#mbBreakBand .bb-come .bb-i{font-size:calc(19*var(--u));padding:calc(3*var(--u)) 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#dfeef1}',
    '#mbBreakBand .bb-come .bb-i b{color:#ffd60a;display:inline-block;min-width:calc(36*var(--u))}'
  ].join('\n');
  document.head.appendChild(css);
  var root = document.createElement('div');
  root.id = 'mbBreakBand';
  function attach() { (document.body || document.documentElement).appendChild(root); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  var rotate = null;

  function evCard(ev) {
    var c = el('div', 'bb-ev'), h = el('div', 'bb-h');
    h.appendChild(el('b', '', 'EVENT ' + ev.no)); h.appendChild(document.createTextNode(ev.label || ''));
    c.appendChild(h);
    (ev.top || []).forEach(function (r) {
      var row = el('div', 'bb-r');
      row.appendChild(el('span', 'bb-pl', String(r.place)));
      row.appendChild(el('span', 'bb-nm', (r.name || '').replace(/ (High School|Swim Team)( Swimming)?(?= "|$)/, '')));   // relays: Grantsville "A"
      row.appendChild(el('span', 'bb-tc', r.team || ''));
      row.appendChild(el('span', 'bb-tm', r.time || ''));
      c.appendChild(row);
    });
    return c;
  }
  function draw(d) {
    clearInterval(rotate);
    root.textContent = '';
    var left = el('div', 'bb-left');
    left.appendChild(el('span', 'bb-kick', d.final ? 'FINAL' : 'BREAK'));
    left.appendChild(el('div', 'bb-ttl', d.final ? 'Final results' : 'Results so far'));
    if ((d.scores || []).length) {
      var sc = el('div', 'bb-sc');
      d.scores.forEach(function (t) {
        var b = el('div', 'bb-sci');
        b.appendChild(el('div', 'bb-n', t.code || (t.name || '').replace(/ (High School|Swim Team)( Swimming)?$/, '')));
        b.appendChild(el('div', 'bb-p', String(t.total)));
        sc.appendChild(b);
      });
      left.appendChild(sc);
    }
    root.appendChild(left);
    var stage = el('div', 'bb-stage');
    root.appendChild(stage);
    if (!d.final && (d.coming || []).length) {
      var come = el('div', 'bb-come');
      come.appendChild(el('div', 'bb-k', 'STILL TO COME'));
      d.coming.slice(0, 4).forEach(function (ev) {
        var i = el('div', 'bb-i'); i.appendChild(el('b', '', String(ev.no))); i.appendChild(document.createTextNode(ev.label || '')); come.appendChild(i);
      });
      if (d.coming.length > 4) come.appendChild(el('div', 'bb-i', '+ ' + (d.coming.length - 4) + ' more'));
      root.appendChild(come);
    }
    var evs = d.done || [], pages = [];
    for (var k = 0; k < evs.length; k += 2) pages.push(evs.slice(k, k + 2));
    var i = 0;
    function show() {
      stage.textContent = '';
      if (!pages.length) { stage.appendChild(el('div', 'bb-empty', 'Results will show here once the results page has them.')); return; }
      pages[i % pages.length].forEach(function (ev) { stage.appendChild(evCard(ev)); });
    }
    show();
    if (pages.length > 1) rotate = setInterval(function () {
      stage.classList.add('fade');
      setTimeout(function () { i++; show(); stage.classList.remove('fade'); }, 480);
    }, HOLD * 1000);
  }
  watch(socket, draw, root, function (on) {
    if (!on) clearInterval(rotate);
    if (opts.onShow) opts.onShow(on);
  });
}
