"""
Regression tests for the hardening fixes.

Run: python test_fixes.py

Hand-rolled assertions in the same style as test_taxonomy.py so the suite runs
anywhere with just requests + python-dotenv installed. No pytest needed.

Most tests stub the network. The redirect tests deliberately do NOT: a
hand-rolled fake response reimplements `is_redirect` itself, which hides the
exact library behaviour that made the SSRF bypass possible in the first place
(requests follows redirects internally by default). Those use two real HTTP
servers on loopback instead.
"""
import http.server
import socket
import sys
import threading

import wp_client
from config import MAX_IMAGE_BYTES
from tools import _unwrap_scraped, _validate_scraped, _year_from_entry

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL  {label}")


def expect_error(label, fn, fragment):
    """Asserts fn raises, and that the message mentions `fragment`."""
    try:
        fn()
    except Exception as e:
        check(label, fragment.lower() in str(e).lower())
        return
    check(label, False)


# ---------------------------------------------------------------- year


def test_year_from_entry():
    print("\n== _year_from_entry (was _year_from_slug) ==")

    check(
        "2001-a-space-odyssey does not report 2001",
        _year_from_entry(
            {"slug": "2001-a-space-odyssey", "title": "2001: A Space Odyssey", "content": ""}
        ) == "",
    )
    check(
        "blade-runner-2049 does not report 2049",
        _year_from_entry(
            {"slug": "blade-runner-2049", "title": "Blade Runner 2049", "content": ""}
        ) == "",
    )
    check(
        "slug that is only a year -> 1917",
        _year_from_entry({"slug": "1917", "title": "1917", "content": ""}) == "1917",
    )
    check(
        "parenthesised year in title -> 2049",
        _year_from_entry({"slug": "blade-runner-2049", "title": "Blade Runner (2049)", "content": ""})
        == "2049",
    )

    body = "<p>Some synopsis.</p><p><strong>Year:</strong> 2019<br><strong>Quality:</strong> 4K</p>"
    check(
        "Year: row in body -> 2019",
        _year_from_entry({"slug": "1917", "title": "1917", "content": body}) == "2019",
    )
    check(
        "body beats parenthesised title",
        _year_from_entry({"content": body, "title": "Something (2001)", "slug": "x"}) == "2019",
    )
    check(
        "Year: row survives entity/whitespace noise",
        _year_from_entry(
            {"content": "<p><strong>Year:</strong>&nbsp;2013&nbsp;</p>", "title": "", "slug": ""}
        ) == "2013",
    )
    check(
        "double-escaped Year: row is still found",
        _year_from_entry(
            {"content": "<p>&lt;strong&gt;Year:&lt;/strong&gt; 2019</p>", "title": "", "slug": ""}
        ) == "2019",
    )
    check(
        "year embedded mid-sentence is not mistaken for a row",
        _year_from_entry({"content": "<p>Set in 1999 the film...", "title": "", "slug": ""}) == "",
    )

    # The synopsis paragraph renders BEFORE the fact row, so an unanchored
    # "Year:" search always lost to prose on any entry mentioning a year.
    check(
        "prose 'Year: 1994' cannot outrank the real fact row",
        _year_from_entry(
            {
                "content": "<p>Set in the Year: 1994, a teacher...</p>"
                "<p><strong>Year:</strong> 2019</p>",
                "title": "",
                "slug": "",
            }
        ) == "2019",
    )
    check(
        "'Release Year: 1994' prose cannot outrank the real row",
        _year_from_entry(
            {
                "content": "<p>Its Release Year: 1994 is disputed.</p>"
                "<p><strong>Year:</strong> 2019</p>",
                "title": "",
                "slug": "",
            }
        ) == "2019",
    )
    check(
        "a <style> block cannot outrank the real row",
        _year_from_entry(
            {
                "content": '<style>.a{content:"Year: 1994"}</style>'
                "<p><strong>Year:</strong> 2019</p>",
                "title": "",
                "slug": "",
            }
        ) == "2019",
    )
    check(
        "escaped code sample cannot outrank the title year",
        _year_from_entry(
            {"content": "<p>Use &lt;Year: 1994&gt; syntax.</p>", "title": "Doc (2005)", "slug": ""}
        ) == "2005",
    )

    # Unicode digits must never reach a published title.
    check(
        "Arabic-Indic digits in slug rejected",
        _year_from_entry({"content": "", "slug": "20\u0661\u0669", "title": ""}) == "",
    )
    check(
        "Arabic-Indic digits in body row rejected",
        _year_from_entry(
            {"content": "<p><strong>Year:</strong> \u0662\u0660\u0661\u0669</p>", "title": "", "slug": ""}
        )
        == "",
    )
    check(
        "fullwidth digits in slug rejected",
        _year_from_entry({"slug": "\uff12\uff10\uff12\uff14", "title": "", "content": ""}) == "",
    )

    check("empty entry -> ''", _year_from_entry({}) == "")
    check("None content/slug/title -> ''", _year_from_entry({"content": None, "slug": None, "title": None}) == "")
    check("non-string content -> ''", _year_from_entry({"content": 12345, "slug": 99, "title": 7}) == "")
    check("non-dict entry -> ''", _year_from_entry(None) == "")
    check("list entry -> ''", _year_from_entry([]) == "")

    for bad in ("1899", "2100", "1234", "9999", "0000"):
        check(
            f"out-of-range {bad} rejected",
            _year_from_entry(
                {"content": f"<p><strong>Year:</strong> {bad}</p>", "title": "", "slug": ""}
            )
            == "",
        )


