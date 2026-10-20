/* OSRS Bank Tag Layouts - an activity index and one activity at a time.
 *
 * The bank grid is 8 wide, so a layout position maps to (row, col) as
 * row = floor(pos / 8), col = pos % 8. Module:Loadout puts worn gear in
 * columns 0-2, leaves column 3 empty as a spacer, and lays the 28 inventory
 * slots out in columns 4-7. Positions run 0-55, so seven rows covers it.
 *
 * Slots the wiki ranked can be stepped through, which means a layout has to be
 * rebuilt here rather than patched: swapping to a two-handed weapon drops the
 * off-hand, and zigzag positions depend on which slots are filled. LOADOUT_MAP
 * and the zigzag order are shipped in layouts.json rather than copied into this
 * file, so they cannot drift from the generator.
 *
 * Only the selected layout is drawn. The address is #activity/variant, so a
 * layout can be linked to and survives a reload.
 */

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
    'In your bank, right-click New tag tab → Import tag tab. ' +
    'With the Bank Tag Layouts plugin, Import tag tab with layout works too — ' +
    'either way there is nothing to convert afterwards.',
  official:
    'In your bank, make a tag, then use its Import option and name the tab yourself — ' +
    'the official client’s string carries the items and their positions, but no name or icon.',
};
const CLIENT = { runelite: 'RuneLite', official: 'official client' };

const ICON = (id) => `https://static.runelite.net/cache/item/icon/${id}.png`;
const WIKI = (page) => `https://oldschool.runescape.wiki/w/${encodeURI(page.replace(/ /g, '_'))}`;
const slugify = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

const els = {
  q: document.getElementById('q'),
  list: document.getElementById('list'),
  detail: document.getElementById('detail'),
  count: document.getElementById('count'),
  meta: document.getElementById('meta'),
};

let LAYOUTS = [];
let LOADOUT_MAP = {};
let ZIGZAG_ORDER = [];
let TAGGING = {};
// Activities in data order, each with its variants: { slug, name, entries }.
let ACTIVITIES = [];
// Per-card slot choices, keyed by activity + variant so they survive a re-render
// (switching variants, or flipping between layout styles).
const CHOICES = new Map();
// The swappable slot whose controls are showing under the grid.
let activeSlot = null;
// Redrawing the pane replaces the button that was pressed, so keyboard focus
// is put back on its replacement: the first of these selectors that matches.
let refocus = null;

// NUL separator, so no activity or variant name can collide into another
// entry's key. Escaped rather than literal: a raw NUL makes git call the
// file binary, and every change to it then lands in review undiffable.
const keyOf = (entry) => `${entry.activity}\u0000${entry.variant}`;

const el = (tag, props = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k in node) node[k] = v;
    else node.setAttribute(k, v);
  }
  node.append(...kids.filter((k) => k != null));
  return node;
};

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

/* ---- routing -------------------------------------------------------------- */

function route() {
  let a, v;
  try {
    [a, v] = location.hash.slice(1).split('/').map(decodeURIComponent);
  } catch {
    return null; // a hand-mangled address; show the index rather than nothing
  }
  const activity = ACTIVITIES.find((x) => x.slug === a);
  if (!activity) return null;
  const entry = activity.entries.find((e) => slugify(e.variant) === v) || activity.entries[0];
  return { activity, entry };
}

const hrefFor = (entry) => `#${entry.slug}/${slugify(entry.variant)}`;

/* ---- index ---------------------------------------------------------------- */

