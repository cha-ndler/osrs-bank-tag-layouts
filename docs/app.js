/* OSRS Bank Tag Layouts - client-side search over the generated library.
 *
 * The bank grid is 8 wide, so a layout position maps to (row, col) as
 * row = floor(pos / 8), col = pos % 8. Module:Loadout puts worn gear in
 * columns 0-2, leaves column 3 empty as a spacer, and lays the 28 inventory
 * slots out in columns 4-7. Positions run 0-55, so seven rows covers it.
 *
 * Slots the wiki ranked can be stepped through with the arrows, which means a
 * layout has to be rebuilt here rather than patched: swapping to a two-handed
 * weapon drops the off-hand, and zigzag positions depend on which slots are
 * filled. LOADOUT_MAP and the zigzag order are shipped in layouts.json rather
 * than copied into this file, so they cannot drift from the generator.
 */

// Every card draws a ~56-cell grid, so rendering the whole library at once
// costs ~30k DOM nodes. Cap it and let search reach the rest.
const MAX_RENDERED = 60;
const STYLE_KEY = 'btl-style';
const TARGET_KEY = 'btl-target';

// The plugin's two arrangements. Presets shapes worn gear like the equipment
// panel; zigzag is the plugin's own default and packs items two per column.
let style = localStorage.getItem(STYLE_KEY) === 'zigzag' ? 'zigzag' : 'presets';

// Which client the copied string is for. RuneLite's string is the Plugin Hub
// format, which both RuneLite importers read - see docs/layout.js. The official
// client got bank tags of its own in June 2026 and its own import string in
// July, and that format shares nothing with either RuneLite one.
let target = localStorage.getItem(TARGET_KEY) === 'official' ? 'official' : 'runelite';

const HOWTO = {
  runelite:
    'In your bank, right-click New tag tab \u2192 Import tag tab. ' +
    'With the Bank Tag Layouts plugin, Import tag tab with layout works too \u2014 ' +
    'either way there is nothing to convert afterwards.',
  official:
    'In your bank, make a tag, then use its Import option and name the tab yourself \u2014 ' +
    'the official client\u2019s string carries the items and their positions, but no name or icon.',
};

const ICON = (id) => `https://static.runelite.net/cache/item/icon/${id}.png`;
const WIKI = (page) => `https://oldschool.runescape.wiki/w/${encodeURI(page.replace(/ /g, '_'))}`;

const els = {
  q: document.getElementById('q'),
  results: document.getElementById('results'),
  count: document.getElementById('count'),
  meta: document.getElementById('meta'),
  howto: document.getElementById('howto'),
};

let LAYOUTS = [];
let LOADOUT_MAP = {};
let ZIGZAG_ORDER = [];
let TAGGING = {};
// Per-card slot choices, keyed by activity + variant so they survive a re-render
// (searching, or flipping between layout styles).
const CHOICES = new Map();

// NUL separator, so no activity or variant name can collide into another
// entry's key. Escaped rather than literal: a raw NUL makes git call the
// file binary, and every change to it then lands in review undiffable.
const keyOf = (entry) => `${entry.activity}\u0000${entry.variant}`;

function syncToggles() {
  for (const btn of document.querySelectorAll('button.style')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.style === style));
  }
  for (const btn of document.querySelectorAll('button.target')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.target === target));
  }
  els.howto.textContent = HOWTO[target];
}

/* ---- layout building ------------------------------------------------------
 * The maths lives in layout.js so it can be checked against the generator
 * headlessly. This file only supplies the current style and constants.
 */

const buildLayout = (entry, choices) =>
  BTL.buildLayout(entry, choices, style, LOADOUT_MAP, ZIGZAG_ORDER);
// The tab icon is the weapon, so it should follow the weapon that was chosen -
// otherwise a swapped layout shows an item it no longer carries.
function importStringFor(entry, layout, equipment) {
  const icon =
    entry.icon === entry.equipment.weapon && equipment.weapon ? equipment.weapon : entry.icon;
  return BTL.importStringFor(entry, layout, icon, target, TAGGING);
}

const isModified = (choices) => Object.values(choices).some((i) => i > 0);

/* ---- rendering ----------------------------------------------------------- */

