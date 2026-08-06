/* Prove docs/layout.js still agrees with generator/encode.py.
 *
 * The site rebuilds layouts in the browser so a swapped slot can change which
 * slots are filled. That means a second implementation of the layout maths
 * exists, and a silent divergence would hand players a subtly wrong import
 * string. So: rebuild every published layout in both styles with no swaps
 * applied, and require it to equal what the generator wrote.
 *
 * Run: node tests/check_layout_port.mjs
 */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');

// layout.js is a plain browser script that hangs BTL off the global object.
// Loading it through require runs it exactly as the browser would.
createRequire(import.meta.url)(join(REPO, 'docs', 'layout.js'));
const { BTL } = globalThis;

const data = JSON.parse(readFileSync(join(REPO, 'docs', 'layouts.json'), 'utf8'));
const { loadoutMap, zigzagOrder, layouts } = data;

function parsePairs(code) {
  const parts = code.split(',');
  const tail = parts.slice(parts.indexOf('layout') + 1);
  const pairs = [];
  for (let i = 0; i + 1 < tail.length; i += 2) pairs.push([Number(tail[i]), tail[i + 1]]);
  pairs.sort((a, b) => a[0] - b[0]);
  return `${parts.slice(0, 4).join(',')}|${pairs.map((p) => p.join(':')).join(',')}`;
}

const canonical = (layout) =>
  Object.keys(layout)
    .map(Number)
    .sort((a, b) => a - b)
    .map((p) => `${p}:${layout[p]}`)
    .join(',');

let checked = 0;
const failures = [];

for (const entry of layouts) {
  for (const style of ['presets', 'zigzag']) {
    const published = style === 'zigzag' ? entry.layoutZigzag : entry.layout;
    const rebuilt = BTL.buildLayout(entry, {}, style, loadoutMap, zigzagOrder).layout;
    checked++;
    if (canonical(published) !== canonical(rebuilt)) {
      failures.push(`${entry.activity} / ${entry.variant} [${style}]`);
      continue;
    }
    // The copy button builds its string from the rebuilt layout, so that has to
    // carry the same pairs. Compared as parsed pairs rather than literally:
    // Module:Loadout emits them unsorted, and order is not meaningful to the
    // plugin's importer.
    const expected = style === 'zigzag' ? entry.importStringZigzag : entry.importString;
    if (parsePairs(BTL.importStringFor(entry, rebuilt)) !== parsePairs(expected)) {
      failures.push(`${entry.activity} / ${entry.variant} [${style}] import string`);
    }
  }
}

// Swapping a slot must produce a coherent loadout, not just a different one.
let swaps = 0;
for (const entry of layouts) {
  const alternatives = entry.alternatives || {};
  const twoHanded = new Set(entry.twoHandedWeapons || []);
  for (const [slot, options] of Object.entries(alternatives)) {
    for (let i = 0; i < options.length; i++) {
      const worn = BTL.wornItems(entry, { [slot]: i });
      swaps++;
      if (slot !== 'shield' && worn[slot] !== options[i]) {
        failures.push(`${entry.activity} / ${entry.variant}: ${slot} option ${i} not applied`);
      }
      if (worn.weapon && twoHanded.has(worn.weapon) && worn.shield) {
        failures.push(
          `${entry.activity} / ${entry.variant}: ${slot} option ${i} leaves a ` +
            `two-handed weapon beside an off-hand`
        );
      }
    }
  }
}

if (failures.length) {
  console.error(`${failures.length} mismatch(es):`);
  for (const f of failures.slice(0, 20)) console.error(`  ${f}`);
  process.exit(1);
}
console.log(`ok: ${checked} layouts rebuilt identically, ${swaps} swaps stay wearable`);
