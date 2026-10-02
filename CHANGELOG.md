# Changelog

## 0.2.19

### Changed
- Serial input card moved to the top of Settings, led by a big **race time**
  readout straight from the console clock, with the event, heat and race state
  and how fresh the clock is ("live from the console" / "last race-time record
  12 s ago").

## 0.2.18

### Added
- **Settings -> Serial input**: live check of the CTS console feed. Shows
  whether the port opened, bytes/sec, time since the last byte, totals,
  decoded CTS records by channel (clock, lanes, event/heat, scores), the last
  96 raw bytes (record-start bytes highlighted) and the latest records, plus
  the serial ports Windows can see. A plain-English verdict says what's wrong:
  port won't open, open but silent (console off / cable / pin 2-5 wiring),
  bytes but not CTS data (wrong device or baud), or data stopped.
  Also at `/api/serial`.

## 0.2.17

### Fixed
- School names (SOUTH SUMMIT, CEDAR VALLEY) no longer cut off: wider team
  column, and it shrinks to fit like swimmer names. Fixed an off-by-one that
  left an ellipsis on names that were only a pixel too long.

## 0.2.16

### Added
- **One school, several team codes.** Meet Manager codes change from meet to
  meet (Stansbury is `SHS` in one, `STAN` in another). Each code is linked to
  its school three ways: built-in aliases (STAN = SHS, DPEAK = DPHS), an
  *Also known as* field per school on Team Colors, and the school name in the
  `.hy3` (a new code whose name matches a known school links by itself).
  Linked codes share the school's colors and logo - one `SHS.png` covers STAN.

## 0.2.15

### Added
- **School logos** on the meet board (white tile + school name beside every
  swimmer, and in Up Next) and on the race overlay. Upload on Team Colors
  (`CODE.png` files or a .zip); stored in a `logos` folder next to the exe.
- **Refresh all screens** button in the admin top bar: every meet board,
  scoreboard and overlay page reloads. Pages also reload by themselves after
  meet-board restarts or updates. TVs never need a keyboard.
- Column headings over the lanes: TEAM, **DROP** (seconds under the seed),
  TIME / SEED, PL.

### Changed
- Meet board footer removed; the lanes use the space (bigger names and
  times). Team scores, when the console sends them, sit in the header; the
  meet name shows there between events.
- Up Next drops seed times so swimmer names fit; long names shrink to fit
  instead of being cut off.

## 0.2.14

### Fixed
- Times are zero-filled: `1:05.23`, not `1: 5.23` (CTS blanks that digit).
- A `.scb` reload (watch folder, including at every app start) replaced the
  `.hy3` and dropped seed times, ages and relay swimmers. They're now kept:
  each `.scb` lane is matched to the last `.hy3` upload by event, team and
  swimmer name (or relay letter), so re-seeded heats still match.

### Added
- Seed times in the in-water lanes while a heat is on the blocks.
- Team colors for all 12 Mel Roberts Invitational schools, from the UHSAA
  school directory and the Tooele County SD school sheet.

## 0.2.13

### Added
- **Relay swimmers' last names** under each relay on the meet board (in the
  water and up next), from `.hy3` start lists. `.scb` files don't carry them.
- **Beat-the-seed marker**: when a swimmer touches under their seed time the
  meet board and the stream overlay show the drop (e.g. `-1.29`) beside the
  time. Follows the existing *Show PR tags* setting.
- **Debug -> Seed times**: Mixed / Everyone beats their seed / Nobody beats
  their seed, to test the marker.
- Built-in colors for meet codes `SHS` (Stansbury, blue), `CARB` (Carbon, blue),
  `CDRV` (Cedar Valley, red), `MOR` (Morgan, maroon), `DPHS` (Deseret Peak).

### Changed
- Bigger **UP NEXT** heading and heat line, and a bigger **HEAT x OF y** in
  the board header.

### Fixed
- A meet board or overlay that connects mid-race or during results (TV
  reboot, OBS reloading the source) now shows the times right away.

## 0.2.12

### Fixed
- Seeded **Meet Entries `.hy3`** exports from Meet Manager failed to load: relay
  heat/lane lines have a blank date, which crashed the parser. Fixed for both
  individual and relay entries.
- Relays showed a blank name. They now show school + relay letter
  ("Stansbury A"), with the relay seed time.
- Open events (Meet Manager ages 0-109) no longer read "109 & Under":
  "Women 50 Yard Freestyle".
- Swimmers with no birth date on file no longer show an age code of "0".

## 0.2.11

### Added
- **Music** tab (`/music`): a button per song (anthem pinned first and
  highlighted), pause/resume, stop, volume, progress. Songs are your own audio
  files in the `music` folder next to meet-board.exe - upload them on the tab.
- Sound comes from the **speaker tab** (`/music?speaker=1`) on the streaming PC,
  so it plays through that PC's speakers/PA. Any phone, tablet or PC on the pool
  network can use `/music` as the remote. Click *Enable sound* once on the
  speaker tab (browser rule). The launcher has a *Music Speaker* link.
- Tab bar across the admin pages: Settings | Music | Team Colors | Software Update.

## 0.2.10

### Fixed
- OBS never connected with the default URL `ws://127.0.0.1:4455`: with no path
  the WebSocket request line was malformed ("Illegal target characters"). URLs
  are now normalized (`ws://host:port/`; bare `localhost` or `ip:port` work too).

### Added
- OBS troubleshooting on the Stream card: plain-language error with a hint,
  **Test connection** (URL, resolve, TCP port, WebSocket handshake, obs-websocket
  Hello and version, password, OBS version, stream destination/key, stream
  status - stops at the first failure and says what to fix), and a
  **Connection log** of recent connect/disconnect events.
- `GET /api/obs/diagnostics`, `POST /api/obs/test`.

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
