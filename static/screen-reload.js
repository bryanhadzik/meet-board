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
