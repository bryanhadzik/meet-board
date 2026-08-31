# Meet Board

Spectator meet board for swim meets and water polo. Shows the heat **in the water**
on the left and the **next heat's full lane assignments** on the right, colour-coded
by team, so a parent can tell at a glance whether their kid is up.

Runs in Docker. Built for a 25-yard high-school pool with a Colorado Time Systems
console and Hy-Tek Meet Manager.

---

## How it works

Two data sources, joined on `event + heat`:

| Source | Carries | Changes |
|---|---|---|
| `data/meet.json` | every event, heat, lane, swimmer, team, seed time | written once at session start |
| CTS console | current event, heat, running clock, lane times, places | continuously |

The **UP NEXT panel comes from `meet.json` alone**, so it keeps working when the
console link is down. If the link drops, the board freezes on the last real state
and shows `NO TIMING LINK` — it never invents times.

---

## The serial problem, and why this reads TCP

**Docker Desktop on Windows cannot pass a COM port into a container.** Containers
run inside a Linux VM; there is no `--device=COM3`. Rather than fight that, put a
**serial-to-Ethernet device server** on the console's scoreboard output. The
console's data becomes a network resource and the container just opens a socket.

This solves a second problem for free: Windows lets only one process open a COM
port, and AquaSync wants that same feed. Most device servers ship a Windows
virtual-COM driver, so AquaSync sees `COM3` while this container reads raw TCP —
one physical connection, two consumers, no Y-cable.

Set `BOARD_TCP` to the device server's address in `docker-compose.yml`.

Running on bare-metal Linux instead? `BOARD_SERIAL=/dev/ttyUSB0` works, and you'll
need `npm install serialport` (an optional dependency, omitted from the image).

---

## Quick start

    git clone https://github.com/OWNER/meet-board.git
    cd meet-board

Bench test, no hardware:

    docker compose -f docker-compose.yml run --rm -p 8080:8080 \
      -e BOARD_SOURCE=mock -e BOARD_MEET=/app/public/meet.json board

Live:

    # edit docker-compose.yml: set image OWNER and BOARD_TCP
    cp your-meet.json data/meet.json
    docker compose up -d

Open `http://<host>:8080/` — that is the URL the TVs point at.

---

## Before each session

1. **Meet Manager → seed the session first**, then
   `File > Export > Start List for Scoreboard > Start Lists for CTS`
2. Convert the `.scb` files to `meet.json`
3. Drop it at `data/meet.json`

The server watches the file and reloads within a second. **No restart needed**, so
a re-export after deck changes is a file copy.

---

## Before going live: verify the parser

The CTS channel map is well documented — `0x0C` event/heat, `0x01–0x0A` lanes,
`0x00` running clock — but record framing differs between console generations.

    docker compose run --rm board node src/board-server.js --tcp HOST:4001 --sniff

Watch the hex while an operator changes the heat and runs a race. Confirm the
delimiter and field offsets in the `CTS` object match. **Only then run live.**
Until that's done, treat `--mock` as the source of truth for the UI.

---

## Updating from home

Push to `main` → GitHub Actions builds and publishes to `ghcr.io` (amd64 + arm64).
Then on the pool device:

    ./scripts/update.sh          # or scripts\update.ps1 on Windows

**Deliberately manual.** Do not put Watchtower on this. An image that
auto-updates itself mid-season will eventually do it twenty minutes before a meet.

To reach the pool device from home, use the Site Magic tunnel — no ports exposed
to the internet.

---

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `BOARD_PORT` | `8080` | HTTP/WS listen port |
| `BOARD_TCP` | — | `host:port` of the serial-to-ethernet device server |
| `BOARD_SERIAL` | — | local serial device (Linux only) |
| `BOARD_SOURCE` | — | `mock` to run a simulated meet |
| `BOARD_MEET` | `/data/meet.json` | path to the flat file |
| `BOARD_BAUD` | `9600` | console baud (8-E-1 is assumed) |

## Endpoints

    GET  /              the board
    GET  /meet.json     the flat file
    GET  /api/live      current state (polling fallback)
    GET  /api/health    { ok, link, meet, clients }
    WS   /live          state pushed on change, plus a 5s heartbeat

---

## Team colours

Defined per team in `meet.json` as `color` and `alt`. School colours are lifted
until they clear 3:1 contrast on the dark board — half of Region 11 wears black or
navy, which would otherwise disappear.

Where two teams in one meet are too close, the page resolves it at load time by
computing OKLab distance: the home team keeps its primary, anyone within ΔE 15
falls back to its `alt`. Colour is never the sole identifier — the team
abbreviation sits beside every stripe, because purple against royal blue is only
ΔE 6.6 under protanopia and no palette fixes that.

## Licence

MIT.