# ---------------------------------------------------------------- envelope


def test_envelope_bridge():
    print("\n== _unwrap_scraped (envelope bridge) ==")

    payload, err = _unwrap_scraped({"status": "success", "data": {"title": "Dune"}})
    check("success envelope unwraps", payload == {"title": "Dune"} and err == "")

    payload, err = _unwrap_scraped({"title": "Dune"})
    check("flat dict passes through", payload == {"title": "Dune"} and err == "")

    payload, err = _unwrap_scraped({"data": {"title": "Dune"}})
    check("envelope without status still unwraps", payload == {"title": "Dune"} and err == "")

    payload, err = _unwrap_scraped({"status": "error", "message": "bot-challenge detected"})
    check("scraper error is surfaced", payload is None and "bot-challenge detected" in err)

    payload, err = _unwrap_scraped({"status": "error", "message": "no scraper", "data": {"title": "x"}})
    check("error wins over a stale data field", payload is None and "no scraper" in err)

    for label, bad in [
        ("status=failed", {"status": "failed", "message": "bot-challenge detected"}),
        ("status=errored", {"status": "errored", "message": "rate limited"}),
        ("error key", {"status": "error", "error": "blocked by cloudflare"}),
        ("success=False", {"success": False, "message": "nothing extracted"}),
        ("status=Error!", {"status": "Error!", "message": "boom"}),
    ]:
        payload, err = _unwrap_scraped(bad)
        check(f"{label} surfaces the message", payload is None and err.startswith("scraper reported"))

    payload, err = _unwrap_scraped({"status": "error", "message": ""})
    check("empty message still diagnosable", payload is None and "envelope keys" in err)

    payload, err = _unwrap_scraped({"data": None, "message": "no metadata could be extracted"})
    check("null data with message surfaces", payload is None and "no metadata" in err)

    payload, err = _unwrap_scraped(
        {"status": "success", "data": {"status": "success", "data": {"title": "Dune"}}}
    )
    check("nested envelope unwraps fully", payload == {"title": "Dune"} and err == "")

    payload, err = _unwrap_scraped({"title": "Dune", "synopsis": "s", "data": {"codec": "x"}})
    check("payload with its own 'data' key is not mangled", payload["title"] == "Dune")
    payload, err = _unwrap_scraped({"title": "Dune", "data": "H.265"})
    check("payload with non-dict 'data' is not mangled", payload["title"] == "Dune")

    payload, err = _unwrap_scraped({"status": "success", "data": "not a dict"})
    check("non-dict data rejected", payload is None and "must be a dict" in err)

    for bad in (None, [], "string", 42):
        payload, err = _unwrap_scraped(bad)
        check(f"non-dict input rejected: {bad!r}", payload is None and "must be a dict" in err)

    payload, err = _unwrap_scraped({"status": "success"})
    check("status success with no data passes through flat", payload == {"status": "success"} and err == "")


# ---------------------------------------------------------------- input validation


