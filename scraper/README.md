# MCP Scraper

A standalone MCP server that extracts metadata from FlixGarden (`flixgarden.site`)
movie and TV pages — title, year, synopsis, genres, cast, runtime, quality,
language and poster.

Read-only. It never downloads media, and it only reads the public HTML of the
site itself.

## How to run

```bash
pip install -r requirements.txt
python main.py
```

## Tool

`run_scrape_post_info(url)` — pass a `https://flixgarden.site/movie/<slug>/` or
`https://flixgarden.site/tv/<slug>/` URL.

Quick check without MCP:

```bash
python test_validation.py   # offline, no network
python test_scrape.py       # hits the live site, print-only
```

## How a page is read

Each page carries two descriptions of the same data: a schema.org JSON-LD block
(`Movie` / `TVSeries`) and the rendered hero markup (`.mf-*` classes). JSON-LD is
read first and the DOM fills gaps, so a page missing either source still scrapes.
Genre and cast names are returned exactly as the site spells them.

## When a scrape reports an error instead of success

A page can return HTTP 200 and still be unusable — bot interstitials, JS shells,
soft-404s and age gates all do. If extraction yields no title plus at least one
of synopsis/genres/cast, the tool returns `status: "error"` with a short
explanation rather than a hollow success, because a caller cannot otherwise
tell "no data" apart from "wrong page". The error distinguishes a bot wall from
a selector that stopped matching, so the fix is obvious.