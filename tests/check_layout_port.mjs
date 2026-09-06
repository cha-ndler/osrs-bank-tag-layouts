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
import { readdirSync, readFileSync } from 'node:fs';
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
  }
}

/* The site no longer ships import strings - the copy button builds one in the
 * browser for every card - so the browser's encoders are pinned against the
 * ones the generator wrote into data/*.json instead. All four combinations:
 * a format only one client accepts is exactly the bug this is here to stop.
 */
const STRINGS = [
  ['presets', 'runelite', 'importString'],
  ['zigzag', 'runelite', 'importStringZigzag'],
  ['presets', 'official', 'importStringOfficial'],
  ['zigzag', 'official', 'importStringZigzagOfficial'],
];

let strings = 0;
for (const file of readdirSync(join(REPO, 'data')).sort()) {
  const record = JSON.parse(readFileSync(join(REPO, 'data', file), 'utf8'));
  for (const variant of record.variants) {
    // data/*.json splits a layout by section; the browser encoders read the
    // whole entry, so give them the shape the site's rows have.
    const entry = { ...variant, activity: record.activity };
    for (const [style, target, field] of STRINGS) {
      const layout = style === 'zigzag' ? variant.layoutZigzag : variant.layout;
      strings++;
      if (BTL.importStringFor(entry, layout, variant.icon, target) !== variant[field]) {
        failures.push(`${record.activity} / ${variant.variant}: ${field} differs`);
      }
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
console.log(
  `ok: ${checked} layouts rebuilt identically, ${strings} import strings match ` +
    `the generator, ${swaps} swaps stay wearable`
);
