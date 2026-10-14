# SEO-MCP

Two [MCP](https://modelcontextprotocol.io) servers for maintaining a
WordPress movie/TV catalogue: one scrapes a detail page's metadata, the other
turns that metadata into a WordPress draft with SEO fields and resolved
taxonomy.

Written for `flixgarden.site`, where titles live in `movie` and `tv` custom
post types rather than regular posts. The site-specific details are explained
below, because most of the non-obvious code exists to handle them.

## How it works

```
  flixgarden.site/movie/<slug>/
            │
            ▼
  ┌───────────────────────┐
  │  scraper              │   run_scrape_post_info(url)
  │  JSON-LD + DOM        │   → 18 metadata fields
  └───────────────────────┘
            │
            ▼   (the scraper's {"status", "data"} envelope is accepted verbatim)
  ┌───────────────────────┐
  │  wp-seo               │   run_create_draft_from_scraped_data(data)
  │  taxonomy + draft     │   → post_id
  └───────────────────────┘
            │
            ▼
  ┌───────────────────────┐
  │  wp-seo               │   run_generate_seo_for_draft(post_id)
  │  RankMath meta        │   → SEO fields written
  └───────────────────────┘
```

The two servers are separate processes with no shared state and no code in
common. The only contract between them is the shape of the scraped dict.

## Tools

| Server | Tool | Returns |
|---|---|---|
| `scraper` | `run_scrape_post_info(url)` | `dict` — envelope with 18 fields |
| `wp-seo` | `run_create_draft_from_scraped_data(scraped_data)` | `dict` — `post_id`, `link`, `featured_image_set`, `resolved` |
| `wp-seo` | `run_generate_seo_for_draft(post_id, content_type="movie")` | `str` — human-readable summary |

`content_type` is `"movie"` or `"tv"`. Note that `run_generate_seo_for_draft`
returns a formatted sentence, not structured data, because it is meant to be
read by an agent rather than parsed.

## Requirements

- Python 3.10+ (the code uses `X | None` annotations at runtime)
- A WordPress user with **Editor or above** — the tools read entries and terms
  with `context=edit`
- Network access to the target site and to your WordPress install

## Setup

**1. Install dependencies** (per server):

```bash
cd scraper && pip install -r requirements.txt
cd ../wp-seo && pip install -r requirements.txt
```

**2. Configure `wp-seo`:**

```bash
cp wp-seo/.env.example wp-seo/.env
```

Then fill in `WP_USERNAME` and `WP_APP_PASSWORD`. `WP_BASE_URL` and
`MONGO_URI` come pre-filled in most checkouts. The app password is **not** your
account password — create one under *wp-admin → Users → Profile → Application
Passwords*. It looks like `abcd efgh ijkl mnop qrst` and is displayed with
spaces; pass it exactly as shown.

> `.env` is gitignored and must never be committed. Until both credentials are
> real, every tool refuses to run and says so rather than issuing a request.

**3. Register both servers with your MCP client.** They use stdio transport:

```json
{
  "mcpServers": {
    "scraper": {
      "command": "python",
      "args": ["<repo>/scraper/main.py"],
      "cwd": "<repo>/scraper"
    },
    "wp-seo": {
      "command": "python",
      "args": ["<repo>/wp-seo/main.py"],
      "cwd": "<repo>/wp-seo"
    }
  }
}
```

`cwd` matters: both projects use flat imports, so the process must start inside
its own directory.

## Usage

Three calls. Pass the scraper's response straight into the next tool — the
envelope is unwrapped for you.

```
1. run_scrape_post_info("https://flixgarden.site/movie/dune-part-two/")
2. run_create_draft_from_scraped_data(<the full result of step 1>)
3. run_generate_seo_for_draft("<post_id from step 2>", "movie")
```

A representative response from step 2:

```json
{
  "status": "success",
  "post_id": 12345,
  "link": "https://flixgarden.site/movie/dune-part-two/",
  "featured_image_set": true,
  "resolved": {
    "genre": ["Action &amp; Adventure", "Sci-Fi &amp; Fantasy"],
    "cast": ["Timothée Chalamet", "Zendaya"],
    "unmatched_genres": ["Cyberpunk Noir"]
  }
}
```

Three things to know about that response:

- **`unmatched_genres`** are scraped genres with no matching WordPress term.
  They are reported and **never invented** — that is deliberate. If one is
  legitimate, add an alias to `wp-seo/genre_aliases.py` so it resolves
  deterministically next time.
- **Cast terms are created on demand** and are never reused or cleaned up, so
  the first time a name appears it becomes a permanent term.
- **Everything lands as a draft.** `status: "draft"` is hardcoded; nothing in
  this codebase can publish.

If the draft was created but a later step failed, the tool returns
`status: "partial"` with the `post_id` rather than stranding an orphan draft —
do not blindly retry, or you will duplicate it.

## Genre resolution

The site treats `genre` as a **closed set** (26 terms) while `actor` is
free-form. That asymmetry drives the design: genres are only ever *assigned*,
actors are *created*.

Matching runs in four ordered passes, and the order is the point:

1. **Exact** term names, collected first so nothing downstream can claim them.
2. **Manual aliases** from `genre_aliases.py`.
3. **Auto-derived fragments** — `"TV Movie (Film)"` also matches `Film`.
4. **Fuzzy** (`difflib`, default cutoff 0.78), restricted to full names only,
   because `difflib`'s ratio is length-biased and a short fragment would let
   short unrelated input score well.

`&` is never treated as a split point, so `"Sci-Fi & Fantasy"` cannot be
fragmented into a plain `"Fantasy"`. Term names on this site store HTML entities
literally — `"Sci-Fi &amp; Fantasy"` is a real term name — so
`genre_lookup.normalize()` decodes entities on both sides before comparing.

Every fuzzy hit is printed to stderr for review, turning a confidence guess
into something a human can promote into a deterministic alias.

## SEO generation

The template in `wp-seo/seo_template.py` is fully deterministic — no LLM is
involved:

```
title:       "<Title> (<Year>) - Watch & Download in HD | FlixGarden"
description: "Watch and download <Title> (<Year>) in HD on FlixGarden. ..."
```

Long titles are truncated *before* the suffix so the branding always survives.
The year is taken from the entry body, then the title, then an all-numeric slug
— and **omitted when unverified**, because a wrong year is worse than no year
when it lands in a published meta title. (`2001-a-space-odyssey` must not become
"2001"; it is from 1968.)

## Configuration

All optional; defaults match the live site. See `wp-seo/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `WP_GENRE_TAXONOMY` | `genre` | Taxonomy REST base |
| `WP_ACTOR_TAXONOMY` | `actor` | Taxonomy REST base |
| `GENRE_FUZZY_THRESHOLD` | `0.78` | `difflib` accept ratio for fuzzy genre matches |
| `GENRE_CACHE_TTL` | `86400` | Genre term cache lifetime, seconds |
| `WP_POSTER_HOSTS` | host of `WP_BASE_URL` | Hosts a poster may be fetched from |
| `WP_MAX_IMAGE_BYTES` | `5242880` | Poster download ceiling |
| `WP_MAX_IMAGE_REDIRECTS` | `3` | Redirect budget per poster fetch |
| `WP_POSTER_DOWNLOAD_DEADLINE` | `30` | Wall-clock seconds per poster fetch |

> The two `GENRE_*` names genuinely have no `WP_` prefix. Earlier revisions of
> `.env.example` documented them with one, which silently did nothing.

## Security notes

`.env` is gitignored. It contains a live MongoDB credential and the WordPress
application password — **rotate both if this repo has ever been shared or
committed.**

Poster downloads are treated as untrusted input, because `image_url` arrives
from a scraped page and is therefore attacker-influenced:

- Fetches are pinned to `WP_POSTER_HOSTS`, defaulting to your own site
- `http`/`https` only; resolved addresses must not be private, loopback,
  link-local (so no cloud metadata endpoint) or otherwise internal
- Redirects are followed manually with every hop re-validated, because
  `requests` otherwise resolves the whole chain itself and the per-hop checks
  never run
- Bodies stream under a byte cap and a wall-clock deadline
- Image type is sniffed from magic bytes rather than trusted from the remote
  `Content-Type` header

### Known limits

- **The host allowlist is the load-bearing control.** The IP checks are a second
  layer. Widening `WP_POSTER_HOSTS` to attacker-influenced hosts brings the
  redirect and DNS-rebinding exposure back with it.
- A host that streams slower than the read timeout can hold a worker for the
  length of the transfer. Memory stays bounded. With the default allowlist this
  is your own site misbehaving, not an attack path.
- Filenames with a dotted stem (`My_Poster.1.jpg`) are skipped. The draft is
  still created; the reason is logged to stderr.

## Tests

188 assertions across three offline suites. No network, WordPress or MongoDB
required, and no pytest:

```bash
cd wp-seo  && python test_taxonomy.py   # 45  genre resolution
cd wp-seo  && python test_fixes.py      # 116 hardening
cd scraper && python test_validation.py # 27  scrape guard
```

`test_fixes.py` runs real HTTP servers on loopback for the redirect cases on
purpose: a mocked response re-implements `is_redirect`, which hides exactly the
library behaviour the SSRF fix depends on.

Two files are **manual scripts, not tests** — `wp-seo/test.py` writes to the
live site, and `scraper/test_scrape.py` hits the real website. Neither asserts
anything; do not wire them into CI.

## Layout

```
scraper/
  main.py              MCP entrypoint
  tools.py             envelope handling + scrape validation
  scrapers/
    base.py            ScrapedPost dataclass, BaseScraper contract
    registry.py        host -> scraper allowlist
    flixgarden.py      JSON-LD with DOM fallback
wp-seo/
  main.py              MCP entrypoint
  tools.py             draft creation, SEO generation, input validation
  wp_client.py         WordPress REST client, hardened poster upload
  taxonomy.py          network-free genre/actor resolution
  genre_lookup.py      term cache + four-pass matching engine
  genre_aliases.py     hand-maintained alias table
  seo_template.py      deterministic RankMath fill
  config.py            env config and tunables
  db.py                MongoDB audit log (write-only)
```

## Licence

Private repository. All rights reserved.