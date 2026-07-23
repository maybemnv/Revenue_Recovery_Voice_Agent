"""Fetch a practice website and strip it to text.

Hard 60-second budget for the whole site. Partial results are fine and expected:
a practice with no /insurance page is normal, and the rep fills that gap at the
review gate. Failing the whole clone because one page 404s is not.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

TOTAL_BUDGET_SECONDS = 60.0
PER_PAGE_TIMEOUT = 12.0
MAX_PAGES = 6
MAX_CHARS_PER_PAGE = 20_000

USER_AGENT = "Mozilla/5.0 (compatible; DentalDemoRig/1.0; +sales-demo)"

# Ranked by how much profile signal each page carries. Home is never skipped.
CANDIDATE_PATHS = [
    "",
    "/services",
    "/insurance",
    "/contact",
    "/about",
    "/our-team",
    "/new-patients",
    "/hours",
    "/dental-services",
    "/insurance-and-financing",
    "/payment-options",
    "/meet-the-doctor",
]

_LINK_HINTS = re.compile(
    r"(service|insurance|contact|about|team|doctor|staff|hour|new.?patient|financ|payment)",
    re.I,
)


@dataclass
class ScrapedPage:
    url: str
    text: str
    html: str = ""


@dataclass
class ScrapeResult:
    base_url: str
    pages: list[ScrapedPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """One readable page is enough to attempt extraction."""
        return bool(self.pages)

    def combined_text(self, max_chars: int = 90_000) -> str:
        parts = [f"### PAGE: {p.url}\n{p.text}" for p in self.pages]
        blob = "\n\n".join(parts)
        return blob[:max_chars]


def _normalize_base(url: str) -> str:
    if not urlparse(url).scheme:
        url = "https://" + url
    return url.rstrip("/")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)[:MAX_CHARS_PER_PAGE]


def discover_links(home_html: str, base_url: str) -> list[str]:
    """Site-internal links whose href or anchor text smells like a profile page.

    Guessing paths misses practices on Wix/Squarespace with non-obvious routes,
    so we read the home page's own navigation rather than assuming a layout.
    """
    soup = BeautifulSoup(home_html, "html.parser")
    host = urlparse(base_url).netloc
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        absolute = urljoin(base_url + "/", href)
        parsed = urlparse(absolute)
        if parsed.netloc != host:
            continue
        clean = absolute.split("#")[0].rstrip("/")
        if clean == base_url or clean in found:
            continue
        if _LINK_HINTS.search(parsed.path) or _LINK_HINTS.search(anchor.get_text(" ")):
            found.append(clean)
    return found


def extract_accent_color(html: str) -> str | None:
    """Best-effort primary colour so the demo page wears the prospect's brand.

    Takes the most frequent non-neutral hex in the page's inline styles. It is
    wrong sometimes, which is exactly why the rep reviews the YAML.
    """
    counts: dict[str, int] = {}
    for match in re.finditer(r"#([0-9a-fA-F]{6})\b", html):
        hex_value = "#" + match.group(1).lower()
        r, g, b = (int(hex_value[i : i + 2], 16) for i in (1, 3, 5))
        # Skip near-greys and the extremes - they are chrome, not brand.
        if max(r, g, b) - min(r, g, b) < 30 or sum((r, g, b)) > 690 or sum((r, g, b)) < 60:
            continue
        counts[hex_value] = counts.get(hex_value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def scrape_site(url: str, *, budget_seconds: float = TOTAL_BUDGET_SECONDS) -> ScrapeResult:
    base = _normalize_base(url)
    result = ScrapeResult(base_url=base)
    deadline = time.monotonic() + budget_seconds

    with httpx.Client(
        follow_redirects=True,
        timeout=PER_PAGE_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        home = _fetch(client, base, result)
        if home is None:
            return result
        result.pages.append(home)

        targets = discover_links(home.html, base)
        for path in CANDIDATE_PATHS[1:]:
            candidate = base + path
            if candidate not in targets:
                targets.append(candidate)

        seen = {base}
        for target in targets:
            if len(result.pages) >= MAX_PAGES:
                result.truncated = True
                break
            if time.monotonic() >= deadline:
                result.truncated = True
                result.errors.append("scrape budget exhausted")
                break
            if target in seen:
                continue
            seen.add(target)
            page = _fetch(client, target, result, quiet_404=True)
            if page is not None:
                result.pages.append(page)

    return result


def _fetch(
    client: httpx.Client, url: str, result: ScrapeResult, *, quiet_404: bool = False
) -> ScrapedPage | None:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        result.errors.append(f"{url}: {type(exc).__name__}")
        return None
    if response.status_code >= 400:
        if not quiet_404:
            result.errors.append(f"{url}: HTTP {response.status_code}")
        return None
    if "html" not in response.headers.get("content-type", "text/html"):
        return None
    return ScrapedPage(url=str(response.url), text=html_to_text(response.text), html=response.text)
