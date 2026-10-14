# wp-seo

A small MCP server that automates SEO for FlixGarden (`flixgarden.site`)
WordPress entries.

It has two tools:

- `run_generate_seo_for_draft(post_id, content_type)` — fetches an entry, fills
  its RankMath fields from a rule-based template, pushes the update back via the
  REST API, and logs completion to MongoDB.
- `run_create_draft_from_scraped_data(scraped_data)` — creates a movie/tv draft
  from metadata produced by the `scraper` MCP server, assigns matching genres,
  creates cast terms, and uploads the poster.

## Site model

Titles on this site are **not** regular posts. Each is an entry in one of two
custom post types:

| | REST base | Permalink |
|---|---|---|
| Movie | `/wp-json/wp/v2/movie` | `/movie/<slug>/` |
| TV show | `/wp-json/wp/v2/tv` | `/tv/<slug>/` |

Both carry the same two taxonomies — `genre` and `actor` — and neither has a
category taxonomy. That is why genres here are assigned rather than created
(the site treats them as a closed set), while cast terms are created on demand.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in:

```
WP_BASE_URL=https://flixgarden.site
WP_USERNAME=<your wp username>
WP_APP_PASSWORD=<application password>
MONGO_URI=<mongodb connection string>
```

The application password is *not* your account password: create one under
**wp-admin → Users → Profile → Application Passwords**. It looks like
`abcd efgh ijkl mnop qrst` and is shown with spaces — pass it exactly as shown.

The user needs **Editor or above** — the tools read entries and terms with
`context=edit`. While `WP_USERNAME`/`WP_APP_PASSWORD` are still the
`REPLACE_WITH_*` placeholders, every tool refuses to run and says so.

## How to run

```bash
python main.py
```

## Tests

All suites are offline — no network, no WordPress, no Mongo — and need no
pytest:

```bash
python test_taxonomy.py    # genre resolution (45 checks)
python test_fixes.py       # envelope bridge, year extraction, poster hardening
```

`test_fixes.py` covers the hardening: the scraper-envelope bridge, release-year
extraction, image magic-byte sniffing, SSRF rejection and the download size cap.

## Manual checks

```bash
python test.py                      # needs ENTRY_ID in the environment
python -c "import wp_client; print(wp_client.check_connection())"
```

## Genre aliases

`genre_aliases.py` maps a canonical WordPress genre name to the variants a
scraper may produce. Edit it by hand. Note that term names store HTML entities
literally — the site really does have a term called `Sci-Fi &amp; Fantasy` —
and `genre_lookup.normalize()` decodes those before comparing.

## Poster uploads

`run_create_draft_from_scraped_data` downloads the scraped poster server-side,
so the fetch is treated as untrusted input:

- **Poster hosts are pinned** to the host of `WP_BASE_URL` (override with
  `WP_POSTER_HOSTS`). `image_url` comes off a scraped page, so it is
  attacker-influenced; pinning it to hosts you control removes the open-redirect
  and DNS-rebinding surface before it matters.
- **http/https only**, and the resolved address must not be private, loopback,
  link-local (so no cloud metadata endpoint) or otherwise internal.
- **Redirects are followed manually**, with every hop re-checked against those
  rules and a `WP_MAX_IMAGE_REDIRECTS` budget. Note this requires
  `allow_redirects=False`: requests otherwise resolves the whole chain itself
  and the per-hop checks never run.
- **The body is streamed** under `WP_MAX_IMAGE_BYTES` and a
  `WP_POSTER_DOWNLOAD_DEADLINE` wall-clock cap, because `timeout=` alone is
  per-read and a slow-drip server would otherwise hold a worker indefinitely.
- **The image type is sniffed from magic bytes**, not taken from the remote
  `Content-Type`, so an HTML page served as `image/jpeg` is refused.

A failure returns `featured_image_set: false` and never fails the draft. If the
draft itself was created before a later step failed, the tool returns
`status: "partial"` with the `post_id` rather than stranding an orphan.

### Known limits

- **The host allowlist is the load-bearing control.** The IP checks are a
  second layer. If `POSTER_HOSTS` ever widens to attacker-influenced hosts, the
  redirect and rebinding exposure comes back with it.
- **A host that streams slower than the read timeout can hold a worker** for the
  length of the transfer; `POSTER_DOWNLOAD_DEADLINE` is only checked between
  reads, and urllib3's `read()` returns a chunk only at EOF for a body smaller
  than one chunk. Memory stays bounded by `WP_MAX_IMAGE_BYTES`. With the default
  allowlist this is your own site misbehaving, not an attack path.
- **`_safe_filename` refuses any dotted stem**, which is stricter than needed:
  a legitimate `My_Poster.1.jpg` is skipped (warned on stderr, draft still
  created).