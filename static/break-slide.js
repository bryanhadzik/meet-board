/* Break slide: "Results so far" on the meet board TVs during the break between sessions -
   team scores, the top three of every event swum so far, and the events still to come.
   Turned on by hand (tab bar "Break slide" / Settings > Screens) or automatically a couple of
   minutes after the last heat of the session's last event. Comes down when the next race starts.
   Add ?nobreak to a page's URL to keep that screen off it. */
window.mbBreakSlide = function (socket) {
  if (/[?&]nobreak\b/.test(location.search)) return;
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
    '#mbBreak .bs-done{flex:1;min-width:0;display:grid;grid-template-columns:repeat(var(--cols,3),minmax(0,1fr));gap:calc(14*var(--u));align-content:start;overflow:hidden}',
    '#mbBreak .bs-ev{background:rgba(4,20,24,.55);border:1px solid rgba(255,255,255,.08);border-radius:calc(12*var(--u));padding:calc(10*var(--u)) calc(14*var(--u))}',
    '#mbBreak .bs-ev .bs-h{font-weight:700;font-size:calc(25*var(--u));margin-bottom:calc(4*var(--u));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#mbBreak .bs-ev .bs-h b{color:#3ddc84;margin-right:calc(8*var(--u))}',
    '#mbBreak .bs-r{display:flex;gap:calc(10*var(--u));align-items:baseline;font-size:calc(24*var(--u));padding:calc(2*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06)}',
    '#mbBreak .bs-r .bs-pl{font-weight:800;width:calc(26*var(--u));color:#ffd60a;flex:0 0 auto}',
    '#mbBreak .bs-r .bs-nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}',
    '#mbBreak .bs-r .bs-tm{font-family:Consolas,"Cascadia Mono",monospace;color:#bfe6ec;flex:0 0 auto}',
    '#mbBreak .bs-r .bs-tc{color:#8fb3ba;font-size:calc(17*var(--u));flex:0 0 auto}',
    '#mbBreak .bs-come{flex:0 0 calc(420*var(--u));background:rgba(4,20,24,.55);border:1px solid rgba(255,255,255,.08);border-radius:calc(12*var(--u));',
    '  padding:calc(14*var(--u)) calc(18*var(--u));overflow:hidden}',
    '#mbBreak .bs-come .bs-k{font-weight:800;letter-spacing:.14em;color:#3ddc84;font-size:calc(22*var(--u));margin-bottom:calc(8*var(--u))}',
    '#mbBreak .bs-come .bs-i{font-size:calc(22*var(--u));padding:calc(4*var(--u)) 0;border-top:1px solid rgba(255,255,255,.06);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#mbBreak .bs-come .bs-i b{color:#ffd60a;display:inline-block;min-width:calc(44*var(--u))}'
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
    left.appendChild(el('span', 'bs-kick', 'BREAK'));
    left.appendChild(el('div', 'bs-ttl', 'Results so far'));
    left.appendChild(el('div', 'bs-sub', (d.meet ? d.meet + ' · ' : '') + 'the meet picks up after the break'));
    hd.appendChild(left);
    var sc = el('div', 'bs-sc');
    (d.scores || []).forEach(function (t) {
      var b = el('div', 'bs-sci'); b.appendChild(el('div', 'bs-n', t.name)); b.appendChild(el('div', 'bs-p', String(t.total))); sc.appendChild(b);
    });
    hd.appendChild(sc);
    root.appendChild(hd);
    var body = el('div', 'bs-body'), done = el('div', 'bs-done');
    var evs = d.done || [];
    done.style.setProperty('--cols', evs.length > 12 ? 4 : 3);
    evs.forEach(function (ev) {
      var c = el('div', 'bs-ev'), h = el('div', 'bs-h');
      h.appendChild(el('b', '', 'E' + ev.no)); h.appendChild(document.createTextNode(ev.label || ''));
      c.appendChild(h);
      (ev.top || []).forEach(function (r) {
        var row = el('div', 'bs-r');
        row.appendChild(el('span', 'bs-pl', String(r.place)));
        row.appendChild(el('span', 'bs-nm', (r.name || '').replace(/ High School( Swimming)?/, '')));
        row.appendChild(el('span', 'bs-tc', r.team || ''));
        row.appendChild(el('span', 'bs-tm', r.time || ''));
        c.appendChild(row);
      });
      done.appendChild(c);
    });
    if (!evs.length) done.appendChild(el('div', 'bs-sub', 'Results will show here once the results page has them.'));
    body.appendChild(done);
    if ((d.coming || []).length) {
      var come = el('div', 'bs-come');
      come.appendChild(el('div', 'bs-k', 'STILL TO COME'));
      d.coming.forEach(function (ev) {
        var i = el('div', 'bs-i'); i.appendChild(el('b', '', String(ev.no))); i.appendChild(document.createTextNode(ev.label || '')); come.appendChild(i);
      });
      body.appendChild(come);
    }
    root.appendChild(body);
  }

  var last = null;
  socket.on('break_slide', function (d) {
    last = d;
    var on = !!(d && d.on);
    if (on) draw(d);
    root.classList.toggle('on', on);
  });
  // results keep coming in during the break (late scoring): refresh every 30 s while it's up
  setInterval(function () {
    if (!last || !last.on) return;
    fetch('/api/break_slide', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (d) {
      last = d; if (d.on) draw(d); root.classList.toggle('on', !!d.on);
    }).catch(function () {});
  }, 30000);
};
