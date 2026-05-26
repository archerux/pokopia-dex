# Pokopia Dex

A mobile-first companion guide for the Nintendo Switch 2 game **Pokémon Pokopia**. Look up a Pokémon to see its specialty, ideal habitat, favorite flavor, and the five categories of items it loves — then tap a category to see exactly which items qualify.

Built as a single self-contained `index.html` with all data baked in. No backend, no build step, no JavaScript framework — just open the file or drop it on any static host.

## Live site

https://pokopia-dex.pages.dev/

## What's in v1

- Searchable Pokémon list (filter by habitat, by specialty, or free-text search across name / type / specialty / habitat)
- Detail view per Pokémon — specialty, ideal habitat, location, favorite flavor, and favorite categories
- Tap any favorite category to see the full list of qualifying items with descriptions, plus a grid of every Pokémon that likes the category
- Browse-by-habitat view (Bright / Dark / Cool / Warm / Humid / Dry)
- Bottom navigation sized for one-handed phone use
- Sprites pulled from the [PokéAPI](https://pokeapi.co/) sprite CDN

## Running locally

Just open `index.html` in any browser — it's fully self-contained.

```sh
open index.html              # macOS
xdg-open index.html          # Linux
start index.html             # Windows
```

If you want a local server (some browsers handle relative paths better via HTTP):

```sh
python3 -m http.server 8000
# then open http://localhost:8000
```

## Deploying to Cloudflare Pages

1. Push this repo to GitHub.
2. In Cloudflare dashboard → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
3. Pick this repo. Configure the build:
   - **Framework preset:** _None_
   - **Build command:** _(leave empty)_
   - **Build output directory:** `/`
4. Click **Save and Deploy**. You'll get a `*.pages.dev` URL in about 30 seconds.

Any push to `main` will auto-deploy from then on. To add a custom domain, go to the project → **Custom domains** → **Set up a custom domain**.

## Rebuilding the data

The data shown by the app is embedded inside `index.html` at build time. If the game updates and you need to refresh it:

```sh
pip install openpyxl requests
python scripts/build-data.py              # uses cached Serebii data
python scripts/build-data.py --rescrape   # refetch Serebii favorite pages
```

The script reads three sources and rewrites the embedded `<script id="pokopia-data">` block in `index.html`:

- `Pokopia Pokemon.xlsx` — Pokémon list (sheet "All Pokemon")
- `data/serebii-favorites.json` — cached scrape of Serebii favorite-item pages
- `data/dex-map.json` — Pokémon name → national dex number, used to build sprite URLs

If the game adds Pokémon that aren't yet in `data/dex-map.json`, the script prints a warning listing the missing names. Add them to the JSON and re-run.

## Project layout

```
.
├── index.html                  Deployable single-file app (UI + embedded data)
├── Pokopia Pokemon.xlsx        Source spreadsheet (Pokémon attributes)
├── data/
│   ├── serebii-favorites.json  Cached scrape of favorite-item pages
│   └── dex-map.json            Name → national dex map for sprite URLs
├── scripts/
│   └── build-data.py           Regenerates the embedded data blob in index.html
├── LICENSE                     MIT (see file for attribution notes)
└── README.md
```

## Credits & attribution

- Pokémon attribute data was compiled from a community spreadsheet.
- Favorite-category item lists were scraped from [Serebii.net](https://www.serebii.net/pokemonpokopia/favorites.shtml).
- Pokémon sprites come from the [PokéAPI sprite repository](https://github.com/PokeAPI/sprites).
- Pokémon and character names are trademarks of Nintendo / Game Freak / Creatures Inc. This is an unofficial fan project, not affiliated with or endorsed by Nintendo.

## License

MIT — see [LICENSE](LICENSE).