function renderList() {
  const needle = els.q.value.trim().toLowerCase();
  const current = route();
  els.list.textContent = '';
  let shown = 0;
  let layouts = 0;

  for (const activity of ACTIVITIES) {
    let via = null;
    let first = activity.entries[0];
    if (needle && !activity.name.toLowerCase().includes(needle)) {
      const hits = activity.entries.filter(
        (e) => e.variant.toLowerCase().includes(needle) || e.tagName.toLowerCase().includes(needle)
      );
      if (!hits.length) continue;
      via = hits.map((e) => e.variant).join(', ');
      // Land on the variant that matched, not the activity's first.
      first = hits[0];
    }
    shown++;
    layouts += activity.entries.length;
    const a = el(
      'a',
      { href: hrefFor(first) },
      el('span', { class: 'name' }, activity.name, via && el('span', { class: 'via' }, via)),
      el('span', { class: 'n', title: 'layouts' }, String(activity.entries.length))
    );
    if (current && current.activity === activity) a.setAttribute('aria-current', 'page');
    // Picking the activity already in the address fires no hashchange, but on a
    // phone it still has to swap the index for the activity.
    a.addEventListener('click', () => document.body.classList.add('reading'));
    els.list.appendChild(el('li', {}, a));
  }

  els.count.textContent = needle
    ? `${shown} activit${shown === 1 ? 'y' : 'ies'} match`
    : `${layouts} layouts · ${shown} activities`;
  if (!shown) els.list.appendChild(el('li', { class: 'empty' }, 'Nothing matches that search.'));
}

/* ---- one layout ------------------------------------------------------------ */

function renderGrid(entry, choices) {
  const built = buildLayout(entry, choices);
  const grid = el('div', { class: 'grid' });
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
  for (const p of built.prepotAt) names.set(p.pos, `Prepot: ${p.name}`);

  for (let i = 0; i < cells; i++) {
    const g = groupAt.get(i);
    if (g) {
      grid.appendChild(
        el('a', {
          class: 'group-label',
          href: WIKI(g.source.page),
          rel: 'noopener',
          textContent: g.group,
          title: `${g.group}: items from the wiki's ${g.source.page} page`,
        })
      );
    }
    const id = built.layout[String(i)] ?? built.layout[i];
    const slot = id ? built.slots[String(i)] ?? built.slots[i] : null;
    const options = slot ? (entry.alternatives || {})[slot] : null;

    let cell;
    if (options && options.length > 1) {
      const at = choices[slot] ?? 0;
      cell = el('button', {
        type: 'button',
        class: 'cell',
        title: `${slot}: option ${at + 1} of ${options.length}`,
      });
      cell.setAttribute('aria-pressed', String(activeSlot === slot));
      cell.setAttribute('aria-label', `${slot}, option ${at + 1} of ${options.length}`);
      cell.dataset.slot = slot;
      cell.addEventListener('click', () => {
        activeSlot = activeSlot === slot ? null : slot;
        refocus = [`button.cell[data-slot="${slot}"]`];
        renderDetail();
      });
    } else {
      cell = el('div', { class: 'cell' });
      if (names.has(i)) cell.title = names.get(i);
    }
    if (id) cell.appendChild(el('img', { src: ICON(id), loading: 'lazy', alt: '' }));
    grid.appendChild(cell);
  }
  return { grid, built };
}

// Controls for the picked slot, under the grid rather than over the item, so
// they work by touch and leave the icon visible.
function swapBar(entry, choices, onChange) {
  const options = activeSlot && (entry.alternatives || {})[activeSlot];
  if (!options) return null;
  const at = choices[activeSlot] ?? 0;
  const bar = el(
    'div',
    { class: 'swap' },
    el('span', { class: 'slot' }, activeSlot),
    `${at + 1} of ${options.length}`
  );
  for (const dir of [-1, 1]) {
    const next = at + dir;
    const b = el('button', {
      type: 'button',
      class: 'step',
      textContent: dir < 0 ? '← Better' : 'Cheaper →',
      disabled: next < 0 || next >= options.length,
    });
    // "Better" and "worse" are the wiki's ranking, not ours.
    b.setAttribute(
      'aria-label',
      `${activeSlot}: ${dir < 0 ? 'closer to best in slot' : 'cheaper alternative'} ` +
        `(currently ${at + 1} of ${options.length})`
    );
    b.dataset.dir = String(dir);
    b.addEventListener('click', () => {
      // The same arrow, or the other one once this end of the ranking is hit.
      refocus = [`.swap button[data-dir="${dir}"]:not(:disabled)`, '.swap button.step:not(:disabled)'];
      onChange(activeSlot, next);
    });
    bar.appendChild(b);
  }
  return bar;
}

