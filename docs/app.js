/* OSRS Bank Tag Layouts - client-side search over the generated library.
 *
 * The bank grid is 8 wide, so a layout position maps to (row, col) as
 * row = floor(pos / 8), col = pos % 8. Module:Loadout puts worn gear in
 * columns 0-2, leaves column 3 empty as a spacer, and lays the 28 inventory
 * slots out in columns 4-7. Positions run 0-55, so seven rows covers it.
 */

const GRID_CELLS = 56;
// Every card draws a 56-cell grid, so rendering the whole library at once costs
// ~30k DOM nodes. Cap it and let search reach the rest.
const MAX_RENDERED = 60;
const ICON = (id) => `https://static.runelite.net/cache/item/icon/${id}.png`;

const els = {
  q: document.getElementById('q'),
  results: document.getElementById('results'),
  count: document.getElementById('count'),
  meta: document.getElementById('meta'),
};

let LAYOUTS = [];

function renderGrid(layout) {
  const grid = document.createElement('div');
  grid.className = 'grid';
  for (let i = 0; i < GRID_CELLS; i++) {
    const cell = document.createElement('div');
    cell.className = 'cell';
    const id = layout[String(i)];
    if (id) {
      const img = document.createElement('img');
      img.src = ICON(id);
      img.loading = 'lazy';
      img.alt = '';
      cell.appendChild(img);
    }
    grid.appendChild(cell);
  }
  return grid;
}

function renderCard(entry) {
  const card = document.createElement('div');
  card.className = 'card';

  const h3 = document.createElement('h3');
  h3.textContent = entry.variant;
  card.appendChild(h3);

  const tag = document.createElement('div');
  tag.className = 'tagname';
  tag.textContent = `tab name: ${entry.tagName}`;
  card.appendChild(tag);

  card.appendChild(renderGrid(entry.layout));

  const row = document.createElement('div');
  row.className = 'row';
  const btn = document.createElement('button');
  btn.className = 'copy';
  btn.type = 'button';
  btn.textContent = 'Copy layout';
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(entry.importString);
    } catch {
      // Clipboard API needs a secure context; fall back to a temp selection.
      const ta = document.createElement('textarea');
      ta.value = entry.importString;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    btn.textContent = 'Copied';
    btn.classList.add('done');
    setTimeout(() => {
      btn.textContent = 'Copy layout';
      btn.classList.remove('done');
    }, 1400);
  });
  row.appendChild(btn);
  card.appendChild(row);

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

fetch('layouts.json')
  .then((r) => r.json())
  .then((data) => {
    LAYOUTS = data.layouts;
    els.meta.textContent = `${LAYOUTS.length} layouts generated ${data.generatedAt} from the OSRS Wiki.`;
    render('');
    let timer;
    els.q.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => render(els.q.value), 120);
    });
  })
  .catch(() => {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = 'Could not load layouts.json.';
    els.results.textContent = '';
    els.results.appendChild(p);
  });
