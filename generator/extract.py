"""Stage 2 - extract: turn strategy wikitext into structured gear setups.

Pages encode setups two different ways, and both are common enough that
guessing is not acceptable:

* **Tabber** (Doom, Nex, Zulrah) - one `<tabber>` chunk per playstyle, each
  holding `{{Equipment}}` then `{{Inventory}}` then optional `{{Rune pouch}}`.
* **Wikitable** (Chambers of Xeric) - a table whose `!` header cells name the
  variants, with a row of five `{{Equipment}}` blocks followed by a row of five
  `{{Inventory}}` blocks.

Pairing therefore works per *scope* (a tabber chunk, or a heading section).
Within a scope, if the equipment and inventory counts match they are zipped in
document order - which is correct for interleaved and blocked layouts alike.
When the counts disagree we fall back to adjacency and report what went
unclaimed, so a stray illustrative block (Nex's transportation gear) is dropped
loudly rather than silently welded onto the wrong setup.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
CORPUS = REPO / "generator" / "corpus.json"
SETUPS = REPO / "generator" / "setups.json"

TEMPLATES = ("Equipment", "Inventory", "Rune pouch")

# Equipment slots Module:Loadout understands. `*quantity` args are display-only.
EQUIPMENT_SLOTS = (
    "head", "cape", "neck", "ammo", "ammo2", "weapon",
    "torso", "legs", "shield", "gloves", "boots", "ring",
)

HEADING_RE = re.compile(r"(?m)^(=+)\s*(.+?)\s*\1\s*$")
TAB_LABEL_RE = re.compile(r"(?m)^([^=|{}\n]{1,60}?)\s*=\s*$")
TABLE_HEADER_RE = re.compile(r"(?m)^!(.+)$")

# A section whose heading names a loadout ("Example melee setup", "Equipment")
# is publishing gear on purpose. One that does not ("Transportation") is
# illustrating something else and must not become a bank tag.
SETUP_HEADING_RE = re.compile(r"setup|gear|equipment|loadout|inventory", re.IGNORECASE)


def clean_item(value: str) -> str:
    """Mirror Module:Loadout._cleanItem / Module:Inventory value parsing.

    Values look like ``ItemName``, ``[[Item Name]]``, ``Item;link`` or
    ``Item\\5`` (quantity). Brackets are stripped and the name is everything
    before the first ``;`` or ``\\``.
    """
    if value is None:
        return ""
    v = value.replace("[", "").replace("]", "").strip()
    v = re.split(r"[;\\]", v, maxsplit=1)[0].strip()
    if not v:
        return ""
    m = re.fullmatch(r"\{\{\s*[Pp]link[^|]*\|([^|}]+).*\}\}", v)
    if m:
        v = m.group(1).strip()
    v = re.sub(r"<[^>]+>", "", v).strip()
    v = v.strip("'\" ")
    if not v:
        return ""
    return v[0].upper() + v[1:]


def split_args(body: str) -> list[str]:
    """Split a template body on top-level ``|`` only."""
    parts: list[str] = []
    buf: list[str] = []
    brace = bracket = 0
    i = 0
    while i < len(body):
        two = body[i : i + 2]
        if two in ("{{", "[["):
            if two == "{{":
                brace += 1
            else:
                bracket += 1
            buf.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            if two == "}}":
                brace -= 1
            else:
                bracket -= 1
            buf.append(two)
            i += 2
            continue
        ch = body[i]
        if ch == "|" and brace == 0 and bracket == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def parse_template(text: str, start: int) -> tuple[str, dict[str, str], int] | None:
    """Parse the template beginning at ``start`` (index of ``{{``).

    Positional arguments get their own counter, exactly as MediaWiki does:
    ``{{Inventory|align=right|Shark|Lobster}}`` makes Shark argument ``1``,
    not ``2``.
    """
    depth = 0
    i = start
    while i < len(text):
        if text[i : i + 2] == "{{":
            depth += 1
            i += 2
            continue
        if text[i : i + 2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    if depth != 0:
        return None

    inner = text[start + 2 : i - 2]
    chunks = split_args(inner)
    name = chunks[0].strip()

    args: dict[str, str] = {}
    positional = 0
    for chunk in chunks[1:]:
        key, sep, value = chunk.partition("=")
        # A "=" only makes it named when the key is a plain identifier.
        if sep and re.fullmatch(r"[A-Za-z0-9 _-]{1,30}", key.strip()):
            args[key.strip().lower()] = value.strip()
        else:
            positional += 1
            args[str(positional)] = chunk.strip()
    return name, args, i


@dataclass
class Occurrence:
    name: str
    args: dict[str, str]
    start: int
    end: int


@dataclass
class Scope:
    start: int
    end: int
    label: str
    kind: str  # "tab" | "section"


@dataclass
class Setup:
    variant: str
    equipment: dict[str, str] = field(default_factory=dict)
    inventory: dict[str, str] = field(default_factory=dict)
    runes: dict[str, str] = field(default_factory=dict)


def find_occurrences(text: str) -> list[Occurrence]:
    out: list[Occurrence] = []
    pattern = r"\{\{\s*(" + "|".join(t.replace(" ", r"\s") for t in TEMPLATES) + r")\b"
    for m in re.finditer(pattern, text, re.IGNORECASE):
        parsed = parse_template(text, m.start())
        if not parsed:
            continue
        name, args, end = parsed
        canonical = re.sub(r"\s+", " ", name.strip().lower())
        for t in TEMPLATES:
            if canonical == t.lower():
                out.append(Occurrence(t, args, m.start(), end))
                break
    out.sort(key=lambda o: o.start)
    return out


def build_scopes(text: str) -> list[Scope]:
    """Tabber chunks take priority; everything else falls back to sections."""
    scopes: list[Scope] = []

    for block in re.finditer(r"<tabber>(.*?)</tabber>", text, re.DOTALL | re.IGNORECASE):
        body, base = block.group(1), block.start(1)
        bounds = [0]
        for sep in re.finditer(r"(?m)^\|-\|\s*$", body):
            bounds.append(sep.start())
            bounds.append(sep.end())
        bounds.append(len(body))
        for a, b in zip(bounds[0::2], bounds[1::2]):
            chunk = body[a:b]
            label_match = TAB_LABEL_RE.search(chunk)
            label = label_match.group(1).strip() if label_match else ""
            scopes.append(Scope(base + a, base + b, label, "tab"))

    headings = list(HEADING_RE.finditer(text))
    for idx, m in enumerate(headings):
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        scopes.append(Scope(m.end(), end, m.group(2).strip(), "section"))
    if not headings:
        scopes.append(Scope(0, len(text), "Setup", "section"))

    return scopes


def scope_for(pos: int, scopes: list[Scope]) -> Scope | None:
    """Innermost scope containing `pos`; tabs win over sections."""
    best: Scope | None = None
    for s in scopes:
        if s.start <= pos < s.end:
            if best is None:
                best = s
            elif s.kind == "tab" and best.kind == "section":
                best = s
            elif s.kind == best.kind and (s.end - s.start) < (best.end - best.start):
                best = s
    return best


def table_headers(text: str, scope: Scope, count: int) -> list[str] | None:
    """Header cells of a wikitable inside `scope`, if they match `count`."""
    region = text[scope.start : scope.end]
    for table in re.finditer(r"^\{\|(.*?)^\|\}", region, re.DOTALL | re.MULTILINE):
        headers: list[str] = []
        for m in TABLE_HEADER_RE.finditer(table.group(1)):
            for cell in m.group(1).split("!!"):
                cell = re.sub(r"\[\[([^|\]]*\|)?([^\]]+)\]\]", r"\2", cell)
                cell = re.sub(r"^[^|]*\|(?!\|)", "", cell) if "|" in cell else cell
                cell = re.sub(r"<[^>]+>", "", cell).strip().strip("'")
                if cell:
                    headers.append(cell)
        if len(headers) == count:
            return headers
    return None


def _slots(occ: Occurrence | None, keys, limit: int | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if occ is None:
        return out
    for key in keys:
        item = clean_item(occ.args.get(key, ""))
        if item:
            out[key] = item
    return out


def extract_page(title: str, text: str) -> tuple[list[Setup], list[str]]:
    occurrences = find_occurrences(text)
    scopes = build_scopes(text)
    warnings: list[str] = []
    setups: list[Setup] = []

    # Group occurrences by the scope that contains them.
    grouped: dict[int, list[Occurrence]] = {}
    scope_by_id: dict[int, Scope] = {}
    for occ in occurrences:
        s = scope_for(occ.start, scopes)
        if s is None:
            continue
        key = id(s)
        grouped.setdefault(key, []).append(occ)
        scope_by_id[key] = s

    for key, occs in grouped.items():
        scope = scope_by_id[key]
        equips = [o for o in occs if o.name == "Equipment"]
        invs = [o for o in occs if o.name == "Inventory"]
        pouches = [o for o in occs if o.name == "Rune pouch"]

        pairs: list[tuple[Occurrence | None, Occurrence | None]] = []

        if not invs:
            # Gear-only loadouts are real and useful, but only when the page
            # deliberately tabbed them as a playstyle. A bare {{Equipment}} in a
            # prose section (Nex's transportation gear) is illustrative, not a
            # setup, so it is dropped loudly instead of published.
            if not equips:
                continue
            if scope.kind != "tab" and not SETUP_HEADING_RE.search(scope.label):
                warnings.append(
                    f"{len(equips)} unpaired {{{{Equipment}}}} in '{scope.label}' ignored "
                    f"(no inventory in scope)"
                )
                continue
            pairs = [(e, None) for e in equips]
        elif len(equips) == len(invs):
            pairs = list(zip(equips, invs))
        else:
            # Counts disagree: pair by adjacency, report the leftovers.
            claimed: set[int] = set()
            for inv in invs:
                match = None
                for e in reversed([e for e in equips if e.end <= inv.start]):
                    if e.start not in claimed:
                        match = e
                        break
                if match:
                    claimed.add(match.start)
                pairs.append((match, inv))
            leftover = len(equips) - len(claimed)
            if leftover > 0:
                warnings.append(
                    f"{leftover} unpaired {{{{Equipment}}}} in '{scope.label}' ignored "
                    f"({len(equips)} equipment vs {len(invs)} inventory)"
                )

        headers = table_headers(text, scope, len(pairs)) if len(pairs) > 1 else None

        for idx, (eq, inv) in enumerate(pairs):
            if headers:
                variant = headers[idx]
            elif scope.kind == "tab" and scope.label:
                variant = scope.label
            else:
                variant = scope.label or "Setup"

            # The rune pouch belongs to this setup if it sits after this
            # setup's last block and before the next setup starts.
            anchor_end = inv.end if inv is not None else (eq.end if eq else 0)
            next_start = len(text)
            if idx + 1 < len(pairs):
                nxt_eq, nxt_inv = pairs[idx + 1]
                candidates = [o.start for o in (nxt_eq, nxt_inv) if o is not None]
                if candidates:
                    next_start = min(candidates)

            pouch = None
            for p in pouches:
                if anchor_end < p.start < next_start:
                    pouch = p
                    break

            setup = Setup(variant=variant)
            setup.equipment = _slots(eq, EQUIPMENT_SLOTS)
            setup.inventory = _slots(inv, [str(n) for n in range(1, 29)])
            setup.runes = _slots(pouch, [str(n) for n in range(1, 5)])

            if not setup.inventory and not setup.equipment:
                continue
            setups.append(setup)

    setups.sort(key=lambda s: s.variant)

    # Disambiguate repeated labels so every variant is addressable.
    seen: dict[str, int] = {}
    for s in setups:
        seen[s.variant] = seen.get(s.variant, 0) + 1
        if seen[s.variant] > 1:
            s.variant = f"{s.variant} ({seen[s.variant]})"

    return setups, warnings


def main() -> None:
    client = WikiClient()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    pages_out = []
    total_setups = 0
    empty_pages: list[str] = []

    for title in corpus["pages"]:
        text, rev_id = client.page_wikitext(title)
        setups, warnings = extract_page(title, text)
        total_setups += len(setups)
        if not setups:
            empty_pages.append(title)
        pages_out.append(
            {
                "page": title,
                "revId": rev_id,
                "warnings": warnings,
                "setups": [
                    {
                        "variant": s.variant,
                        "equipment": s.equipment,
                        "inventory": s.inventory,
                        "runes": s.runes,
                    }
                    for s in setups
                ],
            }
        )

    SETUPS.write_text(
        json.dumps({"pages": pages_out, "emptyPages": empty_pages}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"extract: {len(pages_out)} pages -> {total_setups} setups")
    print(f"  {len(empty_pages)} pages yielded none")
    print(f"  http={client.stats['http']} cache={client.stats['cache']}")
    print(f"  wrote {SETUPS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
