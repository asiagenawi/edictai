from datetime import date
from pathlib import Path

import pytest
import yaml

from edictai.paths import Paths
from edictai.records import load_records
from edictai.watch import (
    USER_AGENT,
    Fetcher,
    Response,
    extract_html,
    find_documents_for_url,
    looks_like_ai_link,
    normalize_url,
    propose_revisions,
    run,
)
from edictai.watch.diff import diff_excerpt

ORG_URL = "https://example.gov/ai/"
DOC_PDF = "https://example.gov/files/ai-guidance.pdf"


def page(body: str, *, title: str = "AI in Schools") -> str:
    return f"""<!doctype html><html><head><title>{title}</title>
<script>var tracker = 1;</script><style>body {{ color: red }}</style></head>
<body>
<header><a href="/">Home</a> Site header</header>
<nav><ul><li><a href="/about">About</a></li><li><a href="/ai-news">AI news</a></li></ul></nav>
<main>
<h1>Artificial Intelligence</h1>
{body}
</main>
<footer>Copyright 2026 <a href="/privacy">Privacy</a></footer>
</body></html>"""


BASE_BODY = """
<h2>Guidance</h2>
<p>The department issued guidance for schools on the responsible use of AI tools
in classrooms. It covers privacy,   academic integrity, and staff training.</p>
<p><a href="/files/ai-guidance.pdf">AI guidance (PDF)</a></p>
<h2>Contact</h2>
<p>Questions about this page go to the office of digital learning, which answers
within five business days.</p>
"""


class FakeWeb:
    """Serves canned responses by URL and records every request."""

    def __init__(self, pages=None, robots="User-agent: *\nAllow: /\n"):
        self.pages: dict[str, dict] = pages or {}
        self.robots = robots
        self.requests: list[tuple[str, dict]] = []

    def set(self, url, body, *, status=200, etag=None, content_type="text/html; charset=utf-8"):
        headers = {"content-type": content_type}
        if etag:
            headers["etag"] = etag
        self.pages[url] = {"status": status, "body": body, "headers": headers}

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers)))
        if url.endswith("/robots.txt"):
            if self.robots is None:
                return Response(404, {}, b"", url)
            return Response(200, {"content-type": "text/plain"}, self.robots.encode(), url)
        spec = self.pages.get(url)
        if spec is None:
            return Response(404, {}, b"not found", url)
        etag = spec["headers"].get("etag")
        if etag and headers.get("If-None-Match") == etag:
            return Response(304, {"etag": etag}, b"", url)
        body = spec["body"]
        return Response(
            spec["status"], spec["headers"], body.encode() if isinstance(body, str) else body, url
        )

    def page_requests(self):
        return [u for u, _ in self.requests if not u.endswith("/robots.txt")]


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Paths:
    (tmp_path / "data" / "schema").mkdir(parents=True)
    write(
        tmp_path / "data" / "organizations" / "us-zz-k12-agency.yaml",
        {
            "id": "us-zz-k12-agency",
            "name": "Example Department of Education",
            "classification": "k12-agency",
            "jurisdiction": "us-zz",
            "url": "https://example.gov/",
            "watch_urls": [ORG_URL],
        },
    )
    write(
        tmp_path / "data" / "documents" / "us-zz" / "zz-ai-guidance.yaml",
        {
            "id": "us-zz-zz-ai-guidance",
            "jurisdiction": "us-zz",
            "issuer": "us-zz-k12-agency",
            "title": "AI Guidance",
            "sources": [
                {"url": "https://example.gov/ai", "kind": "official", "accessed": "2026-10-01"},
                {"url": DOC_PDF, "kind": "official", "accessed": "2026-10-01"},
            ],
        },
    )
    return Paths(tmp_path)


def go(repo, web, day, **kw):
    return run(
        repo,
        transport=web,
        today=day,
        sleep=lambda s: None,
        cache_dir=repo.root / ".cache" / "watch",
        **kw,
    )


# -- extraction ------------------------------------------------------------


def test_extract_strips_chrome_and_normalizes():
    ex = extract_html(page(BASE_BODY), ORG_URL)
    assert ex.title == "AI in Schools"
    assert "tracker" not in ex.text and "color: red" not in ex.text
    assert "Site header" not in ex.text and "About" not in ex.text and "Copyright" not in ex.text
    assert "# Artificial Intelligence" in ex.text
    assert "privacy, academic integrity" in ex.text  # whitespace collapsed
    assert [link.url for link in ex.links] == [DOC_PDF]


