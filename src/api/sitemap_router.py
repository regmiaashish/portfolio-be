"""GET /sitemap.xml: the public site's pages for search engines (Phase 5).

Served by the API because the project list lives in the database. The frontend's robots.txt
points here (cross-host sitemaps listed in robots.txt are accepted by search engines).
"""

from datetime import UTC, datetime
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, Request, Response

from src.core.config import settings
from src.core.dependencies import get_content_service
from src.core.rate_limit import SITEMAP_LIMIT, limiter
from src.services.content_service import ContentService

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
CACHE_CONTROL = "public, max-age=3600"

# The frontend's top-level pages (Phase 6). Pages whose text comes from the admin content share
# its last-modified date; /pitch is an interactive demo with no content date.
STATIC_PAGES: tuple[tuple[str, bool], ...] = (
    ("work", True),
    ("pitch", False),
    ("about", True),
    ("contact", True),
)

router = APIRouter(tags=["seo"])


class XMLResponse(Response):
    media_type = "application/xml"


def _lastmod(stamp: datetime | None) -> str | None:
    return stamp.astimezone(UTC).date().isoformat() if stamp else None


def build_sitemap(
    base_url: str,
    home_lastmod: datetime | None,
    projects: list[tuple[str, datetime | None]],
    include_resume: bool = False,
) -> bytes:
    base = base_url.rstrip("/")
    urlset = ElementTree.Element("urlset", xmlns=SITEMAP_NAMESPACE)

    def add(location: str, stamp: datetime | None) -> None:
        url = ElementTree.SubElement(urlset, "url")
        ElementTree.SubElement(url, "loc").text = location
        if lastmod := _lastmod(stamp):
            ElementTree.SubElement(url, "lastmod").text = lastmod

    add(f"{base}/", home_lastmod)
    for path, uses_content_date in STATIC_PAGES:
        add(f"{base}/{path}", home_lastmod if uses_content_date else None)
    if include_resume:
        add(f"{base}/resume", home_lastmod)
    for slug, stamp in projects:
        add(f"{base}/work/{quote(slug, safe='')}", stamp)

    # ElementTree escapes text; the declaration is written by hand for the conventional form.
    body = ElementTree.tostring(urlset, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'.encode()


@router.get(
    "/sitemap.xml",
    response_class=XMLResponse,
    responses={200: {"description": "Sitemap (sitemaps.org 0.9 urlset)"}},
)
@limiter.limit(SITEMAP_LIMIT)
async def sitemap(
    request: Request, service: ContentService = Depends(get_content_service)
) -> XMLResponse:
    """Home, the top-level pages, /resume when a résumé is set, and every published project."""
    home, projects = await service.sitemap_entries()
    include_resume = await service.has_resume()
    return XMLResponse(
        content=build_sitemap(settings.portfolio_url, home, projects, include_resume),
        headers={"Cache-Control": CACHE_CONTROL},
    )
