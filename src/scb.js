/**
 * scb.js — Hy-Tek Meet Manager CTS start-list (.scb) → meet.json
 *
 * Pure functions, no I/O, so the CLI and the web uploader share one
 * implementation. Format verified against real Meet Manager output:
 *
 *   line 1      "#<eventNo><suffix?> <EVENT NAME>"     "#2 MEN 200 MEDLEY RELAY"
 *   line 2..n   exactly 38 chars, CRLF:
 *                 [0:20]  name  — swimmer, or relay designation ("UHS   C")
 *                 [20:22] "--"  — literal separator
 *                 [22:38] team abbreviation
 *
 * Entry lines come in blocks of N per heat, in lane order; blank name = empty
 * lane. Seeding is slowest-heat-first, centre-out, so partly filled heats have
 * gaps at both ends. There is no seed time field in this format.
 */
'use strict';

const NAME_W = 20, SEP_AT = 20, TEAM_AT = 22, LINE_W = 38;

// Region 11 / Tooele County. Tuned for >=3:1 contrast on the dark board.
// Hexes are approximations until the district style guide is checked.
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

// Fallback ramp for teams outside the table. All validated >=3:1 on #0b1618.
const RAMP = ['#3D8BF5','#F0546A','#D9C46E','#7ACC3E','#D18AE0','#FF8C42',
              '#3FB58A','#3E9BD6','#FFD24D','#E8EEEE','#A5ACAF','#5B8FE8'];