def test_ai_link_detection():
    assert looks_like_ai_link("https://x.gov/docs/report.pdf", "Generative AI toolkit")
    assert looks_like_ai_link("https://x.gov/artificial-intelligence/policy", "Policy")
    assert looks_like_ai_link("https://x.gov/files/k12-ai-framework.pdf", "Framework")
    assert not looks_like_ai_link("https://x.gov/maintenance/said", "Training schedule")
    assert not looks_like_ai_link("https://x.gov/email", "Email us")


def test_normalize_url():
    assert normalize_url("http://Example.gov/ai/#top") == normalize_url("https://example.gov/ai")
    assert normalize_url("https://www.example.gov/a.pdf") == normalize_url(
        "https://example.gov/a.pdf"
    )


def test_diff_excerpt_names_section():
    old = "# Title\n## Guidance\nline one\nline two\n## Contact\nemail"
    new = "# Title\n## Guidance\nline one\nline 2\n## Contact\nemail"
    out = diff_excerpt(old, new)
    assert "Guidance" in out.splitlines()[0]
    assert "-line two" in out and "+line 2" in out


# -- fetching --------------------------------------------------------------


def test_user_agent_and_rate_limit():
    web = FakeWeb()
    web.set("https://a.gov/1", "<p>x</p>")
    web.set("https://a.gov/2", "<p>y</p>")
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    fetcher = Fetcher(web, sleep=sleep, clock=lambda: now[0])
    fetcher.get("https://a.gov/1")
    fetcher.get("https://a.gov/2")
    assert all(h["User-Agent"] == USER_AGENT for _, h in web.requests)
    assert "github.com/asiagenawi/edictai" in USER_AGENT
    # robots.txt, /1, /2 on one host: two waits of a full second each
    assert sleeps == [1.0, 1.0]


def test_robots_disallow_skips_page(repo):
    web = FakeWeb(robots="User-agent: *\nDisallow: /ai/\n")
    web.set(ORG_URL, page(BASE_BODY))
    report, md = go(repo, web, date(2026, 10, 17))
    assert web.page_requests() == []
    assert report.skipped[0]["reason"] == "disallowed by robots.txt"
    assert "Skipped (1)" in md


def test_block_is_not_retried(repo):
    web = FakeWeb()
    web.set(ORG_URL, "<html>Access denied</html>", status=403)
    report, _ = go(repo, web, date(2026, 10, 17))
    assert report.skipped and report.skipped[0]["reason"].startswith("HTTP 403")
    assert web.page_requests() == [ORG_URL]


# -- runs ------------------------------------------------------------------


def test_unchanged_page(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY))
    first, _ = go(repo, web, date(2026, 10, 10))
    assert len(first.baselined) == 1 and not first.changes

    # Cosmetic markup and whitespace changes do not count as a change.
    web.set(ORG_URL, page(BASE_BODY.replace("<p>", "<p class='x'>  ")))
    second, md = go(repo, web, date(2026, 10, 17))
    assert second.changes == [] and len(second.unchanged) == 1
    assert second.unchanged[0]["via"] == "hash"
    assert "no changes" in md


def test_conditional_request_uses_etag(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY), etag='"v1"')
    go(repo, web, date(2026, 10, 10))
    report, _ = go(repo, web, date(2026, 10, 17))
    sent = [h for u, h in web.requests if u == ORG_URL][-1]
    assert sent["If-None-Match"] == '"v1"'
    assert report.unchanged[0]["via"] == "304"


