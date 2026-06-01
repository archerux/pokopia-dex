# Claude bootstrap notes — Pokopia Dex

This file is for Claude. It captures the project's purpose, architecture, decisions, and conventions so a fresh session can pick up without re-deriving the context.

## What this is

A mobile-first webapp companion guide for the Nintendo Switch 2 game **Pokémon Pokopia**. The user's primary problem: look up a Pokémon and immediately see what habitat and items would make it happy. Secondary problem (partly addressed, not fully): go the other direction — item → which Pokémon like it.

Owner: Lawrence (`ngo.lawrence@gmail.com`). Personal project, lives at `~/Projects/dev/personal/pokopia-dex/`. Public GitHub repo, deployed to Cloudflare Pages with auto-deploy from `main`.

Live: https://pokopia-dex.pages.dev/

## Hard architectural constraints

These were chosen deliberately. Don't change them without asking Lawrence first.

- **Single-file static HTML.** `index.html` contains the UI code, embedded data (~380 KB JSON), and inline CSS. No build step at the deploy boundary — Cloudflare Pages serves the file as-is. This unlocks zero-friction hosting and trivial local dev (`open index.html`).
- **Vanilla JS, no framework.** No React/Vue/Svelte. Tailwind is loaded from CDN (`cdn.tailwindcss.com`) — accepts the prod warning in exchange for no build pipeline.
- **No backend, no server-side anything.** All data is baked in at build time. Sprites are the only runtime network dependency.
- **No browser-storage requirement.** `sessionStorage` is used for filter persistence but wrapped in try/catch so the app works even when storage is denied (e.g. file:// in some browsers).

If a future feature genuinely needs a framework or build step (e.g. complex state, code-splitting, SSR), surface that trade-off explicitly and let Lawrence decide before changing direction.

## Project layout

```
.
├── index.html                  Deployable app — UI code + embedded data blob
├── Pokopia Pokemon.xlsx        Source spreadsheet (sheet "All Pokemon")
├── README.md                   Public-facing project overview
├── LICENSE                     MIT + attribution notes
├── CLAUDE.md                   ← this file
├── .gitignore
├── assets/
│   └── hero-img.jpg            Home-screen hero banner (saved from pokopia.pokemon.com — self-hosted so the banner isn't dependent on their CDN)
├── data/
│   ├── serebii-favorites.json  Cached Serebii scrape (43 favorite categories)
│   ├── serebii-litter.json     Cached Serebii scrape (Pokémon ↔ litter-item mappings)
│   ├── serebii-flavors.json    Cached Serebii scrape (5 flavors + "No Flavor" → food items)
│   └── dex-map.json            Pokémon name → national dex number (for sprites)
└── scripts/
    └── build-data.py           Regenerates the embedded data inside index.html
```

## Data pipeline

`index.html` carries one embedded `<script id="pokopia-data" type="application/json">…</script>` block. `scripts/build-data.py` rewrites just that block from five inputs:

1. **`Pokopia Pokemon.xlsx`** — `# / Name / Type / Specialty / Location / Ideal Habitat / Favourite 1-5 / Flavor`. ~311 rows including form variants (e.g. Toxtricity x2) and event Pokémon (E1–E4).
2. **`data/serebii-favorites.json`** — cached scrape of 43 pages under `https://www.serebii.net/pokemonpokopia/favorites/<slug>.shtml`. Each page lists qualifying items for one favorite category.
3. **`data/serebii-litter.json`** — cached scrape of `https://www.serebii.net/pokemonpokopia/litter.shtml`. Single page; each row pairs one Pokémon (with the Litter specialty) to one item it drops. Item slugs come from the `<img src="items/<slug>.png">` filename — authoritative because Serebii's item-cell has no anchor. The build script also builds a reverse index `data.litter` so the Items tab can list "who drops Honey" in one lookup. Name mismatches between Serebii and the spreadsheet are normalized via `LITTER_NAME_ALIASES` (e.g. Serebii's "Paldean Wooper" → spreadsheet's "P-Wooper").
4. **`data/serebii-flavors.json`** — cached scrape of `https://www.serebii.net/pokemonpokopia/flavors.shtml`. Single page divided into 6 sections (Bitter/Dry/Sour/Spicy/Sweet/No Flavor) with the foods that match each flavor. Same image-filename-as-slug trick as litter. Keyed by lowercase flavor slug; Serebii's "general" anchor is stored as `none` so the spreadsheet's "Neutral" flavor (and empty-string case) can be folded to it via `flavorSlug()` in the UI.
5. **`data/dex-map.json`** — hand-maintained map from Pokémon name → national-dex number, needed to build PokéAPI sprite URLs.

Run:

```sh
python scripts/build-data.py              # rebuild from cache (fast)
python scripts/build-data.py --rescrape   # refetch Serebii (when game updates)
```

The script does an **in-place** edit on `index.html`, preserving all UI code. After running, `git diff index.html` will only show changes to the JSON blob.

### Why a static dex-map JSON instead of PokéAPI lookup

During build I tried `pokeapi.co/api/v2/pokemon?limit=…` but the agent's `web_fetch` enforces a strict URL-provenance allowlist that blocked it. The dex-map was filled in from training knowledge for the 304 unique base names appearing in the spreadsheet. If the game adds Pokémon not in the map, `build-data.py` prints a warning listing missing names and leaves their `natDex` as `null` (sprite won't load, everything else still works). Add to `data/dex-map.json` and re-run.