function segmented(label, current, options, onPick) {
  const group = el('span', { role: 'group', 'aria-label': label });
  for (const [value, text] of options) {
    const b = el('button', { type: 'button', textContent: text });
    b.setAttribute('aria-pressed', String(value === current));
    b.addEventListener('click', () => onPick(value));
    group.appendChild(b);
  }
  return el('div', { class: 'seg' }, label, group);
}

function renderWelcome() {
  els.detail.textContent = '';
  els.detail.appendChild(
    el(
      'div',
      { class: 'welcome' },
      el('h2', {}, 'Pick an activity'),
      el(
        'p',
        {},
        'Gear and inventory setups for bosses, raids and activities, built from the ',
        el('a', { href: 'https://oldschool.runescape.wiki/', rel: 'noopener' }, 'OSRS Wiki'),
        ' strategy pages.'
      ),
      el(
        'ol',
        {},
        el('li', {}, 'Choose an activity and a variant.'),
        el('li', {}, 'Swap any highlighted slot for gear you own.'),
        el('li', {}, 'Copy, then import it in your bank.')
      )
    )
  );
}

function renderDetail() {
  const current = route();
  if (!current) return renderWelcome();
  const { activity, entry } = current;
  const choices = CHOICES.get(keyOf(entry)) || {};
  const modified = isModified(choices);
  const rerender = () => renderDetail();

  const onChange = (slot, index) => {
    CHOICES.set(keyOf(entry), { ...choices, [slot]: index });
    rerender();
  };

  const pane = el('article', { class: 'pane' });
  pane.appendChild(
    el('a', { class: 'back', href: '#', textContent: '← All activities' })
  );
  pane.appendChild(
    el(
      'div',
      { class: 'crumbs' },
      el('h2', {}, activity.name),
      el('a', { href: entry.sourceUrl, rel: 'noopener', textContent: 'Wiki guide ↗' })
    )
  );

  if (activity.entries.length > 1) {
    const tabs = el('nav', { class: 'tabs', 'aria-label': 'Variants' });
    for (const e of activity.entries) {
      const a = el('a', { href: hrefFor(e), textContent: e.variant });
      if (e === entry) a.setAttribute('aria-current', 'page');
      tabs.appendChild(a);
    }
    pane.appendChild(tabs);
  }

  // The official client's string has no name field, so the name is a suggestion
  // there rather than something the import will set.
  const meta = el(
    'div',
    { class: 'meta' },
    el(
      'span',
      { class: 'tagname' },
      target === 'official' ? `Suggested tab name: ${entry.tagName}` : `Tab name: ${entry.tagName}`
    )
  );
  if (entry.completeness && entry.completeness !== 'complete') {
    meta.appendChild(
      el('span', {
        class: 'badge',
        textContent: entry.completeness === 'minimal' ? 'small by design' : 'may be partial',
        ...(entry.completenessNote ? { title: entry.completenessNote } : {}),
      })
    );
  }
  // Curated layouts deliberately differ from the wiki page, so say so rather
  // than passing the correction off as the wiki's own answer.
  if (entry.curated) {
    meta.appendChild(
      el('span', {
        class: 'badge curated',
        textContent: 'curated',
        ...(entry.curationReason ? { title: entry.curationReason } : {}),
      })
    );
  }
  pane.appendChild(meta);

  const { grid, built } = renderGrid(entry, choices);
  pane.appendChild(grid);
  const swap = swapBar(entry, choices, onChange);
  if (swap) pane.appendChild(swap);

  if (Object.keys(entry.alternatives || {}).length) {
    pane.appendChild(
      el(
        'div',
        { class: 'hint' },
        modified
          ? 'Swapped from the wiki’s picks — the copied layout is yours.'
          : 'Highlighted slots have alternatives — pick one to step down the wiki’s ranking.'
      )
    );
  }

  pane.appendChild(
    el(
      'div',
      { class: 'actions' },
      segmented('Client', target, [['runelite', 'RuneLite'], ['official', 'Official']], (v) => {
        target = v;
        localStorage.setItem(TARGET_KEY, target);
        rerender();
      }),
      segmented('Style', style, [['presets', 'Presets'], ['zigzag', 'Zigzag']], (v) => {
        style = v;
        localStorage.setItem(STYLE_KEY, style);
        rerender();
      })
    )
  );

  // Built here for every layout, swapped or not: layouts.json ships the layout
  // rather than four spellings of it, and check_layout_port.mjs pins these
  // against the strings the generator wrote into data/*.json.
  const code = importStringFor(entry, built.layout, built.equipment);
  const label = `Copy ${modified ? 'your layout' : 'layout'} for ${CLIENT[target]}`;
  const btn = el('button', { type: 'button', class: 'copy', textContent: label });
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      // Clipboard API needs a secure context; fall back to a temp selection.
      const ta = el('textarea', { value: code });
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    btn.textContent = 'Copied';
    btn.classList.add('done');
    setTimeout(() => {
      btn.textContent = label;
      btn.classList.remove('done');
    }, 1400);
  });
  const row = el('div', { class: 'copyrow' }, btn);
  if (modified) {
    const reset = el('button', { type: 'button', class: 'reset', textContent: 'Reset to wiki' });
    reset.addEventListener('click', () => {
      CHOICES.delete(keyOf(entry));
      rerender();
    });
    row.appendChild(reset);
  }
  pane.appendChild(row);

  pane.appendChild(
    el('details', {}, el('summary', {}, `How to import in ${CLIENT[target]}`), el('p', {}, HOWTO[target]))
  );

  if (entry.warnings && entry.warnings.length) {
    pane.appendChild(
      el(
        'details',
        {},
        el('summary', {}, `${entry.warnings.length} item name(s) normalised`),
        el('ul', {}, ...entry.warnings.map((w) => el('li', {}, w)))
      )
    );
  }

  els.detail.textContent = '';
  els.detail.appendChild(pane);
  if (refocus) refocus.map((q) => pane.querySelector(q)).find(Boolean)?.focus();
  refocus = null;
}

