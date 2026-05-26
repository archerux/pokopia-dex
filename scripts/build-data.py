#!/usr/bin/env python3
"""
Rebuild the embedded data inside index.html from sources:
  - "Pokopia Pokemon.xlsx" (Pokémon list — sheet "All Pokemon")
  - data/serebii-favorites.json (cached item lists per favorite category)
  - data/dex-map.json (Pokémon name → national dex number, for sprite URLs)

Usage:
  python scripts/build-data.py
  python scripts/build-data.py --rescrape   # refetch Serebii favorite pages and update cache

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
SEREBII_CACHE = ROOT / "data" / "serebii-favorites.json"
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
        # Pull rows: each item row has 3 <td>s — picture cell, name cell, description cell
        # Extract pairs of (name, description) by matching the second <a>...</a> for the name and the third <td> contents
        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>.*?</td>\s*<td[^>]*>\s*<a[^>]*>([^<]+)</a>\s*</td>\s*<td[^>]*>([^<][^<]*?)</td>\s*</tr>',
            section, re.S | re.I,
        )
        items = []
        seen = set()
        for name, desc in rows:
            name = re.sub(r'\s+', ' ', name).strip()
            desc = re.sub(r'\s+', ' ', desc).strip()
            if name and name not in seen:
                seen.add(name)
                items.append({"name": name, "desc": desc})
        print(f"{len(items)} items")
        out[slug] = {"displayName": display_name, "items": items}
        time.sleep(0.4)  # be polite

    SEREBII_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEREBII_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Wrote {SEREBII_CACHE}")
    return out


def base_name(raw: str) -> str:
    n = re.sub(r"\s*\(.*?\)\s*", "", raw)
    n = re.sub(r"\s*\*.*$", "", n)
    return n.strip()


def display_name(raw: str) -> str:
    return re.sub(r"\s*\*.*$", "", raw).strip()


def build_data(pokemon, fav, dex_map):
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

    dex_map = json.loads(DEX_MAP_PATH.read_text(encoding="utf-8"))
    dex_map.pop("_comment", None)

    print("Loading spreadsheet…")
    pokemon = load_pokemon()
    print(f"  {len(pokemon)} Pokémon rows")

    print("Building combined data…")
    data = build_data(pokemon, fav, dex_map)

    print("Embedding into index.html…")
    embed(data)
    print("Done.")


if __name__ == "__main__":
    main()
