"""Résumé download endpoint and /resume sitemap entry (Phase 6, docs/ADMIN_CMS_CONTRACT.md)."""

import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.core.config import Settings, settings
from src.core.dependencies import get_content_service
from src.main import app
from src.services.content_service import resume_filename
from src.services.storage_service import S3Storage
from tests.conftest import login_as, make_user
from tests.content_fakes import ContentFakes, FakeStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from seed_content import load_seed, seed_content

CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}
CDN = "https://cdn.example.com"
SITE = "https://aashish-regmi.com.np"
DOWNLOAD = "/api/v1/resume/download"


@pytest.fixture
def cdn(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_base_url", CDN)
    return f"{CDN}/{settings.s3_upload_prefix}resume/"


def make_client(client, storage):
    fakes = ContentFakes(storage)
    asyncio.run(seed_content(fakes.service(), load_seed()))
    app.dependency_overrides[get_content_service] = fakes.service
    return client, fakes


def set_resume(client, fake_repo, url):
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    profile = load_seed()["profile"].model_dump(mode="json")
    profile["resume_url"] = url
    assert client.put("/api/v1/admin/profile", json=profile, headers=CSRF).status_code == 200
    client.cookies.clear()


def test_404_when_no_resume(client):
    client, _ = make_client(client, FakeStorage())

    response = client.get(DOWNLOAD, follow_redirects=False)

    assert response.status_code == 404
    assert response.json()["errors"] == {"code": "resume_unavailable"}


def test_404_when_content_not_seeded(client):
    app.dependency_overrides[get_content_service] = ContentFakes(FakeStorage()).service

    assert client.get(DOWNLOAD, follow_redirects=False).status_code == 404


def test_own_upload_redirects_to_presigned_attachment(client, fake_repo, cdn):
    client, fakes = make_client(client, FakeStorage())
    key = f"{settings.s3_upload_prefix}resume/abc.pdf"
    set_resume(client, fake_repo, f"{CDN}/{key}")

    response = client.get(DOWNLOAD, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == f"https://fake-bucket.s3.amazonaws.com/{key}?signed=1"
    assert response.headers["cache-control"] == "no-store"
    assert fakes.storage.downloads == [
        {"key": key, "filename": "Aashish-Regmi-Resume.pdf", "content_type": "application/pdf"}
    ]


def test_external_url_redirects_unchanged(client, fake_repo, cdn):
    client, fakes = make_client(client, FakeStorage())
    external = "https://drive.example.com/aashish-cv.pdf"
    set_resume(client, fake_repo, external)

    response = client.get(DOWNLOAD, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == external
    assert fakes.storage.downloads == []


def test_own_url_without_s3_falls_back_to_public_url(client, fake_repo, cdn):
    client, _ = make_client(client, None)
    url = f"{CDN}/{settings.s3_upload_prefix}resume/abc.pdf"
    set_resume(client, fake_repo, url)

    response = client.get(DOWNLOAD, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == url


def test_download_is_rate_limited(client):
    client, _ = make_client(client, FakeStorage())

    statuses = [client.get(DOWNLOAD, follow_redirects=False).status_code for _ in range(31)]

    assert statuses[:30] == [404] * 30
    assert statuses[30] == 429


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Aashish Regmi", "Aashish-Regmi-Resume.pdf"),
        ("  Zoë  O'Brien / Ltd ", "Zoe-O-Brien-Ltd-Resume.pdf"),
        ("आशिष", "Resume.pdf"),
        ("", "Resume.pdf"),
        (None, "Resume.pdf"),
        ('a"b\\c', "a-b-c-Resume.pdf"),
    ],
)
def test_resume_filename_is_ascii_safe(name, expected):
    assert resume_filename(name) == expected


def test_s3_presign_get_sets_attachment_disposition():
    storage = S3Storage(
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://u:p@localhost/db",
            smtp_host="x",
            smtp_user="x",
            smtp_password="x",
            super_admin_email="x@example.com",
            portfolio_url="https://example.com",
            github_url="https://github.com/x",
            linkedin_url="https://linkedin.com/in/x",
            jwt_secret_key="x" * 48,
            aws_access_key_id="AKIAEXAMPLEDUMMY0000",
            aws_secret_access_key="dummy-secret",
            aws_region="ap-south-1",
            s3_bucket_name="dummy-bucket",
        )
    )

    url = storage.presign_get(
        "portfolio/resume/a.pdf", "Aashish-Regmi-Resume.pdf", "application/pdf"
    )

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "dummy-bucket.s3.ap-south-1.amazonaws.com"
    assert parts.path == "/portfolio/resume/a.pdf"
    assert query["response-content-disposition"] == [
        'attachment; filename="Aashish-Regmi-Resume.pdf"'
    ]
    assert query["response-content-type"] == ["application/pdf"]
    assert query["X-Amz-Expires"] == ["300"]


# --- Sitemap ------------------------------------------------------------------------------


def sitemap_locations(client):
    from xml.etree import ElementTree

    root = ElementTree.fromstring(client.get("/sitemap.xml").content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [loc.text for loc in root.findall("s:url/s:loc", ns)]


def test_sitemap_omits_resume_when_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "portfolio_url", f"{SITE}/")
    client, _ = make_client(client, FakeStorage())

    assert f"{SITE}/resume" not in sitemap_locations(client)


def test_sitemap_lists_resume_when_set(client, fake_repo, monkeypatch):
    monkeypatch.setattr(settings, "portfolio_url", f"{SITE}/")
    client, _ = make_client(client, FakeStorage())
    set_resume(client, fake_repo, "https://drive.example.com/cv.pdf")

    locations = sitemap_locations(client)

    assert locations.index(f"{SITE}/resume") == locations.index(f"{SITE}/contact") + 1
