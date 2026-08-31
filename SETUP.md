# Setup

Full illustrated runbook (with "how you know it worked" for every step):
https://claude.ai/code/artifact/PLACEHOLDER

Quick version.

## Once, at home — get it on GitHub

    winget install --id Git.Git -e
    winget install --id GitHub.cli -e
    gh auth login

    cd meet-board
    gh repo create meet-board --private --source=. --push

## On the pool PC

Install Docker Desktop (WSL 2 backend). Verify: `docker run --rm hello-world`

    winget install --id Git.Git -e
    winget install --id GitHub.cli -e
    gh auth login
    cd C:\
    gh repo clone YOURNAME/meet-board
    cd meet-board

Start in mock mode first. In docker-compose.yml comment out BOARD_TCP and
uncomment BOARD_SOURCE/BOARD_MEET, then:

    docker compose up -d --build
    docker compose logs -f

Expect `board server on http://0.0.0.0:8080` and `mock timing source running`.
Open http://localhost:8080/ — a board with a ticking clock.

## Before every meet

1. Meet Manager: **seed the session**, then
   `File > Export > Start List for Scoreboard > Start Lists for CTS`
   into a fresh dated folder.
2. Open http://<pool-pc-ip>:8080/admin.html from any machine.
   Fill in the session details, drop in every .scb at once,
   press **Read files**, check the counts, press **Apply to the board**.
   Wrong session? **Undo last upload**.

## The TVs

Give the pool PC a DHCP reservation. Point each display at
http://<pool-pc-ip>:8080/ — via the panel's built-in URL launcher if it has
one, else a Raspberry Pi in Chromium kiosk mode. Disable screen blanking.

## Connecting the console

Docker on Windows cannot pass a COM port into a container. Put the console's
scoreboard output on a serial-to-Ethernet device server (9600 8-E-1, raw TCP,
fixed IP). Sniff before trusting the parser:

    docker compose run --rm board node src/board-server.js --tcp HOST:4001 --sniff

Then set BOARD_TCP in docker-compose.yml, remove the mock lines, and
`docker compose up -d`. Expect `tcp connected` then `event N heat M`.

## Updating

At home: `git add -A && git commit -m "..." && git push`
At the pool: `git pull && docker compose up -d --build`

Never during a meet. There is no auto-updater on purpose.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sample swimmers, not yours | Still in mock mode; set BOARD_MEET back to /data/meet.json |
| Upload permission error (Linux) | `sudo chown -R 1000:1000 data` |
| "timestamps span N hours" | Mixed exports; re-export to a clean dated folder |
| NO TIMING LINK | Console feed down. Up-next still works. Container retries every 5s |
| Empty board after upload | Session wasn't seeded before export |
| Wrong lane count drawn | Set "Pool lanes" on the upload page |
| Can't reach from timing laptop | Windows Firewall: allow inbound TCP 8080 on the private profile |