function show() {
  activeSlot = null;
  document.body.classList.toggle('reading', !!route());
  renderList();
  renderDetail();
  els.detail.scrollTop = 0;
  window.scrollTo(0, 0);
  // A linked activity can sit far down the index; bring it into view.
  // Scrolled by hand: scrollIntoView would also move the page around it.
  const picked = els.list.querySelector('[aria-current]');
  if (picked) {
    const top = picked.offsetTop - els.list.offsetTop;
    if (top < els.list.scrollTop || top + picked.offsetHeight > els.list.scrollTop + els.list.clientHeight) {
      els.list.scrollTop = top - els.list.clientHeight / 2;
    }
  }
  const current = route();
  document.title = current
    ? `${current.activity.name} · ${current.entry.variant} — OSRS Bank Tag Layouts`
    : 'OSRS Bank Tag Layouts';
}

// The rebuilt layout has to reproduce the published one before any swap, or the
// port above has drifted from the generator. Cheap to check, and silence here
// is what makes the swaps trustworthy.
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
    const bySlug = new Map();
    for (const entry of LAYOUTS) {
      if (!bySlug.has(entry.slug)) {
        bySlug.set(entry.slug, { slug: entry.slug, name: entry.activity, entries: [] });
      }
      bySlug.get(entry.slug).entries.push(entry);
    }
    ACTIVITIES = [...bySlug.values()];
    els.meta.textContent = `${LAYOUTS.length} layouts generated ${data.generatedAt}.`;
    checkPortAgainstGenerator();
    show();

    window.addEventListener('hashchange', show);
    let timer;
    els.q.addEventListener('input', () => {
      // Searching from inside an activity on a phone goes back to the index.
      document.body.classList.remove('reading');
      clearTimeout(timer);
      timer = setTimeout(renderList, 80);
    });
    els.q.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      const first = els.list.querySelector('a');
      if (first) {
        document.body.classList.add('reading');
        first.click();
      }
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key !== '/' || ev.target.closest('input, textarea')) return;
      ev.preventDefault();
      els.q.focus();
    });
    window.__btl = { checkPortAgainstGenerator, buildLayout, importStringFor };
  })
  .catch(() => {
    els.list.textContent = '';
    els.list.appendChild(el('li', { class: 'empty' }, 'Could not load layouts.json.'));
  });
