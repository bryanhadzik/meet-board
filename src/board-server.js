#!/usr/bin/env node
/**
 * Natatorium meet board — server
 * -----------------------------------------------------------------------------
 * Serves the spectator board page and merges two data sources:
 *
 *   meet.json          the flat file exported from Meet Manager at session start.
 *                      Every event, heat, lane, swimmer, team, seed. Never changes
 *                      mid-session unless you re-export after deck changes.
 *
 *   CTS console        RS-232 scoreboard output: current event, heat, running
 *                      time, lane times and places. Changes constantly.
 *
 * The board joins them on event + heat. Everything in the UP NEXT panel comes
 * from meet.json alone, so it keeps working when the console link is down.
 *
 * Run:
 *   node src/board-server.js --mock                    test with no hardware
 *   node src/board-server.js --tcp 192.168.1.50:4001   live, serial-over-ethernet
 *   node src/board-server.js --serial /dev/ttyUSB0     live, local serial (Linux)
 *   node src/board-server.js --tcp ... --sniff         dump raw hex, parse nothing
 *
 * Env (for Docker): BOARD_PORT BOARD_TCP BOARD_SERIAL BOARD_MEET BOARD_SOURCE BOARD_BAUD
 */
'use strict';

const http = require('http');
const fs   = require('fs');
const path = require('path');

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const arg  = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? (argv[i + 1] || true) : d; };
const has  = (n) => argv.includes('--' + n);

const env = process.env;
const PORT      = parseInt(arg('port', env.BOARD_PORT || 8080), 10);
const PUBLIC    = path.resolve(arg('public', env.BOARD_PUBLIC || path.join(__dirname, '..', 'public')));
const MEET_FILE = path.resolve(arg('meet', env.BOARD_MEET || path.join(PUBLIC, 'meet.json')));
const SERIAL    = arg('serial', env.BOARD_SERIAL || null);
const TCP       = arg('tcp',    env.BOARD_TCP || null);
const SNIFF     = has('sniff');
const MOCK      = has('mock') || env.BOARD_SOURCE === 'mock';
const BAUD      = parseInt(arg('baud', env.BOARD_BAUD || 9600), 10);

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ---------------------------------------------------------------- meet.json
let MEET = null;
function loadMeet() {
  try {
    MEET = JSON.parse(fs.readFileSync(MEET_FILE, 'utf8'));
    const heats = (MEET.events || []).reduce((n, e) => n + (e.heats || []).length, 0);
    log(`meet.json loaded — ${(MEET.events || []).length} events, ${heats} heats`);
  } catch (e) {
    MEET = null;
    log(`meet.json NOT loaded (${e.code || e.message}) — board will run in idle mode`);
  }
}
loadMeet();
// Re-read on change so a mid-session re-export is picked up without a restart.
try {
  fs.watch(path.dirname(MEET_FILE), (_, f) => {
    if (f === path.basename(MEET_FILE)) { clearTimeout(loadMeet._t); loadMeet._t = setTimeout(loadMeet, 400); }
  });
} catch (e) { /* directory may not exist yet */ }

// ---------------------------------------------------------------- live state
const LIVE = {
  mode: 'idle',       // 'idle' | 'meet'
  event: null,        // event NUMBER as the console reports it
  heat: null,
  running: 0,         // seconds on the race clock
  lanes: {},          // { "1": { time, place, final } }
  link: 'down',       // 'down' | 'mock' | 'up'
  updated: 0
};

function publish() {
  LIVE.updated = Date.now();
  const msg = JSON.stringify(LIVE);
  for (const c of clients) { try { c.send(msg); } catch (e) {} }
}

// ---------------------------------------------------------------- CTS parser
/**
 * IMPORTANT — read before trusting this in production.
 *
 * The Colorado legacy scoreboard stream is RS-232, 9600 baud, 8 data bits,
 * EVEN parity, 1 stop bit, and is organised as channel-addressed records:
 *
 *     channel 0x00        running time
 *     channel 0x01–0x0A   lanes 1–10        (0x17–0x18 for lanes 11–12)
 *     channel 0x0C        event and heat number
 *     channel 0x19–0x1E   splits
 *     channel 0x0D/0x13/0x14/0x15  team scores
 *
 * It carries NO swimmer names — those come from meet.json.
 *
 * The channel map above is well documented. The exact record framing differs
 * between console generations, so this parser is deliberately written to be
 * verified against YOUR console rather than trusted blind:
 *
 *   1. Run `node board-server.js --sniff COM3` and watch the hex while an
 *      operator changes the event/heat and runs a race.
 *   2. Confirm the record delimiter and field offsets below match.
 *   3. Only then run in --serial mode.
 *
 * Until step 2 is done, treat --mock as the source of truth for the UI and
 * this as a starting point for the wire format.
 */
