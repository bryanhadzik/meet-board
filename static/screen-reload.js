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
