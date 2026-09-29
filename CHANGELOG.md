# Changelog

## 0.2.9

### Added
- OBS stream control on the Settings page (Stream card): start/stop, OBS-connected
  and live pills, uptime, outbound Mbps, dropped-frame percentage.
- `obs_client.py` — obs-websocket v5 client (auth handshake, output events,
  2 s status polling) using `simple-websocket`, already a dependency.
- `GET /api/obs` (read-only status, never includes the password) and
  `POST /api/obs/stream` `{"action": "start" | "stop"}`.
- OBS WebSocket URL and password on the Settings page (default
  `ws://127.0.0.1:4455`); a blank password field keeps the saved one.

### Notes
- Stream control degrades safely: the boards, the overlay and uploads are
  unaffected when OBS is closed, crashed or the password is wrong.
- Reconnects every 5 s forever, including when meet-board starts before OBS
  (the boot-order bug found in the Node draft of this feature).
- Requests time out after 5 s. Stopping a live stream needs a second tap
  within 4 s.
- Ported from the project doc written for the retired Node/Docker board;
  there is no admin token here because authentication was removed in 0.2.4.

## 0.2.8
- Settings: debug simulator to drive the boards and overlay without a console.

## 0.2.7
- OBS race overlay at `/overlay/race`; `.bin` replay speed fix.

## 0.2.6
- Launcher reads the installed version reliably; meet-board icon.

## 0.2.5
- Meet board 75/25 split and four styles.

## 0.2.4
- Authentication removed from admin pages.

## 0.2.3
- 8 lanes by default.

## 0.2.2
- Per-install session secret; repo made public.

## 0.2.1
- First Windows exe release.
