/* Layout maths, ported from generator/encode.py. No DOM, so the port can be
 * checked headlessly against the generator's own output - see
 * tests/check_layout_port.mjs, which CI runs on every pull request.
 *
 * A layout is rebuilt here rather than patched in place, because swapping a
 * slot can change which slots are filled at all: a two-handed weapon leaves no
 * hand for an off-hand, and zigzag positions depend on the filled set.
 */
(function (root) {
  'use strict';

  // Port of LayoutGenerator.toZigZagIndex: items fill a two-row block column by
  // column, so a 16-item run covers exactly two rows of the 8-wide bank grid.
  function zigzagIndex(i) {
    const row = Math.floor(i / 16) * 2;
    const j = i - Math.floor(i / 16) * 16;
    return (j % 2 === 0 ? 0 : 8) + Math.floor(j / 2) + row * 8;
  }

  // Port of LayoutGenerator.layoutItems, including its cursor advance.
  function place(items, layout, slots, slotNames, start, useZigzag) {
    let i = start;
    for (let n = 0; n < items.length; n++) {
      const pos = useZigzag ? zigzagIndex(i) : i;
      layout[pos] = items[n];
      if (slotNames && slotNames[n]) slots[pos] = slotNames[n];
      i++;
    }
    const positions = Object.keys(layout);
    if (items.length && positions.length) {
      const highest = Math.max.apply(null, positions.map(Number));
      // After a group the cursor jumps to the next row-pair (zigzag) or the
      // next row (linear), which keeps groups visually separated.
      i = useZigzag
        ? (Math.floor(highest / 16) * 2 + 2) * 8
        : (Math.floor(highest / 8) + 1) * 8;
    }
    return i;
  }

  /** Worn items after applying the arrows' choices. */
  function wornItems(entry, choices) {
    const equipment = Object.assign({}, entry.equipment);
    const alternatives = entry.alternatives || {};
    const twoHanded = entry.twoHandedWeapons || [];
    // The shield is the one slot the generator removes: a two-handed weapon
    // leaves no hand for it. Its ladder is still published, so it can come back
    // if the weapon does. No other unworn slot may be filled in - a ladder can
    // outlive its slot, and inventing one would add gear the wiki never put in
    // this loadout.
    const shieldDropped =
      !!entry.equipment.weapon && twoHanded.indexOf(entry.equipment.weapon) !== -1;

    for (const slot of Object.keys(alternatives)) {
      if (!(slot in entry.equipment) && !(slot === 'shield' && shieldDropped)) continue;
      const at = choices[slot] === undefined ? 0 : choices[slot];
      const picked = alternatives[slot][at];
      if (picked) equipment[slot] = picked;
    }

    if (equipment.weapon && twoHanded.indexOf(equipment.weapon) !== -1) {
      delete equipment.shield;
    }
    return equipment;
  }

  /** {layout, slots, equipment} for a card in the given style. */
  function buildLayout(entry, choices, style, loadoutMap, zigzagOrder) {
    const equipment = wornItems(entry, choices);
    const inventory = entry.inventory || {};
    const runes = entry.runes || {};
    const layout = {};
    const slots = {};

    if (style === 'zigzag') {
      const order = zigzagOrder.filter((s) => equipment[s]);
      let i = place(order.map((s) => equipment[s]), layout, slots, order, 0, true);
      const inv = [];
      for (let n = 1; n <= 28; n++) {
        if (inventory[String(n)]) inv.push(inventory[String(n)]);
      }
      i = place(inv, layout, slots, null, i, true);
      const pouch = [];
      for (let n = 1; n <= 4; n++) {
        if (runes[String(n)]) pouch.push(runes[String(n)]);
      }
      // The plugin lays the rune pouch out linearly, not zigzag.
      place(pouch, layout, slots, null, i, false);
    } else {
      for (const pos of Object.keys(loadoutMap)) {
        const arg = loadoutMap[pos];
        let id;
        if (/^\d+$/.test(arg)) id = inventory[arg];
        else if (arg.indexOf('rune') === 0) id = runes[arg.slice(4)];
        else {
          id = equipment[arg];
          if (id) slots[pos] = arg;
        }
        if (id) layout[pos] = id;
      }
    }
    return { layout: layout, slots: slots, equipment: equipment };
  }

  function importStringFor(entry, layout, icon) {
    const parts = [
      'banktags',
      '1',
      entry.tagName,
      String(icon === undefined ? entry.icon : icon),
      'layout',
    ];
    const positions = Object.keys(layout)
      .map(Number)
      .sort((a, b) => a - b);
    for (const pos of positions) parts.push(String(pos), String(layout[pos]));
    return parts.join(',');
  }

  root.BTL = {
    zigzagIndex: zigzagIndex,
    wornItems: wornItems,
    buildLayout: buildLayout,
    importStringFor: importStringFor,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