def test_changed_page_diff_links_and_proposal(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY))
    go(repo, web, date(2026, 10, 10))

    changed = BASE_BODY.replace("staff training", "staff training and assessment").replace(
        "<h2>Contact</h2>",
        '<p><a href="/files/genai-toolkit-2026.pdf">Generative AI Toolkit (2026)</a></p>'
        '<p><a href="/files/bus-schedule.pdf">Bus schedule</a></p><h2>Contact</h2>',
    )
    web.set(ORG_URL, page(changed))
    report, md = go(repo, web, date(2026, 10, 17))

    assert len(report.changes) == 1
    change = report.changes[0]
    assert change["org_id"] == "us-zz-k12-agency"
    assert change["observed"] == "2026-10-17"
    assert change["old_hash"] != change["new_hash"]
    assert "@@" in change["diff_excerpt"] and "Guidance" in change["diff_excerpt"]
    assert "+" in change["diff_excerpt"] and "assessment" in change["diff_excerpt"]
    # Only the new AI link; the PDF already cited by a record and the bus schedule are not.
    assert change["ai_links"] == [
        {
            "url": "https://example.gov/files/genai-toolkit-2026.pdf",
            "text": "Generative AI Toolkit (2026)",
        }
    ]

    assert len(report.proposals) == 1
    proposal = report.proposals[0]
    assert proposal["document_id"] == "us-zz-zz-ai-guidance"
    assert proposal["via"] == "source"
    assert proposal["revision"]["sha256"] == change["new_hash"]
    assert proposal["revision"]["observed"] == "2026-10-17"

    assert "## Watched pages: 1 changed" in md
    assert "Generative AI Toolkit (2026)" in md and "```diff" in md
    assert "us-zz-zz-ai-guidance" in md

    # The data tree is never written.
    doc = (repo.data / "documents" / "us-zz" / "zz-ai-guidance.yaml").read_text()
    assert "revisions" not in doc


def test_since_gathers_earlier_runs(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY))
    go(repo, web, date(2026, 10, 1))
    web.set(ORG_URL, page(BASE_BODY + "<p>Update one.</p>"))
    go(repo, web, date(2026, 10, 8))
    report, _ = go(repo, web, date(2026, 10, 15), since="2026-10-05")
    assert [c["observed"] for c in report.changes] == ["2026-10-08"]
    report, _ = go(repo, web, date(2026, 10, 15))
    assert report.changes == []


def test_pdf_hashed_by_bytes_and_mapped(repo):
    write(
        repo.data / "organizations" / "us-zz-k12-agency.yaml",
        {
            "id": "us-zz-k12-agency",
            "name": "Example Department of Education",
            "classification": "k12-agency",
            "url": "https://example.gov/",
            "watch_urls": [DOC_PDF],
        },
    )
    web = FakeWeb()
    web.set(DOC_PDF, b"%PDF-1.7 version one", content_type="application/pdf")
    go(repo, web, date(2026, 10, 10))
    web.set(DOC_PDF, b"%PDF-1.7 version two", content_type="application/pdf")
    report, _ = go(repo, web, date(2026, 10, 17))
    change = report.changes[0]
    if change["hash_basis"] == "bytes":
        rev = report.proposals[0]["revision"]
        assert "sha256" not in rev and change["new_hash"] in rev["note"]


def test_find_documents_by_revision_url(repo):
    write(
        repo.data / "documents" / "us-zz" / "zz-other.yaml",
        {
            "id": "us-zz-zz-other",
            "revisions": [
                {
                    "observed": "2026-09-01",
                    "url": "https://example.gov/files/v2.pdf",
                    "sha256": "a" * 64,
                }
            ],
            "sources": [],
        },
    )
    docs = load_records(repo.data, ["document"]).of("document")
    matches = find_documents_for_url("http://example.gov/files/v2.pdf", docs)
    assert [(m.record.id, m.via) for m in matches] == [("us-zz-zz-other", "revision")]
    # Same hash as the latest revision: nothing to propose.
    change = {
        "url": "https://example.gov/files/v2.pdf",
        "observed": "2026-10-17",
        "old_hash": "b" * 64,
        "new_hash": "a" * 64,
    }
    assert propose_revisions(change, docs) == []


def test_challenge_page_with_200_keeps_previous_state(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY))
    first, _ = go(repo, web, date(2026, 10, 10))
    web.set(
        ORG_URL,
        "<html><head><title>Radware Captcha Page</title></head><body>We apologize for the "
        "inconvenience... your activity made us think that you are a bot.</body></html>",
    )
    report, _ = go(repo, web, date(2026, 10, 17))
    assert report.changes == [] and "bot challenge" in report.skipped[0]["reason"]
    # The page returns to normal: still the first hash, so no change is reported.
    web.set(ORG_URL, page(BASE_BODY))
    report, _ = go(repo, web, date(2026, 10, 18))
    assert report.changes == [] and report.unchanged[0]["sha256"] == first.baselined[0]["sha256"]