const CTS = {
  buf: Buffer.alloc(0),

  feed(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    // Records are delimited by CR on every CTS generation I have seen.
    let i;
    while ((i = this.buf.indexOf(0x0d)) !== -1) {
      const rec = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 1);
      if (rec.length) this.record(rec);
    }
    if (this.buf.length > 4096) this.buf = this.buf.slice(-1024); // never grow unbounded
  },

  record(rec) {
    // Strip parity bit; CTS sets it and the byte values are 7-bit ASCII underneath.
    const b = Buffer.from(rec.map(v => v & 0x7f));
    const channel = b[0];
    const text = b.slice(1).toString('ascii').trim();

    if (channel === 0x0c) {                       // event / heat
      const m = text.match(/(\d+)\D+(\d+)/);
      if (m) {
        const ev = parseInt(m[1], 10), ht = parseInt(m[2], 10);
        if (ev !== LIVE.event || ht !== LIVE.heat) {
          LIVE.event = ev; LIVE.heat = ht;
          LIVE.lanes = {};                        // new heat, clear the board
          LIVE.running = 0;
          log(`event ${ev} heat ${ht}`);
        }
        LIVE.mode = 'meet';
        publish();
      }
    } else if (channel === 0x00) {                // running clock
      const t = parseClock(text);
      if (t !== null) { LIVE.running = t; publish(); }
    } else if ((channel >= 0x01 && channel <= 0x0a) || channel === 0x17 || channel === 0x18) {
      const lane = channel === 0x17 ? 11 : channel === 0x18 ? 12 : channel;
      const t = parseClock(text);
      if (t !== null) {
        LIVE.lanes[lane] = Object.assign(LIVE.lanes[lane] || {}, { time: t, final: true });
        rankLanes();
        publish();
      }
    }
    // splits and team scores are parsed the same way; add channels as needed.
  }
};

function parseClock(s) {
  const m = String(s).match(/(?:(\d+):)?(\d{1,2})\.(\d{1,2})/);
  if (!m) return null;
  return (parseInt(m[1] || 0, 10) * 60) + parseInt(m[2], 10) + parseInt(m[3].padEnd(2, '0'), 10) / 100;
}

function rankLanes() {
  const done = Object.entries(LIVE.lanes)
    .filter(([, v]) => typeof v.time === 'number')
    .sort((a, b) => a[1].time - b[1].time);
  done.forEach(([lane], i) => { LIVE.lanes[lane].place = i + 1; });
}

// ---------------------------------------------------------------- serial
function openSerial(portName) {
  let SerialPort;
  try { ({ SerialPort } = require('serialport')); }
  catch (e) { log('serialport module not installed — run: npm install serialport'); process.exit(1); }

  const sp = new SerialPort({ path: portName, baudRate: BAUD, dataBits: 8, parity: 'even', stopBits: 1 });

  sp.on('open',  () => { LIVE.link = 'up'; log(`serial open ${portName} @ ${BAUD} 8-E-1`); publish(); });
  sp.on('error', (e) => { LIVE.link = 'down'; log('serial error:', e.message); publish(); });
  sp.on('close', () => {
    LIVE.link = 'down'; log('serial closed — retrying in 5s'); publish();
    setTimeout(() => openSerial(portName), 5000);
  });

  sp.on('data', onBytes);
}

/**
 * Serial over TCP — the primary path when this runs in Docker.
 *
 * Docker Desktop on Windows runs containers inside a Linux VM and has no way to
 * pass a Windows COM port through. So rather than fight that, the console's
 * serial output goes to a serial-to-Ethernet device server and becomes a network
 * resource. The container just opens a socket. See README.
 */
