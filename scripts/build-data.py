#!/usr/bin/env python3
"""
Rebuild the embedded data inside index.html from sources:
  - "Pokopia Pokemon.xlsx" (Pokémon list — sheet "All Pokemon")
  - data/serebii-favorites.json (cached item lists per favorite category)
  - data/serebii-litter.json (cached list of Pokémon ↔ litter item mappings)
  - data/dex-map.json (Pokémon name → national dex number, for sprite URLs)

Usage:
  python scripts/build-data.py
  python scripts/build-data.py --rescrape   # refetch Serebii pages (favorites + litter) and update cache

When the game updates:
  1. Update the .xlsx with new Pokémon / changes
  2. If Serebii has new favorite categories or items: run with --rescrape
  3. If new Pokémon aren't in data/dex-map.json: add them (look up the national dex)
  4. Run this script and commit the regenerated index.html
"""

from __future__ import annotations
import argparse, json, os, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "Pokopia Pokemon.xlsx"
DEX_MAP_PATH = ROOT / "data" / "dex-map.json"
SEREBII_CACHE  = ROOT / "data" / "serebii-favorites.json"
LITTER_CACHE   = ROOT / "data" / "serebii-litter.json"
FLAVORS_CACHE  = ROOT / "data" / "serebii-flavors.json"
HTML_PATH = ROOT / "index.html"

# In-place replace strategy: only this <script> block in index.html is rewritten. UI code is
# preserved untouched, so `git diff index.html` after a rebuild only shows the data delta.
DATA_TAG_RE = re.compile(
    r'(<script id="pokopia-data" type="application/json">)(.*?)(</script>)',
    re.S,
)

# Spreadsheet ↔ Serebii naming mismatches. The spreadsheet is the canonical source of truth for
# what each Pokémon likes — don't "fix" the spreadsheet to match Serebii. Normalize here instead.
# (Serebii prefixes "Lots of ..." for some categories; uses US spelling; uses different separators.)
FAV_ALIASES = {
    "dirt": "lotsofdirt",
    "fire": "lotsoffire",
    "nature": "lotsofnature",
    "water": "lotsofwater",
    "colourful stuff": "colorfulstuff",    # UK → US spelling
    "letters/words": "lettersandwords",
    "group activities": "groupactivities",
    "nice breezes": "nicebreezes",          # Sheet sometimes capitalizes "Breezes"
}

# Specialty-column typos found in the spreadsheet. Same principle: fix here, not in the sheet.
SPECIALTY_FIX = {
    "Bulldose": "Bulldoze",
    "Bull": "Bulldoze",
    "Teleportort": "Teleport",
    "Rain?": "Rain",
    "???": "Unknown",
}

# Litter table on Serebii uses different display names for some Pokémon than the spreadsheet.
# Maps Serebii name → spreadsheet displayName so we can match the litter entry to the row.
LITTER_NAME_ALIASES = {
    "Paldean Wooper": "P-Wooper",
}


def load_pokemon():
    try:
        import openpyxl  # type: ignore
    except ImportError:
        sys.exit("openpyxl is required. Install with: pip install openpyxl")
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["All Pokemon"]
    rows = list(ws.iter_rows(values_only=True))
    out = []
    for r in rows[1:]:
        if not r[0] and not r[1]:
            continue
        num, name, ptype, specialty, location, habitat, f1, f2, f3, f4, f5, flavor = r[:12]
        favs = [str(f).strip() for f in (f1, f2, f3, f4, f5)
                if f and str(f).strip().lower() not in ("n/a", "na", "none", "")]
        dex = int(float(num)) if num is not None and isinstance(num, (int, float)) else None
        out.append({
            "dex": dex,
            "num": num,
            "name": (name or "").strip(),
            "type": (ptype or "").strip(),
            "specialty": (specialty or "").strip(),
            "location": (location or "").strip(),
            "habitat": (habitat or "").strip(),
            "favorites_raw": favs,
            "flavor": (flavor or "").strip(),
        })
    return out