function renderGrid(entry, choices, onChange) {
  const built = buildLayout(entry, choices);
  const grid = document.createElement('div');
  grid.className = 'grid';
  // Size to the content: a layout can run past seven rows, and the bank
  // scrolls, so a fixed 56 cells would silently clip items.
  const positions = Object.keys(built.layout).map(Number);
  const highest = positions.length ? Math.max(...positions) : 0;
  const cells = (Math.floor(highest / 8) + 1) * 8;

  // Clue supply groups start on a row of their own (see placeSupplies), so a
  // label spanning the grid above that row names it without moving any cell.
  const groupAt = new Map();
  const names = new Map();
  for (const g of built.supplyGroups) {
    groupAt.set(g.start, g);
    g.items.forEach((item, n) => names.set(g.start + n, item.name));
  }

  for (let i = 0; i < cells; i++) {
    const g = groupAt.get(i);
    if (g) {
      const label = document.createElement('a');
      label.className = 'group-label';
      label.href = WIKI(g.source.page);
      label.rel = 'noopener';
      label.textContent = g.group;
      label.title = `${g.group}: items from the wiki's ${g.source.page} page`;
      grid.appendChild(label);
    }
    const cell = document.createElement('div');
    cell.className = 'cell';
    const id = built.layout[String(i)] ?? built.layout[i];
    if (id) {
      const img = document.createElement('img');
      img.src = ICON(id);
      img.loading = 'lazy';
      img.alt = '';
      cell.appendChild(img);
      if (names.has(i)) cell.title = names.get(i);

      const slot = built.slots[String(i)] ?? built.slots[i];
      const options = slot ? (entry.alternatives || {})[slot] : null;
      if (options && options.length > 1) {
        const at = choices[slot] ?? 0;
        cell.classList.add('swappable');
        cell.title = `${slot}: option ${at + 1} of ${options.length}`;
        cell.appendChild(stepper(slot, at, options.length, onChange));
      }
    }
    grid.appendChild(cell);
  }
  return { grid, built };
}

function stepper(slot, at, total, onChange) {
  const wrap = document.createElement('div');
  wrap.className = 'steps';
  for (const dir of [-1, 1]) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = dir < 0 ? 'step prev' : 'step next';
    b.textContent = dir < 0 ? '◀' : '▶';
    const next = at + dir;
    b.disabled = next < 0 || next >= total;
    // "Better" and "worse" are the wiki's ranking, not ours.
    b.setAttribute(
      'aria-label',
      `${slot}: ${dir < 0 ? 'closer to best in slot' : 'cheaper alternative'} ` +
        `(currently ${at + 1} of ${total})`
    );
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (!b.disabled) onChange(slot, next);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

function renderCard(entry) {
  const card = document.createElement('div');
  card.className = 'card';
  const choices = CHOICES.get(keyOf(entry)) || {};

  const h3 = document.createElement('h3');
  h3.textContent = entry.variant;
  if (entry.completeness && entry.completeness !== 'complete') {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = entry.completeness === 'minimal' ? 'small by design' : 'may be partial';
    if (entry.completenessNote) badge.title = entry.completenessNote;
    h3.appendChild(badge);
  }
  // Curated layouts deliberately differ from the wiki page, so say so rather
  // than passing the correction off as the wiki's own answer.
  if (entry.curated) {
    const badge = document.createElement('span');
    badge.className = 'badge curated';
    badge.textContent = 'curated';
    if (entry.curationReason) badge.title = entry.curationReason;
    h3.appendChild(badge);
  }
  card.appendChild(h3);

  const tag = document.createElement('div');
  tag.className = 'tagname';
  // The official client's string has no name field, so the name is a suggestion
  // there rather than something the import will set.
  tag.textContent =
    target === 'official'
      ? `suggested tab name: ${entry.tagName}`
      : `tab name: ${entry.tagName}`;
  card.appendChild(tag);

  const rerender = () => {
    const fresh = renderCard(entry);
    card.replaceWith(fresh);
  };
  const onChange = (slot, index) => {
    const next = { ...choices, [slot]: index };
    CHOICES.set(keyOf(entry), next);
    rerender();
  };

  const { grid, built } = renderGrid(entry, choices, onChange);
  card.appendChild(grid);

  const row = document.createElement('div');
  row.className = 'row';

  const modified = isModified(choices);
  // Built here for every card, swapped or not: layouts.json ships the layout
  // rather than four spellings of it, and check_layout_port.mjs pins these
  // against the strings the generator wrote into data/*.json.
  const code = importStringFor(entry, built.layout, built.equipment);

  const btn = document.createElement('button');
  btn.className = 'copy';
  btn.type = 'button';
  btn.textContent = modified ? 'Copy your layout' : 'Copy layout';
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      // Clipboard API needs a secure context; fall back to a temp selection.
      const ta = document.createElement('textarea');
      ta.value = code;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    btn.textContent = 'Copied';
    btn.classList.add('done');
    setTimeout(() => {
      btn.textContent = modified ? 'Copy your layout' : 'Copy layout';
      btn.classList.remove('done');
    }, 1400);
  });
  row.appendChild(btn);

  if (modified) {
    const reset = document.createElement('button');
    reset.className = 'reset';
    reset.type = 'button';
    reset.textContent = 'Reset to wiki';
    reset.addEventListener('click', () => {
      CHOICES.delete(keyOf(entry));
      rerender();
    });
    row.appendChild(reset);
  }
  card.appendChild(row);

  if (Object.keys(entry.alternatives || {}).length) {
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = modified
      ? 'Swapped from the wiki’s picks — the copied layout is yours.'
      : 'Hover a highlighted slot to step down the wiki’s ranking.';
    card.appendChild(hint);
  }

  if (entry.warnings && entry.warnings.length) {
    const d = document.createElement('details');
    d.className = 'warn';
    const s = document.createElement('summary');
    s.textContent = `${entry.warnings.length} item name(s) normalised`;
    d.appendChild(s);
    const ul = document.createElement('ul');
    for (const w of entry.warnings) {
      const li = document.createElement('li');
      li.textContent = w;
      ul.appendChild(li);
    }
    d.appendChild(ul);
    card.appendChild(d);
  }

  return card;
}

