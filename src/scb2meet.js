#!/usr/bin/env node
/**
 * scb2meet — convert a folder of Hy-Tek Meet Manager CTS start-list exports
 *            (.scb, one file per event) into a single meet.json for the board.
 *
 *   node src/scb2meet.js ./export -o data/meet.json --home TOOEL
 *
 * FILE FORMAT (verified against real Meet Manager output, not guessed):
 *
 *   line 1      "#<eventNo><suffix?> <EVENT NAME>"        e.g. "#2 MEN 200 MEDLEY RELAY"
 *   line 2..n   fixed 38 chars, CRLF terminated:
 *                 [0:20]  name   (swimmer, or relay designation e.g. "UHS   C")
 *                 [20:22] "--"   literal separator
 *                 [22:38] team abbreviation
 *
 *   Entry lines are written in blocks of LANES per heat, in lane order.
 *   A blank name means that lane is empty in that heat. Seeding is
 *   slowest-heat-first with center-out lane assignment, so partially filled
 *   heats legitimately have gaps at both ends.
 *
 *   There is NO seed time and NO swimmer age/ID in this format. Two fields only.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const NAME_W = 20, SEP_AT = 20, TEAM_AT = 22, LINE_W = 38;

// ---------------------------------------------------------------- args
const OPTS = { o:1, out:1, lanes:1, 'pool-lanes':1, home:1, name:1, venue:1 };  // take a value
const FLAGS = { force:1, quiet:1 };                              // boolean

const opt = {}; const pos = [];
{
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const t = a[i];
    const key = t.startsWith('--') ? t.slice(2) : t.startsWith('-') ? t.slice(1) : null;
    if (key && OPTS[key]) { opt[key] = a[++i]; }
    else if (key && FLAGS[key]) { opt[key] = true; }
    else if (key) { console.error(`unknown option: ${t}`); process.exit(1); }
    else pos.push(t);
  }
}

const IN_DIR  = pos[0] || './export';
const OUT     = opt.o || opt.out || 'data/meet.json';
const LANES_A = opt.lanes || null;
const POOL_L  = opt['pool-lanes'] ? parseInt(opt['pool-lanes'], 10) : null;
const HOME    = (opt.home || '').toUpperCase();
const MEET_NM = opt.name || null;
const VENUE   = opt.venue || null;
const FORCE   = !!opt.force;
const QUIET   = !!opt.quiet;

const warn = [];
const say  = (...a) => { if (!QUIET) console.log(...a); };

// ---------------------------------------------------------------- team colours
// Region 11 / Tooele County, tuned for 3:1 contrast on the dark board.
// Every hex is an approximation until the district style guide is checked.
const KNOWN = {
  TOOEL: { name: 'Tooele',         color: '#D18AE0', alt: '#E8EEEE' },
  STAN:  { name: 'Stansbury',      color: '#3D8BF5', alt: '#9AA6AB' },
  GHS:   { name: 'Grantsville',    color: '#F0546A', alt: '#E8EEEE' },
  DPEAK: { name: 'Deseret Peak',   color: '#D9C46E', alt: '#BFC7CA' },
  BRHS:  { name: 'Bear River',     color: '#E8EEEE', alt: '#9AA6AB' },
  SVHS:  { name: 'Sky View',       color: '#3E9BD6', alt: '#FFD24D' },
  MCHS:  { name: 'Mountain Crest', color: '#FF8C42', alt: '#5B8FE8' },
  RIDGE: { name: 'Ridgeline',      color: '#7ACC3E', alt: '#A5ACAF' },
  GCHS:  { name: 'Green Canyon',   color: '#3FB58A', alt: '#BFC7CA' }
};

// Fallback ramp for teams not in the table. All validated >= 3:1 on #0b1618.
// Assigned in order of first appearance, so it is deterministic per meet and
// spreads hues rather than colliding the way a hash would.
const RAMP = ['#3D8BF5','#F0546A','#D9C46E','#7ACC3E','#D18AE0','#FF8C42',
              '#3FB58A','#3E9BD6','#FFD24D','#E8EEEE','#A5ACAF','#5B8FE8'];

// ---------------------------------------------------------------- parse one file
function parseScb(file) {
  const raw = fs.readFileSync(file, 'latin1');          // never throws on stray bytes
  const lines = raw.split(/\r\n|\n|\r/);
  const header = (lines[0] || '').trim();

  const m = header.match(/^#\s*(\d+)([A-Za-z]?)\s+(.*)$/);
  if (!m) { warn.push(`${path.basename(file)}: header not recognised: ${header.slice(0,40)!==''?JSON.stringify(header.slice(0,40)):'(empty)'} — skipped`); return null; }

  const no     = parseInt(m[1], 10);
  const suffix = m[2] || '';
  const rawName = m[3].replace(/\s+/g, ' ').trim();

  const entries = [];
  for (const l of lines.slice(1)) {
    if (l.trim() === '' && l.length < LINE_W) continue;   // trailing blank from final CRLF
    entries.push(l.padEnd(LINE_W, ' '));
  }

  return { file: path.basename(file), no, suffix, rawName, entries };
}

// ---------------------------------------------------------------- lanes per heat
function detectLanes(files) {
  if (LANES_A) return parseInt(LANES_A, 10);
  const counts = files.map(f => f.entries.length).filter(n => n > 0);
  for (const n of [10, 8, 6, 5, 4, 3]) {
    if (counts.every(c => c % n === 0)) return n;
  }
  warn.push(`could not detect lanes per heat from entry counts (${counts.join(', ')}) — assuming 10; pass --lanes N to override`);
  return 10;
}

// ---------------------------------------------------------------- event metadata
function eventMeta(rawName) {
  const up = rawName.toUpperCase();
  const dm = up.match(/(\d{2,4})\s*(?:Y|M|YARD|METER)?\s/);
  return {
    distance: dm ? parseInt(dm[1], 10) : 100,
    relay:    /RELAY/.test(up),
    gender:   /\bWOMEN|GIRLS\b/.test(up) ? 'F' : /\bMEN|BOYS\b/.test(up) ? 'M' : null
  };
}

const title = (s) => s.replace(/\s+/g, ' ').trim()
  .replace(/\b([A-Z])([A-Z']+)\b/g, (_, a, b) => a + b.toLowerCase())
  .replace(/\bIm\b/, 'IM').replace(/\bMedley\b/i, 'Medley');

// ---------------------------------------------------------------- main
if (!fs.existsSync(IN_DIR) || !fs.statSync(IN_DIR).isDirectory()) {
  console.error(`not a directory: ${IN_DIR}`); process.exit(1);
}

const scbFiles = fs.readdirSync(IN_DIR)
  .filter(f => /\.scb$/i.test(f))
  .map(f => path.join(IN_DIR, f));

if (!scbFiles.length) { console.error(`no .scb files in ${IN_DIR}`); process.exit(1); }

// Stale-file guard. Exporting into a reused folder is the real footgun here:
// last week's events silently fold into tonight's board.
const stats = scbFiles.map(f => fs.statSync(f).mtimeMs);
const spreadH = (Math.max(...stats) - Math.min(...stats)) / 3.6e6;
if (spreadH > 6) {
  const msg = `file timestamps span ${spreadH.toFixed(1)} hours — the folder may contain stale exports from a previous session`;
  if (FORCE) warn.push(msg);
  else { console.error(`REFUSING: ${msg}\n  Export into a fresh dated folder, or pass --force if this is intentional.`); process.exit(2); }
}

const parsed = scbFiles.map(parseScb).filter(Boolean).sort((a, b) =>
  a.no - b.no || a.suffix.localeCompare(b.suffix));

const dupes = parsed.map(p => p.no + p.suffix).filter((v, i, a) => a.indexOf(v) !== i);
if (dupes.length) warn.push(`duplicate event numbers: ${[...new Set(dupes)].join(', ')}`);

const LANES = detectLanes(parsed);

const teamsSeen = [];
const events = [];

for (const p of parsed) {
  const meta = eventMeta(p.rawName);
  const heats = [];
  const nHeats = Math.floor(p.entries.length / LANES);
  if (p.entries.length % LANES) warn.push(`${p.file}: ${p.entries.length} entry lines is not a multiple of ${LANES}`);

  for (let h = 0; h < nHeats; h++) {
    const lanes = [];
    for (let i = 0; i < LANES; i++) {
      const line = p.entries[h * LANES + i];
      const name = line.slice(0, NAME_W).replace(/\s+/g, ' ').trim();
      const team = line.slice(TEAM_AT).replace(/\s+/g, ' ').trim();
      if (line.slice(SEP_AT, SEP_AT + 2) !== '--')
        warn.push(`${p.file} line ${h * LANES + i + 2}: expected '--' at col 21`);
      if (!name) continue;                              // empty lane
      if (team && !teamsSeen.includes(team)) teamsSeen.push(team);
      lanes.push({ lane: i + 1, name, team, seed: null });  // .scb carries no seed time
    }
    if (lanes.length) heats.push({ no: h + 1, lanes });
  }

  if (!heats.length) { warn.push(`${p.file}: no occupied lanes — skipped`); continue; }

  events.push({
    no: p.no, name: title(p.rawName), distance: meta.distance,
    relay: meta.relay || undefined, gender: meta.gender || undefined, heats
  });
}

// ---------------------------------------------------------------- teams block
const teams = {};
const ordered = HOME && teamsSeen.includes(HOME)
  ? [HOME, ...teamsSeen.filter(t => t !== HOME)] : teamsSeen;

// Two passes. Known schools claim their real colours first, then unknown teams
// draw from the ramp skipping anything already taken — otherwise a visitor gets
// handed Tooele's purple and the board lies about who is in the lane.
const taken = new Set();
for (const code of ordered) {
  const k = KNOWN[code.toUpperCase()];
  if (k) { teams[code] = { name: k.name, color: k.color, alt: k.alt }; taken.add(k.color.toUpperCase()); }
}
const free = RAMP.filter(c => !taken.has(c.toUpperCase()));
let rampIdx = 0;
for (const code of ordered) {
  if (teams[code]) continue;
  const color = free[rampIdx % free.length] || RAMP[rampIdx % RAMP.length];
  const altPool = free.filter(c => c !== color);
  teams[code] = { name: code, color,
                  alt: altPool[(rampIdx + 3) % altPool.length] || color };
  rampIdx++;
}
const unknown = ordered.filter(c => !KNOWN[c.toUpperCase()]);
if (unknown.length > free.length)
  warn.push(`${unknown.length} unlisted teams but only ${free.length} free ramp colours — some will repeat; the team abbreviation still carries identity`);

const meet = {
  meet:  MEET_NM || 'Swim Meet',
  venue: VENUE || undefined,
  date:  new Date().toISOString().slice(0, 10),
  laneCount: POOL_L || LANES,   // lanes the BOARD draws; --pool-lanes to override
  fileLanes: LANES,             // lanes per heat block in the .scb export
  homeTeam: HOME && teams[HOME] ? HOME : (ordered[0] || null),
  generatedBy: 'scb2meet',
  teams, events, scores: []
};

fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(meet, null, 1));

const nHeats = events.reduce((n, e) => n + e.heats.length, 0);
const nSwim  = events.reduce((n, e) => n + e.heats.reduce((m, h) => m + h.lanes.length, 0), 0);
say(`${OUT}`);
say(`  ${events.length} events, ${nHeats} heats, ${nSwim} entries, ${ordered.length} teams`);
say(`  ${LANES} lanes per heat in the export${LANES_A ? ' (from --lanes)' : ' (detected)'}`);
say(`  board draws ${POOL_L || LANES} lanes${POOL_L ? ' (from --pool-lanes)' : ''}`);
if (unknown.length) say(`  no colour table entry for: ${unknown.join(', ')} — assigned from the fallback ramp`);
for (const w of warn) console.warn(`  ! ${w}`);