def scrape_serebii():
    """Fetch every favorite category page from Serebii. Requires `requests`.

    Result is cached to data/serebii-favorites.json and committed to the repo so casual rebuilds
    (e.g. fixing a spreadsheet typo) don't need network. Only run --rescrape when the game updates
    or Serebii flags new items in their "work in progress" categories.
    """
    try:
        import requests  # type: ignore
        from html.parser import HTMLParser
    except ImportError:
        sys.exit("`requests` is required to rescrape. Install with: pip install requests")

    base = "https://www.serebii.net/pokemonpokopia/favorites"
    # Pull the index to discover slugs
    print(f"Fetching index: {base}.shtml")
    idx_html = requests.get(f"{base}.shtml", timeout=30).text
    slugs = sorted(set(re.findall(r'favorites/([a-z]+)\.shtml', idx_html)))
    print(f"Found {len(slugs)} category slugs.")

    out = {}
    for slug in slugs:
        url = f"{base}/{slug}.shtml"
        print(f"  fetching {slug}...", end=" ", flush=True)
        html = requests.get(url, timeout=30).text
        # The "## List of X Items" table is in an <h2> ... <table> block
        # Just extract item rows by name+description heuristic
        m_h2 = re.search(r'<h2>\s*List of\s+(.*?)\s+Items?\s*</h2>', html, re.I)
        m_end = re.search(r'<h2>\s*List of\s+Pok[eé]mon', html, re.I)
        if not m_h2 or not m_end:
            print(f"missing table — skipping ({slug})")
            out[slug] = {"displayName": slug.title(), "items": []}
            continue
        display_name = m_h2.group(1).strip()
        section = html[m_h2.end():m_end.start()]
        # Pull rows: each row has 3 <td>s — picture cell, name cell, description cell.
        # The name cell contains <a href="…/items/<itemslug>.shtml">Name</a>. We capture the slug
        # from the href (authoritative — used to build the Serebii item-image URL at runtime) and
        # the visible name + description text. Deriving slug from name is wrong about 16% of the
        # time (Pokémetal → pokemetal not pokmetal; parens are preserved; etc.), so always pull it
        # from the URL.
        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>.*?</td>\s*<td[^>]*>\s*<a[^>]*href="[^"]*/items/([^"\.]+)\.shtml"[^>]*>([^<]+)</a>\s*</td>\s*<td[^>]*>([^<][^<]*?)</td>\s*</tr>',
            section, re.S | re.I,
        )
        items = []
        seen = set()
        for item_slug, name, desc in rows:
            item_slug = item_slug.strip()
            name = re.sub(r'\s+', ' ', name).strip()
            desc = re.sub(r'\s+', ' ', desc).strip()
            if item_slug and item_slug not in seen:
                seen.add(item_slug)
                items.append({"slug": item_slug, "name": name, "desc": desc})
        print(f"{len(items)} items")
        out[slug] = {"displayName": display_name, "items": items}
        time.sleep(0.4)  # be polite

    SEREBII_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEREBII_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Wrote {SEREBII_CACHE}")
    return out


def scrape_litter(fav_cache: dict | None = None):
    """Scrape https://www.serebii.net/pokemonpokopia/litter.shtml.

    Each row in Serebii's "List of Litter" table maps one Pokémon to one item it drops.
    Raw HTML format (post-table-header):
      <tr>
        <td>#003</td>
        <td>...<a href=".../pokedex/venusaur.shtml"><img alt="Venusaur Image"></a></td>
        <td>...<a href=".../pokedex/venusaur.shtml"><u>Venusaur</u></a></td>
        <td>...specialty mini-table...</td>
        <td><img src="items/leaf.png" alt="Leaf" /><br />Leaf</td>
      </tr>

    The item cell has NO anchor — the authoritative slug comes from the image filename
    `items/<slug>.png` (matches our ITEM_IMG() runtime hotlink). The name link wraps the text
    in <u> tags, which an earlier version of this regex missed.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        sys.exit("`requests` is required to rescrape. Install with: pip install requests")

    url = "https://www.serebii.net/pokemonpokopia/litter.shtml"
    print(f"Fetching {url}")
    html = requests.get(url, timeout=30).text

    # Each outer <tr> contains a nested specialty mini-table (<table align="center">…</table>) with
    # its own <tr>s. Without stripping it, the non-greedy outer-<tr> regex closes on the inner
    # </tr> and we never reach the item cell. The outer "List of Litter" table uses class="tab",
    # so the inner ones are uniquely identifiable as <table align="center"> without that class.
    html_flat = re.sub(r'<table align="center"[^>]*>.*?</table>', ' ', html, flags=re.S | re.I)

    entries = []
    seen = set()  # de-dupe rows in case Serebii ever lists the same pair twice
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_flat, re.S | re.I)
    for row in rows:
        # Pokémon: <a href="/pokemonpokopia/pokedex/<slug>.shtml"><u>Name</u></a>
        # The first link in the row is the image link; the second is the name (with <u>).
        # We want the one that wraps text, so prefer the <u>-wrapped capture.
        m_p = re.search(
            r'href="[^"]*pokemonpokopia/pokedex/[a-z]+\.shtml"[^>]*>\s*<u>([^<]+)</u>',
            row, re.I,
        )
        if not m_p:
            continue
        pokemon_name = re.sub(r'\s+', ' ', m_p.group(1)).strip()

        # Item: <img src="items/<slug>.png" ... alt="<Item Name>" ... />
        # Slug from the image filename is authoritative (matches the slugs used elsewhere on the site).
        m_i = re.search(
            r'<img\s+src="items/([^"\.]+)\.png"[^>]*alt="([^"]+)"',
            row, re.I,
        )
        if not m_i:
            continue
        slug = m_i.group(1).strip().lower()
        item_name = re.sub(r'\s+', ' ', m_i.group(2)).strip()

        key = (pokemon_name, slug)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"pokemon": pokemon_name, "item": item_name, "slug": slug})

    LITTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(LITTER_CACHE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"Wrote {LITTER_CACHE} ({len(entries)} entries)")
    return entries


def scrape_flavors():
    """Scrape https://www.serebii.net/pokemonpokopia/flavors.shtml.

    Page layout: one big <table class="dextable"> with section-header rows that contain
    <a name="<flavorSlug>"></a><h3>FlavorName</h3>, followed by item rows of the form
        <tr><td class="cen"><img src="items/<slug>.png" alt="<Name>"></td>
            <td class="cen">Name</td>
            <td class="fooinfo">Description</td></tr>

    The "No Flavor" bucket is treated as its own flavor named 'none' — useful for showing
    universal foods on the detail drawer too if we ever want.
    """
    try:
        import requests  # type: ignore
        import html as html_mod
    except ImportError:
        sys.exit("`requests` is required to rescrape. Install with: pip install requests")

    url = "https://www.serebii.net/pokemonpokopia/flavors.shtml"
    print(f"Fetching {url}")
    page = requests.get(url, timeout=30).text

    # Narrow to the food table to avoid matching nav-img rows above.
    start = page.lower().find('<h2>list of food')
    if start < 0:
        sys.exit("Could not find 'List of Food' header on flavors page — page structure changed?")
    section = page[start:]
    # Stop at the next <h2> or end-of-page.
    end_h2 = re.search(r'<h2[^>]*>', section[100:], re.I)
    if end_h2:
        section = section[: 100 + end_h2.start()]

    flavors = {}              # slug → {slug, displayName, items}
    current = None            # current flavor slug while walking rows

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S | re.I)
    for row in rows:
        # Section header row carries the anchor name + an <h3>.
        m_section = re.search(
            r'<a\s+name="([a-z]+)"[^>]*>\s*</a>\s*<h3[^>]*>([^<]+)',
            row, re.I,
        )
        if m_section:
            slug = m_section.group(1).strip().lower()
            # "general" is Serebii's anchor for the "No Flavor" bucket — store as 'none' so
            # the spreadsheet's empty-flavor case can fall back to this if we want it later.
            if slug == "general":
                slug = "none"
            name = html_mod.unescape(m_section.group(2).strip())
            current = slug
            flavors.setdefault(slug, {"slug": slug, "displayName": name, "items": []})
            continue

        if not current:
            continue

        m_item = re.search(
            r'<img\s+src="items/([^"\.]+)\.png"[^>]*alt="([^"]+)"',
            row, re.I,
        )
        if not m_item:
            continue
        item_slug = m_item.group(1).strip().lower()
        item_name = html_mod.unescape(m_item.group(2).strip())
        # Description is in the LAST <td>; strip tags, collapse whitespace, unescape entities.
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)
        desc = ""
        if tds:
            desc = re.sub(r'<[^>]+>', ' ', tds[-1])
            desc = html_mod.unescape(re.sub(r'\s+', ' ', desc)).strip()
            # The last <td> sometimes also includes the name cell content when the row uses
            # fewer cells; if desc starts with the item name verbatim, trim it.
            if desc.startswith(item_name):
                desc = desc[len(item_name):].lstrip(' .')

        # Avoid dupes (Rare Candy appears once with an anchor and once without in the raw HTML).
        if any(it["slug"] == item_slug for it in flavors[current]["items"]):
            continue
        flavors[current]["items"].append({"slug": item_slug, "name": item_name, "desc": desc})

    FLAVORS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(FLAVORS_CACHE, "w", encoding="utf-8") as f:
        json.dump(flavors, f, indent=2, ensure_ascii=False)
    n_items = sum(len(d["items"]) for d in flavors.values())
    print(f"Wrote {FLAVORS_CACHE} ({len(flavors)} flavor sections, {n_items} food items)")
    return flavors


def base_name(raw: str) -> str:
    n = re.sub(r"\s*\(.*?\)\s*", "", raw)
    n = re.sub(r"\s*\*.*$", "", n)
    return n.strip()


def display_name(raw: str) -> str:
    return re.sub(r"\s*\*.*$", "", raw).strip()


def build_data(pokemon, fav, litter_entries, flavors, dex_map):
    # Clean fav values
    for slug, d in fav.items():
        d["displayName"] = d["displayName"].strip()
        for it in d["items"]:
            it["name"] = it["name"].strip()
            it["desc"] = it["desc"].strip()

    name_to_slug = {d["displayName"].lower(): slug for slug, d in fav.items()}
    name_to_slug.update(FAV_ALIASES)

    unresolved = set()
    missing_dex = set()
    for p in pokemon:
        resolved = []
        for f in p.pop("favorites_raw"):
            slug = name_to_slug.get(f.lower())
            if slug:
                resolved.append({"slug": slug, "name": fav[slug]["displayName"]})
            else:
                unresolved.add(f)
                resolved.append({"slug": None, "name": f})
        p["favorites"] = resolved

        specs = [s.strip() for s in re.split(r"[,/]", p["specialty"]) if s.strip()]
        specs = [SPECIALTY_FIX.get(s, s) for s in specs]
        seen = set()
        p["specialties"] = [s for s in specs if not (s in seen or seen.add(s))]
        p["types"] = [t.strip() for t in p["type"].split("/") if t.strip()]
        if p["flavor"].lower() in ("n/a", "na", "none", ""):
            p["flavor"] = ""
        if p["habitat"].lower() in ("n/a", "na", "none", ""):
            p["habitat"] = ""

        # "P-Wooper" is Paldean Wooper. The public PokéAPI sprite set we use doesn't have a
        # separate sprite for the Paldean form at a stable ID, so fall back to the Johto Wooper
        # sprite. If/when we want true form sprites, swap to Pokémon Home artwork URLs.
        bn = base_name(p["name"])
        if bn == "P-Wooper":
            bn = "Wooper"
        p["displayName"] = display_name(p["name"])
        nat = dex_map.get(bn)
        if not nat:
            missing_dex.add(bn)
        p["natDex"] = nat
        p["_search"] = (p["displayName"] + " " + p["type"] + " "
                        + " ".join(p["specialties"]) + " " + p["habitat"]).lower()
        for k in ("specialty", "type", "num"):
            p.pop(k, None)

    if unresolved:
        print(f"  warning: {len(unresolved)} unresolved favorite name(s): {sorted(unresolved)}")
    if missing_dex:
        print(f"  warning: {len(missing_dex)} Pokémon missing from dex-map.json: {sorted(missing_dex)}")
        print("  → add them to data/dex-map.json so sprites render.")

    # Reverse index
    liked_by = {slug: [] for slug in fav}
    for p in pokemon:
        for f in p["favorites"]:
            if f["slug"]:
                liked_by[f["slug"]].append({"dex": p["dex"], "name": p["displayName"]})

    # Merge litter data onto each Pokémon and build the inverse `litter` index (item slug → who drops it).
    # The litter table on Serebii is one row per (Pokémon, item) pair; a Pokémon can in principle
    # drop more than one item, so we store p["litter"] as a list. Empty list = doesn't litter anything.
    litter_by_canonical = {}
    for e in litter_entries:
        canonical = LITTER_NAME_ALIASES.get(e["pokemon"], e["pokemon"])
        litter_by_canonical.setdefault(canonical, []).append(e)
    unmatched_litter_names = set(litter_by_canonical.keys())
    litter_items = {}  # slug → {slug, name, pokemon: [{dex, name, natDex}]}
    for p in pokemon:
        matches = litter_by_canonical.get(p["displayName"], [])
        p["litter"] = [{"slug": m["slug"], "name": m["item"]} for m in matches]
        if matches:
            unmatched_litter_names.discard(p["displayName"])
        for m in matches:
            entry = litter_items.setdefault(m["slug"], {
                "slug": m["slug"], "name": m["item"], "pokemon": [],
            })
            entry["pokemon"].append({
                "dex": p["dex"], "name": p["displayName"], "natDex": p["natDex"],
            })
    if unmatched_litter_names:
        print(f"  warning: {len(unmatched_litter_names)} litter Pokémon not matched to spreadsheet rows: "
              f"{sorted(unmatched_litter_names)}")
        print("  → add an entry to LITTER_NAME_ALIASES if the spreadsheet uses a different name.")

    pokemon.sort(key=lambda p: (p["dex"] or 0, p.get("displayName", "")))

    return {
        "pokemon": pokemon,
        "favorites": {
            slug: {
                "slug": slug,
                "displayName": fav[slug]["displayName"],
                "items": fav[slug]["items"],
                "likedBy": liked_by[slug],
            }
            for slug in fav
        },
        "litter": litter_items,
        # flavors keyed by lowercase slug ('sweet','bitter','dry','sour','spicy','none').
        # The UI looks this up by lowercasing the spreadsheet's p.flavor.
        "flavors": flavors,
    }


def embed(data: dict):
    html = HTML_PATH.read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    # Per the HTML5 spec, the only token that closes a <script> block is "</script". We escape
    # every "</" inside the JSON to "<\/" defensively — JSON.parse treats \/ as /, so it's a no-op
    # at parse time but bulletproof against future content (e.g. item descriptions with HTML).
    safe = blob.replace("</", "<\\/")
    if not DATA_TAG_RE.search(html):
        sys.exit('Could not find <script id="pokopia-data"> tag in index.html')
    new_html = DATA_TAG_RE.sub(lambda m: m.group(1) + safe + m.group(3), html)
    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"Wrote {HTML_PATH} ({len(new_html):,} bytes)")
    print(f"  Pokémon: {len(data['pokemon'])}")
    print(f"  Favorite categories: {len(data['favorites'])}")
    print(f"  Catalogued items: {sum(len(f['items']) for f in data['favorites'].values())}")
    print(f"  Litter items: {len(data['litter'])} "
          f"(dropped by {sum(len(li['pokemon']) for li in data['litter'].values())} Pokémon entries)")
    print(f"  Flavor sections: {len(data['flavors'])} "
          f"({sum(len(f['items']) for f in data['flavors'].values())} foods)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rescrape", action="store_true",
                        help="Refetch Serebii favorite pages and update data/serebii-favorites.json")
    args = parser.parse_args()

    if not XLSX.exists():
        sys.exit(f"Missing {XLSX}")

    if args.rescrape or not SEREBII_CACHE.exists():
        fav = scrape_serebii()
    else:
        fav = json.loads(SEREBII_CACHE.read_text(encoding="utf-8"))
        print(f"Using cached Serebii data ({len(fav)} categories). Pass --rescrape to refetch.")

    # Litter cache is scraped on --rescrape, or whenever the cache is missing/empty (so blanking
    # the file is a quick way to force a single-source refresh without touching the favorites cache).
    if args.rescrape or not LITTER_CACHE.exists():
        litter_entries = scrape_litter(fav)
    else:
        litter_entries = json.loads(LITTER_CACHE.read_text(encoding="utf-8"))
        if not litter_entries:
            print("Cached litter data is empty — rescraping.")
            litter_entries = scrape_litter(fav)
        else:
            print(f"Using cached litter data ({len(litter_entries)} entries). Pass --rescrape to refetch.")

    # Flavors cache uses the same "blank to refresh" idiom as litter.
    if args.rescrape or not FLAVORS_CACHE.exists():
        flavors = scrape_flavors()
    else:
        flavors = json.loads(FLAVORS_CACHE.read_text(encoding="utf-8"))
        if not flavors:
            print("Cached flavors data is empty — rescraping.")
            flavors = scrape_flavors()
        else:
            print(f"Using cached flavors data ({len(flavors)} sections). Pass --rescrape to refetch.")

    dex_map = json.loads(DEX_MAP_PATH.read_text(encoding="utf-8"))
    dex_map.pop("_comment", None)

    print("Loading spreadsheet…")
    pokemon = load_pokemon()
    print(f"  {len(pokemon)} Pokémon rows")

    print("Building combined data…")
    data = build_data(pokemon, fav, litter_entries, flavors, dex_map)

    print("Embedding into index.html…")
    embed(data)
    print("Done.")


if __name__ == "__main__":
    main()
