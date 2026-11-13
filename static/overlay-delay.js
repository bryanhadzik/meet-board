/* Overlay delay: hold every scoreboard update for N seconds before the overlay shows it, so the
   data lines up with a camera feed that reaches OBS late. Settings > Stream sets N for every
   overlay (live, no OBS refresh needed); ?delay=N on one overlay's URL overrides it for that one.
   Connection events (connect, disconnect, reload) are never delayed.
   Call right after io.connect(), before any socket.on(). */
window.mbOverlayDelay = function (socket, initial) {
  var q = new URLSearchParams(location.search);
  var fixed = q.has('delay') ? Math.max(0, parseFloat(q.get('delay')) || 0) : null;
  var delay = fixed !== null ? fixed : Math.max(0, +initial || 0);
  var skip = {connect: 1, disconnect: 1, connect_error: 1, reload: 1, server_info: 1, overlay_delay: 1};
  var on = socket.on.bind(socket);
  socket.on = function (ev, fn) {
    if (skip[ev] || typeof fn !== 'function') return on(ev, fn);
    return on(ev, function () {
      var a = arguments, self = this;
      if (delay > 0) setTimeout(function () { fn.apply(self, a); }, delay * 1000);
      else fn.apply(self, a);
    });
  };
  on('overlay_delay', function (d) {
    if (fixed === null && d && d.seconds != null) delay = Math.max(0, +d.seconds || 0);
  });
  return function () { return delay; };
};
