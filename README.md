# meet-board

Pool swim-meet display software for a Colorado Time Systems (CTS) console:

- **Stream overlay** for OBS (`/overlay/1080p`) — names, lane times, places, records and time standards.
- **Spectator meet board** for lobby TVs (`/web/meetboard`) — the heat in the water on the left, the full
  lane assignments for the **next heat** on the right, color-coded by school.
- **Web scoreboard** for phones (`/web/home`).

It snoops the serial line between the CTS console and the scoreboard and joins that live data with the
start lists exported from Hy-Tek Meet Manager.

Built on [CTS_Scoreboard](https://github.com/STU940652/CTS_Scoreboard) by STU940652, from Ben Betz's
[h2orules fork](https://github.com/h2orules/CTS_Scoreboard) (upstream PR #4: Meet Manager 8 `.hy3`
loader, records and time standards, race state machine, web scoreboard). This project is in no way
associated with CTS or Hy-Tek.

## What meet-board adds

| | |
|---|---|
| **CTS start lists (`.scb`)** | Upload the `.scb` files Meet Manager writes (several at once, or a `.zip`), or point the app at a **watch folder** and re-export after deck changes — the boards update within a few seconds. |
| **Meet board** | `/web/meetboard`: in-the-water + up-next panels, team color stripes with the team code beside every stripe, clash resolution between similar colors. The up-next panel comes from the start lists alone, so it keeps working if the console link drops. |
| **Team colors** | `/team_colors`: built-in Region 11 defaults, auto colors for teams nobody has set, editable per meet. |
| **Windows exe + Update button** | Every push to `main` builds `meet-board.exe` on GitHub Actions and publishes a release. On the timing PC, **Settings → Software Update → Update now** downloads it, swaps the exe and restarts. |

## Install on the timing PC (Windows)

1. Download `meet-board-launcher.cmd` and `meet-board-launcher.ps1` from the
   [latest release](https://github.com/bryanhadzik/meet-board/releases/latest) into their own folder,
   e.g. `C:\MeetBoard\`, and double-click the `.cmd`. The launcher window has **Start**, **Stop**,
   **Install/Update** (downloads the newest `meet-board.exe` from GitHub Releases into the same folder)
   and links to Settings and the board. Closing the launcher leaves the server running.
   Or skip the launcher: download `meet-board.exe` into the folder and run it directly.
   `settings.json` is created beside the exe.
2. Start it. The console window prints the URLs. Allow it through Windows Firewall (private networks)
   so the TVs can reach it.
3. Open `http://localhost:5000/settings` (no login: every page, including Settings and Software
   Update, is open to anyone who can reach the PC on the network), then:
   - pick the serial port for the CTS Y-cable,
   - set **Number of Lanes**,
   - load start lists (below),
   - check team colors on `/team_colors`.
4. Point each TV's browser/signage player at `http://<timing-pc>:5000/web/meetboard` in kiosk mode.
   OBS browser source: `http://localhost:5000/overlay/1080p`.

### Loading start lists

**CTS start lists (.scb)** — in Meet Manager: *File → Export → Start Lists for Scoreboard → Start Lists
for CTS*. That writes one `E###.scb` per event. Either upload them on the Settings page, or set the
**CTS start list watch folder** to the export folder and just re-export whenever heats change.

`.scb` files carry names (20 characters, truncated by Meet Manager), team codes, and heat/lane — no
seed times or ages. For seed times, age codes, records and standards matching by age, use the
**.hy3** Meet Entries export instead (upload it in the same box).

### Updating

Settings shows **Software Update**; the button turns green when a newer release exists. Updating
restarts the app (boards blank for ~15 s and reconnect on their own), so do it between sessions.
The repo is private, so update checks need a GitHub token. Both the launcher and the in-app
updater find one automatically if the GitHub CLI is signed in on that PC (`gh auth login`);
otherwise set `GITHUB_TOKEN` or add `"github_token"` (read-only, fine-grained) to `settings.json`.

## History

Version 1 was a Node.js/Docker board (Aug 2026). It is preserved on the
[`docker`](https://github.com/bryanhadzik/meet-board/tree/docker) branch and tag `docker-v1`.
It was replaced because Docker on Windows can't open a COM port (it needed a serial-to-Ethernet
box) and its CTS reader assumed a text protocol: on a recorded console stream it decoded 0 of 522
records, where the CTS_Scoreboard parser decodes every event/heat. Its best ideas are carried over
here: preview-safe loading with **Undo last load**, the **6-hour stale-export guard**, lanes-per-heat
auto-detection, duplicate-event warnings, and `/api/health`.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
uv sync --dev
uv run meet-board                         # serial port from settings
uv run meet-board --in samples/meet.bin   # replay a raw serial capture (loops)
uv run meet-board --in capture.txt        # replay a --out text capture
uv run pytest
uv run pyinstaller meet-board.spec        # local exe build
```

```
usage: meet-board [-h] [--port PORT] [--in IN_FILE] [--out OUT] [--portlist]
                  [--speed IN_SPEED] [--debug] [--version]
```

Sample start lists are in `samples/scb/`. Templates are Jinja2 under `templates/`; anything in
`templates/web/` is served at `/web/<name>`.

### Layout

| File | |
|---|---|
| `CTS_Scoreboard.py` | Flask/Socket.IO app, CTS serial parser, settings and admin routes |
| `scb_loader.py` | `.scb` parser, zip/folder loading, watch-folder change detection |
| `meet_board.py` | next-heat logic, team color table for the meet board |
| `updater.py` | GitHub Releases check and self-update (exe only) |
| `app_paths.py` | bundle vs. data paths when frozen by PyInstaller |
| `hytek_*.py` | `.hy3`, `.st2` (time standards), `.rec` (records) loaders |
| `race_state_machine.py` | PreRace / Running / Finished / Clear / Blank tracking |
| `meet-board.spec`, `.github/workflows/build-windows.yml` | Windows build and release |

## Hardware

To get the CTS serial stream you need an RS-232 port (e.g. a USB to RS-232 adapter) and a tap on the
scoreboard line:

1. A mono 1/4" male to two female Y cable, e.g. Hosa YPP-111.
2. A female DB-9 connector, e.g. StarTech C9PSF.

Cut off one of the 1/4" female connectors. Solder the center conductor to pin 2 of the DB-9 and the
shield to pin 5. Put it inline with the CTS scoreboard cable and connect the DB-9 to the serial port.

## Protocol

Interpretation of the CTS protocol is based on the work of
[hwbrill](https://github.com/hwbrill/vsCTS/blob/master/README.md) and
[Marco](https://marcoscorner.walther-family.org/2015/07/colorado-timing-console-scoreboard-protocol/).