def test_scraped_validation():
    """
    The guard that stops malformed payloads becoming permanent damage:
    cast="Zendaya" used to create six junk actor terms on the live site, and
    actor terms are only ever created, never reused.
    """
    print("\n== _validate_scraped (blocks WordPress pollution) ==")

    clean = {
        "title": "Dune",
        "synopsis": "Paul Atreides.",
        "image_url": "https://flixgarden.site/p.jpg",
        "genres": ["Action"],
        "cast": ["Zendaya"],
        "year": "2024",
    }
    check("well-formed payload passes", _validate_scraped(clean) == [])

    problems = _validate_scraped({"title": "Dune", "cast": "Zendaya"})
    check("cast as a bare string is rejected", len(problems) == 1 and "'cast'" in problems[0])

    problems = _validate_scraped({"title": "Dune", "genres": "Action, Drama"})
    check("genres as a bare string is rejected", len(problems) == 1 and "'genres'" in problems[0])

    problems = _validate_scraped({"title": "Dune", "cast": ["Zendaya", 5]})
    check("non-string list member is rejected", len(problems) == 1 and "only strings" in problems[0])

    problems = _validate_scraped({"title": "Dune", "synopsis": {"x": 1}})
    check("dict synopsis is rejected", len(problems) == 1 and "'synopsis'" in problems[0])

    for label, bad_year in [
        ("N/A", "N/A"),
        ("dash", "-"),
        ("19xx", "19xx"),
        ("1899 (too early)", "1899"),
        ("2100 (too late)", "2100"),
        ("arabic-indic digits", "20\u0661\u0669"),
        ("int", 2024),
    ]:
        problems = _validate_scraped({"title": "Dune", "year": bad_year})
        check(f"bad year {label} rejected", len(problems) == 1 and "'year'" in problems[0])

    check("year None accepted", _validate_scraped({"title": "Dune", "year": None}) == [])
    check("year empty string accepted", _validate_scraped({"title": "Dune", "year": ""}) == [])
    check("year missing accepted", _validate_scraped({"title": "Dune"}) == [])
    check("genres/cast None accepted", _validate_scraped({"title": "Dune", "cast": None}) == [])

    problems = _validate_scraped({"title": "Dune", "cast": "Z", "genres": "G", "year": "x"})
    check("multiple problems reported together", len(problems) == 3)


# ---------------------------------------------------------------- image sniffing

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
AVIF = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 32
HEIC = b"\x00\x00\x00\x20ftypheic" + b"\x00" * 32


def test_image_sniffing():
    print("\n== _sniff_image (never trust the remote Content-Type) ==")
    for data, want, label in [
        (JPEG, "image/jpeg", "jpeg"),
        (PNG, "image/png", "png"),
        (GIF, "image/gif", "gif"),
        (WEBP, "image/webp", "webp"),
        (AVIF, "image/avif", "avif"),
        (HEIC, "image/heic", "heic"),
    ]:
        mime, ext = wp_client._sniff_image(data)
        check(f"{label} sniffed", mime == want and ext is not None)

    for payload, label in [
        (b"<!DOCTYPE html><html><body>pwned</body></html>", "html"),
        (b"", "empty body"),
        (b"%PDF-1.4\n%binary", "pdf"),
        (b"\x00\x00\x00\x18ftypmp42", "mp4 video"),
        (b"<script>alert(1)</script>", "javascript"),
        (b"\x89PNG\r\n\x1a\n", "truncated 8-byte png"),
    ]:
        mime, ext = wp_client._sniff_image(payload)
        if label == "truncated 8-byte png":
            check(f"{label} still recognised (lands as .png)", mime == "image/png")
        else:
            check(f"{label} rejected", mime is None and ext is None)


