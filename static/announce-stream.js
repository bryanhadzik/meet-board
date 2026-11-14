/* Announcements on the stream: the race overlay in OBS plays each heat announcement (and the
   one-off ones) itself, so viewers hear the clean recording instead of the PA through the camera.
   meet-board mutes the camera audio while it plays: this page says when it starts and stops.

   Only when "Play announcements on the stream" is on (Music page -> Stream audio). In OBS the
   browser source needs "Control audio via OBS" so the sound goes into the stream mix.
   ?annaudio=0 on an overlay's URL keeps that overlay silent (one overlay should play them).

     mbAnnounceStream(socket)     after mbOverlayDelay, so the audio lines up with the delayed overlay
*/
window.mbAnnounceStream = function (socket) {
  var q = new URLSearchParams(location.search);
  if (q.get('annaudio') === '0') return;
  var player = new Audio(), queue = [], busy = false, cur = 0, safety = null;
  player.preload = 'auto';

  function state(playing) {
    try { socket.emit('announce_stream_state', {id: cur, playing: playing}); } catch (e) {}
  }
  function done() {
    busy = false; clearTimeout(safety);
    state(false);
  }
  function step() {
    var it = queue.shift();
    if (!it) { done(); return; }
    if (it.url) {
      player.src = it.url;
      var p = player.play();
      if (p && p.catch) p.catch(function () { step(); });
      return;
    }
    if (it.say && window.speechSynthesis && window.SpeechSynthesisUtterance) {
      var u = new SpeechSynthesisUtterance(it.say);
      u.rate = 0.95; u.onend = u.onerror = function () { step(); };
      window.speechSynthesis.speak(u);
      return;
    }
    step();                                    // no clip and no voice here: skip that piece
  }
  player.addEventListener('ended', function () { setTimeout(step, 120); });
  player.addEventListener('error', function () { step(); });

  socket.on('announce_stream', function (d) {
    if (!d || !d.items || !d.items.length) return;
    if (busy) { try { player.pause(); } catch (e) {} if (window.speechSynthesis) window.speechSynthesis.cancel(); }
    cur = d.id; queue = d.items.slice(); busy = true;
    state(true);                               // camera muted first, then the voice
    clearTimeout(safety);
    safety = setTimeout(function () { if (busy) { queue = []; try { player.pause(); } catch (e) {} done(); } }, 60000);
    setTimeout(step, 400);
  });
};