function openTcp(hostport) {
  const net = require('net');
  const [host, port] = String(hostport).split(':');
  const sock = new net.Socket();
  let retry = null;

  const again = (why) => {
    if (retry) return;
    LIVE.link = 'down'; publish();
    log(`tcp ${hostport} ${why} — retrying in 5s`);
    retry = setTimeout(() => { retry = null; openTcp(hostport); }, 5000);
  };

  sock.setKeepAlive(true, 10000);
  sock.on('connect', () => { LIVE.link = 'up'; log(`tcp connected ${hostport}`); publish(); });
  sock.on('data', onBytes);
  sock.on('error', (e) => { sock.destroy(); again(e.code || e.message); });
  sock.on('close', () => again('closed'));
  sock.connect(parseInt(port || 4001, 10), host);
}

function onBytes(d) {
  if (SNIFF) {
    log('RX', d.toString('hex').replace(/(..)/g, '$1 ').trim(),
        '|', d.toString('ascii').replace(/[^\x20-\x7e]/g, '.'));
  } else {
    CTS.feed(d);
  }
}

// ---------------------------------------------------------------- mock
function startMock() {
  LIVE.link = 'mock';
  let ei = 0, hi = 0, t = 0, resting = 0;
  const evs = () => (MEET && MEET.events) || [];
  if (!evs().length) { log('mock needs meet.json — none loaded'); return; }

  const paceOf = (l) => { let k = 0; for (const c of l.name) k = (k * 31 + c.charCodeAt(0)) % 997; return 0.94 + (k % 120) / 1000; };

  setInterval(() => {
    const ev = evs()[ei]; if (!ev) return;
    const heat = ev.heats[hi]; if (!heat) return;
    const total = Math.max(28, (ev.distance || 100) * 0.62);

    LIVE.mode = 'meet'; LIVE.event = ev.no; LIVE.heat = heat.no;

    if (resting > 0) {
      resting -= 0.25;
      if (resting <= 0) {                                   // advance
        if (hi + 1 < ev.heats.length) hi++;
        else { hi = 0; ei = (ei + 1) % evs().length; }
        t = 0; LIVE.lanes = {}; LIVE.running = 0;
      }
    } else {
      t += 0.25 * 2.6;                                       // demo runs faster than real time
      LIVE.running = t;
      for (const l of heat.lanes) {
        const done = total * paceOf(l);
        if (t >= done && !(LIVE.lanes[l.lane] || {}).final) {
          LIVE.lanes[l.lane] = { time: done, final: true };
          rankLanes();
        }
      }
      if (t > total * 1.25) resting = 5;
    }
    publish();
  }, 250);
  log('mock timing source running');
}

// ---------------------------------------------------------------- http + ws
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
               '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
               '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };

const server = http.createServer((req, res) => {
  const url = req.url.split('?')[0];

  if (url === '/api/live') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    return res.end(JSON.stringify(LIVE));
  }
  if (url === '/api/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ ok: true, link: LIVE.link, meet: !!MEET, clients: clients.size }));
  }

  let file = path.join(PUBLIC, url === '/' ? 'index.html' : decodeURIComponent(url));
  if (!file.startsWith(PUBLIC)) { res.writeHead(403); return res.end('forbidden'); }

  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end('not found'); }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': path.extname(file) === '.json' ? 'no-store' : 'public, max-age=60'
    });
    res.end(data);
  });
});

const clients = new Set();
let WebSocketServer;
try { ({ WebSocketServer } = require('ws')); }
catch (e) { log('ws module not installed — run: npm install ws'); process.exit(1); }

const wss = new WebSocketServer({ server, path: '/live' });
wss.on('connection', (ws) => {
  clients.add(ws);
  log(`client connected (${clients.size})`);
  try { ws.send(JSON.stringify(LIVE)); } catch (e) {}
  ws.on('close', () => { clients.delete(ws); log(`client gone (${clients.size})`); });
  ws.on('error', () => clients.delete(ws));
});

// Heartbeat so a display that loses the link notices quickly rather than
// sitting on a stale board for minutes.
setInterval(() => { publish(); }, 5000);

server.listen(PORT, () => {
  log(`board server on http://0.0.0.0:${PORT}  (serving ${PUBLIC})`);
  if (SNIFF) log('SNIFF MODE — dumping raw bytes, parsing nothing');
  if (TCP)         openTcp(TCP);
  else if (SERIAL) openSerial(SERIAL);
  else if (MOCK)   startMock();
  else log('no timing source — set BOARD_TCP, BOARD_SERIAL, or --mock');
});

// Docker sends SIGTERM on `docker stop`; exit promptly so restarts are quick.
for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    log(`${sig} — shutting down`);
    for (const c of clients) { try { c.close(); } catch (e) {} }
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000).unref();
  });
}