function render(term) {
  const needle = term.trim().toLowerCase();
  const matches = needle
    ? LAYOUTS.filter(
        (e) =>
          e.activity.toLowerCase().includes(needle) ||
          e.variant.toLowerCase().includes(needle) ||
          e.tagName.toLowerCase().includes(needle)
      )
    : LAYOUTS;

  // Group first, then cap on whole activities. Slicing a flat list would show
  // an activity with only some of its variants and no sign the rest exist.
  const byActivity = new Map();
  for (const entry of matches) {
    if (!byActivity.has(entry.activity)) byActivity.set(entry.activity, []);
    byActivity.get(entry.activity).push(entry);
  }

  const groups = new Map();
  let rendered = 0;
  for (const [activity, entries] of byActivity) {
    if (rendered >= MAX_RENDERED) break;
    groups.set(activity, entries);
    rendered += entries.length;
  }

  const activities = byActivity.size;
  let summary = `${matches.length} layout${matches.length === 1 ? '' : 's'} across ${activities} activit${activities === 1 ? 'y' : 'ies'}`;
  if (groups.size < activities) {
    summary += ` — showing ${groups.size} of ${activities}, search to narrow`;
  }
  els.count.textContent = summary;
  els.results.textContent = '';

  if (!matches.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = 'Nothing matches that search.';
    els.results.appendChild(p);
    return;
  }

  const frag = document.createDocumentFragment();
  for (const [activity, entries] of groups) {
    const section = document.createElement('section');
    section.className = 'activity';

    const h2 = document.createElement('h2');
    h2.appendChild(document.createTextNode(activity));
    const link = document.createElement('a');
    link.href = entries[0].sourceUrl;
    link.rel = 'noopener';
    link.textContent = 'wiki source';
    h2.appendChild(link);
    section.appendChild(h2);

    const wrap = document.createElement('div');
    wrap.className = 'variants';
    for (const entry of entries) wrap.appendChild(renderCard(entry));
    section.appendChild(wrap);
    frag.appendChild(section);
  }
  els.results.appendChild(frag);
}

// The rebuilt layout has to reproduce the published one before any swap, or the
// port above has drifted from the generator. Cheap to check, and silence here
// is what makes the arrows trustworthy.
function checkPortAgainstGenerator() {
  const bad = [];
  for (const entry of LAYOUTS) {
    const published = (style === 'zigzag' ? entry.layoutZigzag : entry.layout) || entry.layout;
    const rebuilt = buildLayout(entry, {}).layout;
    const a = JSON.stringify(Object.keys(published).map(Number).sort((x, y) => x - y).map((p) => [p, published[p]]));
    const b = JSON.stringify(Object.keys(rebuilt).map(Number).sort((x, y) => x - y).map((p) => [p, rebuilt[p]]));
    if (a !== b) bad.push(`${entry.activity} / ${entry.variant}`);
  }
  if (bad.length) {
    console.warn(`layout rebuild differs from the generator for ${bad.length} layout(s):`, bad.slice(0, 5));
  }
  return bad;
}

fetch('layouts.json')
  .then((r) => r.json())
  .then((data) => {
    LAYOUTS = data.layouts;
    LOADOUT_MAP = data.loadoutMap || {};
    ZIGZAG_ORDER = data.zigzagOrder || [];
    TAGGING = data.tagging || {};
    els.meta.textContent = `${LAYOUTS.length} layouts generated ${data.generatedAt} from the OSRS Wiki.`;
    syncToggles();
    checkPortAgainstGenerator();
    render('');
    let timer;
    els.q.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => render(els.q.value), 120);
    });
    for (const btn of document.querySelectorAll('button.style')) {
      btn.addEventListener('click', () => {
        style = btn.dataset.style;
        localStorage.setItem(STYLE_KEY, style);
        syncToggles();
        render(els.q.value);
      });
    }
    for (const btn of document.querySelectorAll('button.target')) {
      btn.addEventListener('click', () => {
        target = btn.dataset.target;
        localStorage.setItem(TARGET_KEY, target);
        syncToggles();
        render(els.q.value);
      });
    }
    window.__btl = { checkPortAgainstGenerator, buildLayout, importStringFor };
  })
  .catch(() => {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = 'Could not load layouts.json.';
    els.results.textContent = '';
    els.results.appendChild(p);
  });
