#!/usr/bin/env node
/**
 * scb2meet — CLI wrapper. Reads a folder of .scb exports, writes one meet.json.
 * Shares all parsing with the web uploader via src/scb.js.
 *
 *   node src/scb2meet.js ./export/2026-12-11 -o data/meet.json \
 *     --home TOOEL --pool-lanes 6 --name "Tooele County Tri-Meet"
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { convert } = require('./scb');

const OPTS  = { o:1, out:1, lanes:1, 'pool-lanes':1, home:1, name:1, venue:1 };
const FLAGS = { force:1, quiet:1 };

const opt = {}, pos = [];
{
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const t = a[i];
    const key = t.startsWith('--') ? t.slice(2) : t.startsWith('-') ? t.slice(1) : null;
    if (key && OPTS[key]) opt[key] = a[++i];
    else if (key && FLAGS[key]) opt[key] = true;
    else if (key) { console.error(`unknown option: ${t}`); process.exit(1); }
    else pos.push(t);
  }
}

const IN_DIR = pos[0] || './export';
const OUT    = opt.o || opt.out || 'data/meet.json';
const say    = (...a) => { if (!opt.quiet) console.log(...a); };

if (!fs.existsSync(IN_DIR) || !fs.statSync(IN_DIR).isDirectory()) {
  console.error(`not a directory: ${IN_DIR}`); process.exit(1);
}

const files = fs.readdirSync(IN_DIR)
  .filter(f => /\.scb$/i.test(f))
  .map(f => {
    const full = path.join(IN_DIR, f);
    return { name: f, text: fs.readFileSync(full, 'latin1'), lastModified: fs.statSync(full).mtimeMs };
  });

if (!files.length) { console.error(`no .scb files in ${IN_DIR}`); process.exit(1); }

const r = convert(files, {
  lanes: opt.lanes, poolLanes: opt['pool-lanes'], home: opt.home,
  meetName: opt.name, venue: opt.venue, force: opt.force
});

for (const w of r.warnings) console.warn(`  ! ${w}`);
if (!r.ok) { for (const e of r.errors) console.error(`REFUSING: ${e}`); process.exit(2); }

fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
if (fs.existsSync(OUT)) fs.copyFileSync(OUT, OUT + '.bak');
fs.writeFileSync(OUT, JSON.stringify(r.meet, null, 1));

const s = r.summary;
say(OUT);
say(`  ${s.events} events, ${s.heats} heats, ${s.entries} entries, ${s.teams} teams`);
say(`  ${s.fileLanes} lanes per heat in the export · board draws ${s.boardLanes}`);
if (s.unlisted.length) say(`  no colour table entry: ${s.unlisted.join(', ')} — assigned from the fallback ramp`);