def test_url_validation():
    print("\n== _assert_fetchable_url (SSRF guard) ==")

    real_hosts = wp_client.POSTER_HOSTS
    real_dns = wp_client.socket.getaddrinfo
    wp_client.POSTER_HOSTS = (
        "evil.example", "sneaky.example", "intranet.example", "flixgarden.site", "a.example",
    )
    try:
        expect_error(
            "file:// scheme rejected",
            lambda: wp_client._assert_fetchable_url("file:///etc/passwd"),
            "http or https",
        )
        expect_error(
            "ftp:// scheme rejected",
            lambda: wp_client._assert_fetchable_url("ftp://example.com/x.jpg"),
            "http or https",
        )
        expect_error(
            "no scheme rejected",
            lambda: wp_client._assert_fetchable_url("example.com/x.jpg"),
            "http or https",
        )

        def set_ip(ip):
            wp_client.socket.getaddrinfo = lambda host, port, *a, **k: [
                (2, 1, 6, "", (ip, port or 80))
            ]

        set_ip("169.254.169.254")
        expect_error(
            "cloud metadata endpoint rejected",
            lambda: wp_client._assert_fetchable_url("http://evil.example/latest/meta-data/"),
            "private or internal",
        )
        expect_error(
            "hostname resolving to internal IP rejected",
            lambda: wp_client._assert_fetchable_url("https://sneaky.example/poster.jpg"),
            "private or internal",
        )
        set_ip("127.0.0.1")
        expect_error(
            "loopback rejected",
            lambda: wp_client._assert_fetchable_url("http://evil.example/p.jpg"),
            "private or internal",
        )
        set_ip("10.1.2.3")
        expect_error(
            "rfc1918 private rejected",
            lambda: wp_client._assert_fetchable_url("https://intranet.example/p.jpg"),
            "private or internal",
        )
        set_ip("93.184.216.34")
        wp_client._assert_fetchable_url("https://flixgarden.site/img/poster.jpg")
        check("public https allowed", True)

        # The host allowlist is the control that actually matters in practice.
        wp_client.POSTER_HOSTS = ("flixgarden.site",)
        wp_client._assert_fetchable_url("https://flixgarden.site/p.jpg")
        check("allowlisted host allowed", True)
        expect_error(
            "host outside the allowlist rejected",
            lambda: wp_client._assert_fetchable_url("https://attacker.example/p.jpg"),
            "poster hosts are restricted",
        )

        # The parser-desync bypass: urlparse takes the host after the last '@',
        # urllib3 (which requests uses) takes the text before it. A backslash
        # makes them disagree, so the validated host is not the dialled host.
        for url in (
            "http://127.0.0.1\\@flixgarden.site/x.png",
            "http://127.0.0.1:80\\@flixgarden.site/x.png",
            "http://169.254.169.254\\@flixgarden.site/latest/",
        ):
            try:
                wp_client._assert_fetchable_url(url)
                check(f"backslash desync blocked: {url[:34]}", False)
            except ValueError as e:
                check(
                    f"backslash desync blocked: {url[:34]}",
                    "backslash" in str(e) or "ambiguous" in str(e),
                )

        expect_error(
            "multiple '@' in authority rejected",
            lambda: wp_client._assert_fetchable_url("http://a@127.0.0.1@flixgarden.site/x.png"),
            "more than one '@'",
        )

        # Fails closed: an unresolvable allowlist must never mean "allow all".
        wp_client.POSTER_HOSTS = ("",)
        expect_error(
            "empty allowlist fails closed",
            lambda: wp_client._assert_fetchable_url("https://flixgarden.site/p.jpg"),
            "no poster hosts are configured",
        )
    finally:
        wp_client.POSTER_HOSTS = real_hosts
        wp_client.socket.getaddrinfo = real_dns


def test_safe_filename():
    print("\n== _safe_filename (header injection / extension smuggling) ==")

    check(
        "quoted URL is decoded",
        wp_client._safe_filename("https://x.com/a%20b/poster.jpg", ".jpg") == "poster.jpg",
    )
    check(
        "query string dropped",
        wp_client._safe_filename("https://x.com/poster.jpg?v=2", ".jpg") == "poster.jpg",
    )
    check(
        "URL extension replaced by sniffed one",
        wp_client._safe_filename("https://x.com/poster.jpg", ".png") == "poster.png",
    )
    check(
        "quotes stripped",
        '"' not in wp_client._safe_filename('https://x.com/a"b/c.jpg', ".jpg"),
    )
    check(
        "CRLF stripped",
        "\r" not in wp_client._safe_filename("https://x.com/a%0d%0aX-Evil:1/f.jpg", ".jpg"),
    )
    check(
        "long stem truncated",
        len(wp_client._safe_filename("https://x.com/" + "a" * 300 + ".jpg", ".jpg")) <= 64,
    )
    check(
        "empty path falls back to poster",
        wp_client._safe_filename("https://x.com/", ".jpg") == "poster.jpg",
    )
    check(
        "path traversal collapses",
        wp_client._safe_filename("https://x.com/../../etc/passwd.jpg", ".jpg") == "passwd.jpg",
    )
    # Intermediate executable extensions must not survive.
    for url in (
        "https://x.com/shell.php.jpg",
        "https://x.com/shell.phtml.png",
        "https://x.com/evil.svg.jpg",
        "https://x.com/shell.asp.gif",
    ):
        try:
            name = wp_client._safe_filename(url, ".jpg")
            check(f"{url.rsplit('/', 1)[-1]} refused", False)
            print(f"        got {name!r}")
        except ValueError as e:
            check(f"{url.rsplit('/', 1)[-1]} refused", "extra extension" in str(e))