const title = (s) => s.replace(/\s+/g, ' ').trim()
  .replace(/\b([A-Z])([A-Z']+)\b/g, (_, a, b) => a + b.toLowerCase())
  .replace(/\bIm\b/g, 'IM');

function eventMeta(rawName) {
  const up = rawName.toUpperCase();
  const dm = up.match(/(\d{2,4})\s*(?:Y|M|YARD|METER)?\s/);
  return {
    distance: dm ? parseInt(dm[1], 10) : 100,
    relay:    /RELAY/.test(up),
    gender:   /\b(WOMEN|GIRLS)\b/.test(up) ? 'F' : /\b(MEN|BOYS)\b/.test(up) ? 'M' : null
  };
}

function parseOne(name, text, warn) {
  const lines = String(text).split(/\r\n|\n|\r/);
  const header = (lines[0] || '').trim();
  const m = header.match(/^#\s*(\d+)([A-Za-z]?)\s+(.*)$/);
  if (!m) {
    warn.push(`${name}: header not recognised (${JSON.stringify(header.slice(0, 40))}) — skipped`);
    return null;
  }
  const entries = [];
  for (const l of lines.slice(1)) {
    if (l.trim() === '' && l.length < LINE_W) continue;   // trailing blank from final CRLF
    entries.push(l.padEnd(LINE_W, ' '));
  }
  return { file: name, no: parseInt(m[1], 10), suffix: m[2] || '',
           rawName: m[3].replace(/\s+/g, ' ').trim(), entries };
}

function detectLanes(parsed, override, warn) {
  if (override) return parseInt(override, 10);
  const counts = parsed.map(p => p.entries.length).filter(n => n > 0);
  for (const n of [10, 8, 6, 5, 4, 3]) if (counts.every(c => c % n === 0)) return n;
  warn.push(`could not detect lanes per heat from line counts (${counts.join(', ')}) — assuming 10`);
  return 10;
}

/**
 * files: [{ name, text, lastModified? }]
 * opts:  { lanes, poolLanes, home, meetName, venue, force }
 * returns { ok, meet, summary, warnings, errors }
 */
function convert(files, opts = {}) {
  const warn = [], errors = [];
  const scb = files.filter(f => /\.scb$/i.test(f.name));
  const rejected = files.filter(f => !/\.scb$/i.test(f.name)).map(f => f.name);
  if (rejected.length) warn.push(`ignored ${rejected.length} non-.scb file(s): ${rejected.slice(0, 5).join(', ')}${rejected.length > 5 ? '…' : ''}`);
  if (!scb.length) { errors.push('no .scb files supplied'); return { ok: false, warnings: warn, errors }; }

  // Stale-export guard. Reusing an export folder is the real footgun: last
  // week's events silently fold into tonight's board.
  const times = scb.map(f => f.lastModified).filter(Boolean);
  if (times.length > 1) {
    const spreadH = (Math.max(...times) - Math.min(...times)) / 3.6e6;
    if (spreadH > 6) {
      const msg = `file timestamps span ${spreadH.toFixed(1)} hours — this looks like a mix of sessions`;
      if (opts.force) warn.push(msg);
      else { errors.push(`${msg}. Export into a fresh folder, or tick "ignore timestamp spread".`); return { ok: false, warnings: warn, errors }; }
    }
  }

  const parsed = scb.map(f => parseOne(f.name, f.text, warn)).filter(Boolean)
    .sort((a, b) => a.no - b.no || a.suffix.localeCompare(b.suffix));
  if (!parsed.length) { errors.push('no readable .scb files — is this the CTS export?'); return { ok: false, warnings: warn, errors }; }

  const dupes = [...new Set(parsed.map(p => p.no + p.suffix)
    .filter((v, i, a) => a.indexOf(v) !== i))];
  if (dupes.length) warn.push(`duplicate event number(s): ${dupes.join(', ')} — two exports in one folder?`);

  const LANES = detectLanes(parsed, opts.lanes, warn);
  const teamsSeen = [], events = [];

  for (const p of parsed) {
    const meta = eventMeta(p.rawName);
    const nHeats = Math.floor(p.entries.length / LANES);
    if (p.entries.length % LANES)
      warn.push(`${p.file}: ${p.entries.length} entry lines is not a multiple of ${LANES}`);
    const heats = [];
    for (let h = 0; h < nHeats; h++) {
      const lanes = [];
      for (let i = 0; i < LANES; i++) {
        const line = p.entries[h * LANES + i];
        if (line.slice(SEP_AT, SEP_AT + 2) !== '--')
          warn.push(`${p.file} line ${h * LANES + i + 2}: expected '--' at column 21`);
        const nm = line.slice(0, NAME_W).replace(/\s+/g, ' ').trim();
        const tm = line.slice(TEAM_AT).replace(/\s+/g, ' ').trim();
        if (!nm) continue;
        if (tm && !teamsSeen.includes(tm)) teamsSeen.push(tm);
        lanes.push({ lane: i + 1, name: nm, team: tm, seed: null });
      }
      if (lanes.length) heats.push({ no: h + 1, lanes });
    }
    if (!heats.length) { warn.push(`${p.file}: no occupied lanes — skipped`); continue; }
    events.push({ no: p.no, name: title(p.rawName), distance: meta.distance,
                  relay: meta.relay || undefined, gender: meta.gender || undefined, heats });
  }

  // Colours: known schools claim theirs first, then unlisted teams draw from
  // the ramp skipping anything taken — otherwise a visitor is handed Tooele's
  // purple and the board lies about who is in the lane.
  const HOME = (opts.home || '').toUpperCase();
  const ordered = HOME && teamsSeen.includes(HOME)
    ? [HOME, ...teamsSeen.filter(t => t !== HOME)] : teamsSeen;

  const teams = {}, taken = new Set();
  for (const code of ordered) {
    const k = KNOWN[code.toUpperCase()];
    if (k) { teams[code] = { name: k.name, color: k.color, alt: k.alt }; taken.add(k.color.toUpperCase()); }
  }
  const free = RAMP.filter(c => !taken.has(c.toUpperCase()));
  let ri = 0;
  for (const code of ordered) {
    if (teams[code]) continue;
    const color = free[ri % free.length] || RAMP[ri % RAMP.length];
    const altPool = free.filter(c => c !== color);
    teams[code] = { name: code, color, alt: altPool[(ri + 3) % altPool.length] || color };
    ri++;
  }
  const unlisted = ordered.filter(c => !KNOWN[c.toUpperCase()]);
  if (unlisted.length > free.length)
    warn.push(`${unlisted.length} unlisted teams but ${free.length} free colours — some repeat; the abbreviation still carries identity`);

  const meet = {
    meet: opts.meetName || 'Swim Meet',
    venue: opts.venue || undefined,
    date: new Date().toISOString().slice(0, 10),
    laneCount: opts.poolLanes ? parseInt(opts.poolLanes, 10) : LANES,
    fileLanes: LANES,
    homeTeam: (HOME && teams[HOME]) ? HOME : (ordered[0] || null),
    generatedBy: 'scb2meet',
    teams, events, scores: []
  };

  const nHeats = events.reduce((n, e) => n + e.heats.length, 0);
  const nEntries = events.reduce((n, e) => n + e.heats.reduce((m, h) => m + h.lanes.length, 0), 0);

  return {
    ok: true, meet, warnings: warn, errors,
    summary: {
      files: scb.length, events: events.length, heats: nHeats, entries: nEntries,
      teams: ordered.length, unlisted, fileLanes: LANES, boardLanes: meet.laneCount,
      eventList: events.map(e => ({ no: e.no, name: e.name, heats: e.heats.length })),
      teamList: ordered.map(c => ({ code: c, name: teams[c].name, color: teams[c].color,
                                    known: !!KNOWN[c.toUpperCase()] }))
    }
  };
}

module.exports = { convert, KNOWN, RAMP };
