/* Phone results on the spectator boards: a QR code that opens the public results page.

   Settings -> Phone results sets the address and how it shows:
     corner  a small code + the address in the bottom-right corner, always
     slide   corner, plus every few minutes between races a full-screen slide with a code big
             enough to scan from the stands (about 20 ft from a 65" TV)
     off     nothing

     mbResultsQr({busy: function () { return true while a race is running or results are up },
                  dock: element})   dock: put the small code at the bottom of this column (so it pushes
                                    the rows up instead of covering them); without it, a fixed corner card
   ?noresultsqr on the page URL hides it on that screen.
*/
window.mbResultsQr = function (opts) {
  opts = opts || {};
  if (new URLSearchParams(location.search).has('noresultsqr')) return;
  var cfg = null, svg = '', corner, slide, lastSlide = Date.now(), slideUntil = 0;

  var css = document.createElement('style');
  css.textContent = [
    '.rqr{position:fixed;z-index:2147482000;font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;--u:calc(100vw/1920);display:none}',
    '.rqr.on{display:flex}',
    '.rqr-c{right:calc(22*var(--u));bottom:calc(22*var(--u));align-items:center;gap:calc(14*var(--u));background:rgba(6,23,27,.88);',
    '  border:1px solid rgba(255,255,255,.18);border-radius:calc(14*var(--u));padding:calc(10*var(--u)) calc(16*var(--u)) calc(10*var(--u)) calc(10*var(--u))}',
    '.rqr-c .q{width:calc(150*var(--u));height:calc(150*var(--u));background:#fff;border-radius:calc(8*var(--u));padding:calc(6*var(--u))}',
    '.rqr-c .k{color:#3ddc84;font-weight:800;letter-spacing:.14em;font-size:calc(19*var(--u))}',
    '.rqr-c .a{color:#fff;font-weight:600;font-size:calc(22*var(--u));margin-top:calc(4*var(--u));max-width:calc(380*var(--u));word-break:break-all}',
    '.rqr-s{inset:0;align-items:center;justify-content:center;gap:calc(90*var(--u));',
    '  background:radial-gradient(120% 90% at 20% 0%,#0f6f80 0%,#0a3f4a 45%,#06171b 100%);opacity:0;transition:opacity .6s}',
    '.rqr-s.show{opacity:1}',
    '.rqr-s .q{width:calc(860*var(--u));height:calc(860*var(--u));background:#fff;border-radius:calc(24*var(--u));padding:calc(28*var(--u))}',
    '.rqr-s .t{color:#fff;max-width:calc(760*var(--u))}',
    '.rqr-s .k{display:inline-block;color:#0b1618;background:#3ddc84;font-weight:800;letter-spacing:.2em;font-size:calc(30*var(--u));',
    '  padding:calc(6*var(--u)) calc(18*var(--u));border-radius:calc(8*var(--u))}',
    '.rqr-s .h{font-weight:800;font-size:calc(92*var(--u));line-height:1.02;margin-top:calc(22*var(--u))}',
    '.rqr-s .p{color:#bfe6ec;font-size:calc(38*var(--u));margin-top:calc(24*var(--u));line-height:1.3}',
    '.rqr-s .a{color:#ffd60a;font-weight:700;font-size:calc(44*var(--u));margin-top:calc(30*var(--u));word-break:break-all}',
    '.rqr-d{position:static;flex:0 0 auto;align-items:center;gap:.9cqw;padding:.8cqw 1.2cqw;border-top:.2cqw solid #24393e;background:#0b1719}',
    '.rqr-d .q{width:8.4cqw;height:8.4cqw;flex:0 0 auto;background:#fff;border-radius:.4cqw;padding:.35cqw}',
    '.rqr-d .k{color:#3ddc84;font-weight:800;letter-spacing:.12em;font-size:1cqw;line-height:1.25}',
    '.rqr-d .a{color:#fff;font-weight:600;font-size:1.05cqw;margin-top:.4cqw;word-break:break-all;line-height:1.25}',
    '.rqr svg{width:100%;height:100%;display:block}'
  ].join('\n');
  document.head.appendChild(css);

  corner = document.createElement('div');
  corner.className = opts.dock ? 'rqr rqr-d' : 'rqr rqr-c';
  corner.innerHTML = '<div class="q"></div><div><div class="k">RESULTS ON YOUR PHONE</div><div class="a"></div></div>';
  slide = document.createElement('div');
  slide.className = 'rqr rqr-s';
  slide.innerHTML = '<div class="q"></div><div class="t"><span class="k">LIVE RESULTS</span><div class="h">Results on your phone</div>' +
    '<div class="p">Point your camera at the code. Every event, place and time, plus team scores &mdash; updated through the meet.</div><div class="a"></div></div>';
  (opts.dock || document.body).appendChild(corner);
  document.body.appendChild(slide);

  function short(u) { return (u || '').replace(/^https?:\/\//, '').replace(/\/$/, ''); }
  function busy() { try { return !!(opts.busy && opts.busy()); } catch (e) { return false; } }

  function load() {
    fetch('/api/results_qr', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (d) {
      var changed = !cfg || d.url !== cfg.url;
      cfg = d;
      if (changed && d.url) {
        return fetch('/results_qr.svg?v=' + encodeURIComponent(d.url), {cache: 'no-store'}).then(function (r) { return r.text(); })
          .then(function (t) { svg = t; corner.querySelector('.q').innerHTML = t; slide.querySelector('.q').innerHTML = t; });
      }
    }).catch(function () {}).then(apply);
  }

  function apply() {
    var on = cfg && cfg.url && cfg.mode !== 'off' && svg;
    corner.querySelector('.a').textContent = short(cfg && cfg.url);
    slide.querySelector('.a').textContent = short(cfg && cfg.url);
    var showSlide = on && cfg.mode === 'slide' && Date.now() < slideUntil && !busy();
    corner.classList.toggle('on', !!on && !showSlide);
    if (showSlide) { slide.classList.add('on'); requestAnimationFrame(function () { slide.classList.add('show'); }); }
    else { slide.classList.remove('show'); setTimeout(function () { if (!slide.classList.contains('show')) slide.classList.remove('on'); }, 650); }
  }

  setInterval(function () {
    if (!cfg || cfg.mode !== 'slide' || !cfg.url) return;
    var now = Date.now();
    if (now >= slideUntil && now - lastSlide >= 60000 * (cfg.every || 4) && !busy()) { lastSlide = now; slideUntil = now + 1000 * (cfg.secs || 15); }
    if (busy() && now < slideUntil) slideUntil = 0;        // a race started: get out of the way
    apply();
  }, 1000);
  load();
  setInterval(load, 60000);
};