# ---------------------------------------------------------------- byte cap (stubbed)


class FakeResponse:
    """Minimal stand-in for a streamed requests.Response."""

    def __init__(self, chunks, headers=None, status=200, redirect_to=None):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status
        if redirect_to is not None:
            self._redirect_to = redirect_to
        elif status in (301, 302, 303, 307, 308) and self.headers.get("location"):
            self._redirect_to = self.headers["location"]
        else:
            self._redirect_to = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def is_redirect(self):
        return self._redirect_to is not None

    @property
    def is_permanent_redirect(self):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, n):
        yield from self._chunks


def test_byte_cap():
    print("\n== _download_capped: size ceiling (stubbed stream) ==")

    real_get = wp_client.requests.get
    real_dns = wp_client.socket.getaddrinfo
    real_hosts = wp_client.POSTER_HOSTS

    wp_client.POSTER_HOSTS = ("a.example",)
    wp_client.socket.getaddrinfo = lambda host, port, *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", port or 80))
    ]
    try:
        wp_client.requests.get = lambda url, **kw: FakeResponse([PNG], {"content-length": str(len(PNG))})
        check("small image passes", wp_client._download_capped("https://a.example/p.png", {}) == PNG)

        big = [PNG] * ((MAX_IMAGE_BYTES // len(PNG)) + 50)
        wp_client.requests.get = lambda url, **kw: FakeResponse(big)
        expect_error(
            "oversized stream aborted mid-download",
            lambda: wp_client._download_capped("https://a.example/p.png", {}),
            "exceeded",
        )

        wp_client.requests.get = lambda url, **kw: FakeResponse(
            [PNG], {"content-length": str(MAX_IMAGE_BYTES + 1)}
        )
        expect_error(
            "oversized content-length rejected up front",
            lambda: wp_client._download_capped("https://a.example/p.png", {}),
            "over the",
        )

        # A lying content-length must not save an oversized body.
        wp_client.requests.get = lambda url, **kw: FakeResponse(big, {"content-length": "10"})
        expect_error(
            "lying content-length cannot bypass the cap",
            lambda: wp_client._download_capped("https://a.example/p.png", {}),
            "exceeded",
        )

        # str.isdigit() is true for Unicode superscripts; int() then raises and
        # turns any image into a hard failure.
        wp_client.requests.get = lambda url, **kw: FakeResponse(
            [PNG], {"content-length": "\u00b2\u00b2\u00b2\u00b2"}
        )
        try:
            data = wp_client._download_capped("https://a.example/p.png", {})
            check("unicode-superscript Content-Length falls through to the cap", data == PNG)
        except Exception as e:
            check("unicode-superscript Content-Length falls through to the cap", False)
            print(f"        {type(e).__name__}: {e}")

        # Slowloris: many small chunks must hit the wall-clock deadline.
        import time as _time

        wp_client.requests.get = lambda url, **kw: FakeResponse([PNG] * 40)
        real_deadline = wp_client.POSTER_DOWNLOAD_DEADLINE
        wp_client.POSTER_DOWNLOAD_DEADLINE = 0.0
        try:
            expect_error(
                "wall-clock deadline enforced",
                lambda: wp_client._download_capped("https://a.example/p.png", {}),
                "wall-clock",
            )
        finally:
            wp_client.POSTER_DOWNLOAD_DEADLINE = real_deadline

        wp_client.requests.get = lambda url, **kw: FakeResponse([], status=404)
        expect_error(
            "404 surfaces",
            lambda: wp_client._download_capped("https://a.example/p.png", {}),
            "404",
        )
    finally:
        wp_client.requests.get = real_get
        wp_client.socket.getaddrinfo = real_dns
        wp_client.POSTER_HOSTS = real_hosts


# ---------------------------------------------------------------- redirects (REAL servers)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever the owning test configured."""

    def do_GET(self):
        self.server.hits.append(self.path)
        spec = self.server.spec
        status, headers, body = spec.get(self.path, (404, {}, b""))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve(spec):
    """Starts a real loopback HTTP server. Returns (server, thread, base_url)."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    srv.spec = spec
    srv.hits = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def test_redirects_real_server():
    """
    Real HTTP, because the bug this guards against was invisible to a mock.

    requests follows redirects itself unless told not to, so with
    allow_redirects left at its default the whole chain was resolved internally
    and the per-hop validation below never executed. A stubbed response cannot
    catch that: it reimplements is_redirect, which is exactly the behaviour at
    fault.
    """
    print("\n== redirects, over real HTTP servers ==")

    real_dns = wp_client.socket.getaddrinfo
    real_hosts = wp_client.POSTER_HOSTS
    real_get = wp_client.requests.get
    seen_kwargs = []

    # Note: wp_client.socket IS the stdlib socket module, so this is global for
    # the duration of the test and restored in the finally block.
    #
    # The test servers live on loopback, which _assert_fetchable_url correctly
    # rejects, so the first lookup for a given (host, port) returns a public
    # address (satisfying the validator) and the second -- the actual connect --
    # returns the loopback address the server is really on. That is exactly the
    # relationship that holds in production: the host resolves to the address we
    # then connect to.
    lookups = [0]

    def fake_dns(host, port, *a, **k):
        # Calls alternate strictly between the validator and the connection
        # that follows it, so a redirect chain of any length keeps working.
        n = lookups[0]
        lookups[0] += 1
        ip = "93.184.216.34" if n % 2 == 0 else "127.0.0.1"
        return [(2, 1, 6, "", (ip, port or 80))]

    wp_client.socket.getaddrinfo = fake_dns

    def spy_get(url, **kwargs):
        seen_kwargs.append((url, kwargs.get("allow_redirects")))
        return real_get(url, **kwargs)

    servers = []
    try:
        # Target server: serves a real PNG.
        target, _, target_url = _serve({"/p.png": (200, {"Content-Type": "image/png"}, PNG)})
        servers.append(target)
        wp_client.POSTER_HOSTS = ("127.0.0.1",)

        # 1. allow_redirects=False must actually reach requests.
        wp_client.requests.get = spy_get
        wp_client._download_capped(f"{target_url}/p.png", {})
        check(
            "allow_redirects=False is passed to requests",
            bool(seen_kwargs) and seen_kwargs[0][1] is False,
        )

        # 2. A redirect that stays on an allowed host is followed manually.
        hop, _, hop_url = _serve(
            {"/start": (302, {"Location": f"{target_url}/p.png"}, b"")}
        )
        servers.append(hop)
        seen_kwargs.clear()
        data = wp_client._download_capped(f"{hop_url}/start", {})
        check("allowed-host redirect is followed to the image", data == PNG)
        check(
            "redirect was followed by our loop, not by requests",
            [u for u, _ in seen_kwargs] == [f"{hop_url}/start", f"{target_url}/p.png"],
        )

        # 3. A redirect to a host outside the allowlist is refused before the
        #    target is ever contacted.
        evil, _, evil_url = _serve({"/x.png": (200, {}, b"\x89PNG\r\n\x1a\nEVIL")})
        servers.append(evil)
        hop2, _, hop2_url = _serve(
            {"/go": (302, {"Location": f"{evil_url}/x.png"}, b"")}
        )
        servers.append(hop2)
        seen_kwargs.clear()
        before = len(evil.hits)
        wp_client.POSTER_HOSTS = ("allowed.example",)
        try:
            wp_client._download_capped(f"{hop2_url}/go", {})
            check("redirect to a disallowed host is blocked", False)
        except ValueError as e:
            check("redirect to a disallowed host is blocked", "poster hosts are restricted" in str(e))
        check("disallowed redirect target never received a request", len(evil.hits) == before)

        # 4. A redirect loop terminates.
        loop, _, loop_url = _serve({})
        servers.append(loop)
        wp_client.POSTER_HOSTS = ("127.0.0.1",)
        loop.spec["/a"] = (302, {"Location": f"{loop_url}/a"}, b"")
        expect_error(
            "redirect loop terminates",
            lambda: wp_client._download_capped(f"{loop_url}/a", {}),
            "too many redirects",
        )
    finally:
        wp_client.requests.get = real_get
        wp_client.socket.getaddrinfo = real_dns
        wp_client.POSTER_HOSTS = real_hosts
        for srv in servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


def main():
    print("=" * 72)
    print("HARDENING REGRESSION TESTS")
    print("=" * 72)

    test_year_from_entry()
    test_envelope_bridge()
    test_scraped_validation()
    test_image_sniffing()
    test_url_validation()
    test_safe_filename()
    test_byte_cap()
    test_redirects_real_server()

    print("\n" + "=" * 72)
    print(f"{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 72)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()