### Naming-mismatch normalizations baked into the script

The spreadsheet and Serebii disagree on some category names. These are codified in `FAV_ALIASES` and `SPECIALTY_FIX` in `scripts/build-data.py`. Don't "fix" the spreadsheet to match Serebii — keep the spreadsheet as the canonical input and let the script normalize.

| Spreadsheet term      | Serebii slug      | Notes                              |
| --------------------- | ----------------- | ---------------------------------- |
| `Dirt`                | `lotsofdirt`      | Serebii prefixes "Lots of …"       |
| `Fire`                | `lotsoffire`      |                                    |
| `Nature`              | `lotsofnature`    |                                    |
| `Water`               | `lotsofwater`     |                                    |
| `Colourful stuff`     | `colorfulstuff`   | UK vs US spelling                  |
| `Letters/words`       | `lettersandwords` |                                    |
| `Group activities`    | `groupactivities` |                                    |
| `Nice Breezes` / `Nice breezes` | `nicebreezes` | case-variants in the sheet |

Specialty typos in the source: `Bulldose`/`Bull` → `Bulldoze`; `Teleportort` → `Teleport`; `Rain?` → `Rain`; `???` → `Unknown`. `P-Wooper` (Paldean Wooper) is mapped to the standard `Wooper` sprite ID — there's no separate sprite for the Paldean form in the public PokéAPI sprite set we use.

## UI architecture (inside `index.html`)

- **Routing:** hash-based (`#/pokemon`, `#/pokemon/142:Aerodactyl`, `#/favorite/fabric`, `#/habitats`, `#/about`). Hash routing is deliberate so the file works equally well opened from disk (`file://`) and deployed under a path on Pages — no server-side rewrite rules needed.
- **State:** a single `state` object (search, filters, etc.) persisted to `sessionStorage` via `saveState()` with try/catch. Filter state survives within a tab but not across tabs — intentional.
- **Rendering:** a tiny `el(tag, attrs, …children)` helper creates DOM nodes directly. No `innerHTML`, no template strings of HTML. This is XSS-safe by construction (descriptions from Serebii are inserted as text nodes) and avoids the weight of a framework. Each route returns a single root DOM node; `render()` swaps it in via `app.replaceChildren(view)`.
- **Identity for Pokémon:** `pokeKey(p)` = `"<natDex>:<displayName>"`. Necessary because forms share dex numbers (Toxtricity Amped vs Low Key) and event Pokémon have no dex (`null:Sableye`). Routing uses this composite key.
- **Sprites:** `SPRITE_PIXEL` (small pixel art, used in lists) and `SPRITE_ART` (official artwork, used on detail hero). Both from the PokéAPI sprite GitHub mirror. On any image error, fall back to the pixel sprite and finally hide.
- **Item images:** `ITEM_IMG(slug)` hotlinks from Serebii at `https://www.serebii.net/pokemonpokopia/items/<slug>.png`. Slug is captured at scrape time from Serebii's own item URLs (NOT derived from item name — derivation is wrong ~16% of the time: `Pokémetal` → `pokemetal` not `pokmetal`, parens are kept literally, etc.). On image error the thumbnail slot is hidden so the row falls back to text-only with no broken-image icon. If hotlinking ever gets blocked by Serebii, the contingency is to mirror images into `data/item-images/` at build time and self-host.
- **Type & habitat colors:** pre-defined CSS classes (`.type-Fire`, `.hab-Warm`) in the `<style>` block, not via Tailwind utilities — Tailwind doesn't ship Pokémon-type colors and we don't want a custom build.
- **Search index:** each Pokémon has a precomputed `_search` lowercased string of name+type+specialties+habitat, built once during data build, so the search filter is a fast multi-token `includes` check.

## Deploy

Cloudflare Pages → connected to the GitHub repo. Build config:
- Framework preset: **None**
- Build command: _(empty)_
- Build output directory: `/`

Every push to `main` auto-deploys. Custom domain can be added later under project → Custom domains.