def test_robots_behind_waf_skips_host(repo):
    web = FakeWeb()
    web.set(ORG_URL, page(BASE_BODY))
    original = web.__call__

    def transport(url, headers, timeout):
        if url.endswith("/robots.txt"):
            return Response(403, {}, b"<title>Azure WAF</title>", url)
        return original(url, headers, timeout)

    report, _ = run(
        repo,
        transport=transport,
        today=date(2026, 10, 17),
        sleep=lambda s: None,
        cache_dir=repo.root / ".cache" / "watch",
    )
    assert web.page_requests() == []
    assert "robots.txt answered with a bot challenge" in report.skipped[0]["reason"]


def test_long_crawl_delay_defers_second_page():
    web = FakeWeb(robots="User-agent: *\nCrawl-delay: 900\nAllow: /\n")
    web.set("https://a.gov/1", "<p>x</p>")
    web.set("https://a.gov/2", "<p>y</p>")
    now = [0.0]

    def sleep(s):
        now[0] += s

    fetcher = Fetcher(web, sleep=sleep, clock=lambda: now[0])
    assert fetcher.get("https://a.gov/1").outcome == "ok"
    second = fetcher.get("https://a.gov/2")
    assert second.outcome == "deferred" and "Crawl-delay 900" in second.error
    assert web.page_requests() == ["https://a.gov/1"]
    assert now[0] < 5  # no long sleep


def test_short_crawl_delay_is_honored_between_pages():
    web = FakeWeb(robots="User-agent: *\nCrawl-delay: 5\n")
    web.set("https://a.gov/1", "<p>x</p>")
    web.set("https://a.gov/2", "<p>y</p>")
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    fetcher = Fetcher(web, sleep=sleep, clock=lambda: now[0])
    fetcher.get("https://a.gov/1")
    fetcher.get("https://a.gov/2")
    assert sleeps == [1.0, 5.0]


def test_crawl_delay_holds_across_runs(repo):
    web = FakeWeb(robots="User-agent: *\nCrawl-delay: 900\n")
    web.set(ORG_URL, page(BASE_BODY))
    t = [1_000_000.0]
    go(repo, web, date(2026, 10, 17), wall=lambda: t[0])
    t[0] += 60
    report, _ = go(repo, web, date(2026, 10, 17), wall=lambda: t[0])
    assert "Crawl-delay 900" in report.skipped[0]["reason"]
    assert web.page_requests() == [ORG_URL]
    t[0] += 900
    report, _ = go(repo, web, date(2026, 10, 18), wall=lambda: t[0])
    assert report.unchanged and web.page_requests() == [ORG_URL, ORG_URL]


def test_nextjs_page_text_comes_from_embedded_data():
    import json

    content = "<p>" + "The department supports responsible use of AI in schools. " * 5 + "</p>"
    content += '<h2>Guidance</h2><p><a href="/docs/ai-guide">AI guide</a></p>'
    data = {"props": {"pageProps": {"page": {"title": "AI", "content": content}}}}
    html = (
        "<html><head><title>AI - Dept</title></head><body><div id='__next'></div>"
        f"<script id='__NEXT_DATA__' type='application/json'>{json.dumps(data)}</script>"
        "</body></html>"
    )
    ex = extract_html(html, "https://x.gov/ai/")
    assert ex.title == "AI - Dept"
    assert "responsible use of AI" in ex.text and "## Guidance" in ex.text
    assert [link.url for link in ex.links] == ["https://x.gov/docs/ai-guide"]


def test_empty_page_is_an_error_not_a_baseline(repo):
    web = FakeWeb()
    web.set(ORG_URL, "<html><body><div id='app'></div><script>render()</script></body></html>")
    report, _ = go(repo, web, date(2026, 10, 17))
    assert not report.baselined and "no text extracted" in report.errors[0]["reason"]


def test_ai_link_ignores_query_string():
    login = (
        "https://x.org/wp-login.php?redirect_to=https%3A%2F%2Fx.org%2Fartificial-intelligence%2F"
    )
    assert not looks_like_ai_link(login, "logged in")


def test_main_with_skip_link_target_id_is_kept():
    html = (
        "<html><body><a class='skip-link screen-reader-text' href='#m'>Skip</a>"
        "<main id='wp--skip-link--target'><h1>AI</h1><p>"
        + "Guidance for schools on artificial intelligence. " * 6
        + "</p></main><div class='breadcrumbs-wrap'>Home / AI</div></body></html>"
    )
    ex = extract_html(html, "https://x.gov/")
    assert "Guidance for schools" in ex.text and "Skip" not in ex.text
