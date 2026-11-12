/* Remote refresh for screens nobody can touch (lobby TVs, OBS browser sources).
   - Settings > "Refresh all screens" sends 'reload' to every page.
   - When the server restarts or updates, pages see a new boot id on reconnect
     and reload themselves, so they always run the current page code.
   Only reloads once the server answers, so a TV never lands on an error page. */
window.mbAutoReload = function (socket) {
  var boot = null, pending = false;
  function safeReload() {
    if (pending) return; pending = true;
    (function tryIt() {
      fetch('/api/health', {cache: 'no-store'})
        .then(function (r) { if (r.ok) location.reload(); else throw 0; })
        .catch(function () { setTimeout(tryIt, 5000); });
    })();
  }
  socket.on('server_info', function (d) {
    if (!d || !d.boot) return;
    if (boot && d.boot !== boot) safeReload();
    boot = d.boot;
  });
  socket.on('reload', safeReload);
};

/* Go black: a full-screen black cover on the TV pages (meet board, scoreboard)
   while the server says so - the admin "Go black" button, or automatically
   after a couple of hours with no meet running. It lifts by itself when a
   race starts. The page keeps running underneath, so waking is instant.
   Add ?noblack to a page's URL to opt that screen out. */
window.mbBlackout = function (socket) {
  if (/[?&]noblack\b/.test(location.search)) return;
  var cover = document.createElement('div');
  cover.id = 'mbBlack';
  cover.style.cssText = 'position:fixed;inset:0;background:#000;z-index:2147483647;display:none;cursor:none';
  function attach() { (document.body || document.documentElement).appendChild(cover); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  socket.on('blackout', function (d) {
    var on = !!(d && d.black);
    cover.style.display = on ? 'block' : 'none';
    document.documentElement.style.cursor = on ? 'none' : '';
  });
};

/* Notice slide ("We need timers!"): full screen on the TV pages while it's on
   (nav bar button / Settings > Screens). Under Go black, over everything else.
   Add ?nonotice to a page's URL to opt that screen out. */
window.mbNotice = function (socket) {
  if (/[?&]nonotice\b/.test(location.search)) return;
  var root = document.createElement('div');
  root.id = 'mbNotice';
  root.style.cssText = 'position:fixed;inset:0;z-index:2147483300;display:none;align-items:center;justify-content:center;' +
    'flex-direction:column;text-align:center;padding:4vw;box-sizing:border-box;background:#0b1618;color:#fff;cursor:none;' +
    'font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif';
  root.innerHTML = '<div style="width:16vmin;height:16vmin;border-radius:50%;border:1.4vmin solid #ffd23f;position:relative;margin-bottom:5vmin">' +
    '<div style="position:absolute;left:50%;top:22%;width:1.4vmin;height:30%;background:#ffd23f;transform:translateX(-50%);border-radius:1vmin"></div>' +
    '<div style="position:absolute;left:50%;top:50%;width:1.4vmin;height:24%;background:#ffd23f;transform:translateX(-50%) rotate(120deg);transform-origin:50% 0;border-radius:1vmin"></div></div>' +
    '<div class="t" style="font-weight:800;font-size:13vmin;line-height:1.02;letter-spacing:-.01em;color:#ffd23f;text-transform:uppercase"></div>' +
    '<div class="x" style="font-weight:600;font-size:5.6vmin;line-height:1.25;margin-top:4vmin;max-width:80vw;color:#e8f1f3"></div>';
  function attach() { (document.body || document.documentElement).appendChild(root); }
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  socket.on('notice', function (d) {
    var on = !!(d && d.on);
    root.querySelector('.t').textContent = (d && d.title) || '';
    root.querySelector('.x').textContent = (d && d.text) || '';
    root.style.display = on ? 'flex' : 'none';
  });
};