When iterating: edit `index.html` (or the data sources + re-run `build-data.py`), commit, push, deploy completes in ~30 seconds.

## What's done in v1

Primary lookup flow is complete: list Pokémon → see specialty/habitat/location/flavor/favorites/litter → tap a favorite category → see qualifying items + which other Pokémon like that category. On Pokémon detail pages, the "Favorite flavor" row expands an inline drawer with all foods of that flavor (with images). Items tab has three sections: Litter Drops (item → who drops it), Flavors (flavor → matching foods), and Favorite Categories (existing). Filters on the Pokémon list: free-text search + a collapsible drawer for habitat/specialty selects (with active-filter count badge on the toggle). Habitats browse grid. Bottom nav with four tabs (active tab uses a soft green pill behind the icon). Pokopia-branded light theme — cream parchment background, grass-green accents, Fredoka/Nunito typography from Google Fonts, official Pokopia logo hotlinked into the home header with a CSS-wordmark fallback. About page credits Serebii, PokéAPI, and the official Pokémon Pokopia site with a fan-project disclaimer.

## Known gaps & likely next asks

These are deliberate v1 cuts. When Lawrence raises any of them, prefer the smallest change that satisfies the request — don't pre-emptively over-engineer.

- **Dedicated item search across favorites items.** Right now you reach a favorite-category item by drilling through a Pokémon → a favorite category. A top-level "Items" search that finds any of the 1,668 catalogued items by name and shows which categories (and therefore which Pokémon) like it is the obvious follow-up. (Note: litter items already have a dedicated browse + drill-in flow on the Items tab — this gap is about the much larger pool of favorite-category items.)
- **PWA support.** `manifest.json` + a tiny service worker would make the app installable to the home screen and work offline (data is already embedded; only sprites need cache). High value for a phone companion.
- **Bookmarks / favorites list.** Let the user mark a few Pokémon they're actively training.
- **Sortable list.** Currently sorts by Pokopia dex; sorting by name / habitat / specialty would be nice.
- **Type-tinted detail hero.** Detail hero is currently tinted by habitat; tinting by primary type is another option.
- **Pokémon with no favorites data** (Kyogre, Ditto event, Sableye event) just show an empty state. If Serebii fills these in later, a rescrape picks them up automatically.
- **Item lists are "work in progress"** on Serebii — re-running `--rescrape` periodically will keep them fresh.

## Conventions

- **Edit, don't rewrite.** Prefer `Edit` over `Write` for `index.html` since the data blob is large and a full overwrite is wasteful.
- **Don't add npm / a build step** without explicit approval.
- **Don't introduce localStorage.** The app needs to work in environments where storage is denied (some `file://` contexts, private browsing). `sessionStorage` with try/catch is the chosen pattern.
- **Don't use `innerHTML` with user/data-derived content.** Use the `el()` helper.
- **Any new search/text input must include `'data-focus': '<unique-id>'`.** `render()` does a full DOM swap on every keystroke, so without the focus marker the input would unmount mid-type — on mobile this dismisses the soft keyboard after every letter. The focus-restoration logic in `render()` keys off `data-focus`.
- **Inline comments for non-obvious decisions, and keep them in sync.** Whenever you make a choice that a future reader couldn't guess from the code alone — *why* a pattern was chosen, *what* edge case it handles, *which* trade-off was accepted — leave a comment near it. When you later change that code, audit the surrounding comments in the same edit and rewrite anything that's now wrong. A stale comment is worse than no comment because it actively misleads. Examples of decisions worth a comment: hotlinking vs bundling an asset, choosing one storage mechanism over another, why a glyph or icon was picked, why a search index is precomputed, why a fallback exists. Examples that don't need a comment: standard library calls, obvious variable names, anything restated by the code immediately below.
- **Verify changes by running the playwright smoke test** (it's not committed — write it inline in a bash call when needed). The pattern used in v1: navigate to list / detail / favorite-category in a 390×844 viewport, count rendered items, screenshot, eyeball.
- **Commit messages:** plain English, present tense ("Add item search", not "Added item search"). No conventional-commits prefix required; keep it human.

## Useful one-liners

```sh
# Local dev
open index.html

# Rebuild data after spreadsheet edits
python scripts/build-data.py

# Rebuild data + refetch Serebii (when game updates)
pip install requests openpyxl
python scripts/build-data.py --rescrape

# Verify the embedded JSON is still parseable
python -c "import re,json; h=open('index.html').read(); m=re.search(r'<script id=\"pokopia-data\"[^>]*>(.*?)</script>',h,re.S); d=json.loads(m.group(1).replace('<\\\\/','</')); print(len(d['pokemon']),'Pokémon,',len(d['favorites']),'categories')"
```
