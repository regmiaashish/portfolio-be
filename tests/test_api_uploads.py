"""Uploads through the API (Phase 6.1, docs/ADMIN_CMS_CONTRACT.md)."""

import asyncio
import re

import pytest
from botocore.exceptions import ClientError

from src.core.config import settings
from src.core.dependencies import get_content_service
from src.main import app
from src.services.content_service import UPLOAD_CACHE_CONTROL
from src.services.upload_files import UploadTooLargeError, detect_content_type, read_limited
from tests.conftest import login_as, make_user
from tests.content_fakes import ContentFakes, FakeStorage

ADMIN = "/api/v1/admin"
URL = f"{ADMIN}/uploads/file"
CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
AVIF = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 16
AVIF_COMPAT = b"\x00\x00\x00\x20ftypmif1\x00\x00\x00\x00mif1avifmiafMA1B" + b"\x00" * 16
HTML = b"<!doctype html><html><script>alert(1)</script></html>"


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def admin(client, fake_repo, storage):
    app.dependency_overrides[get_content_service] = ContentFakes(storage).service
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    return client


def upload(client, content: bytes, name: str = "file.bin", purpose: str | None = None, **kw):
    data = {"purpose": purpose} if purpose is not None else {}
    return client.post(
        URL,
        data=data,
        files={"file": (name, content, kw.pop("declared", "application/octet-stream"))},
        headers=kw.pop("headers", CSRF),
    )


def error_locs(response) -> list[list]:
    return [e["loc"] for e in response.json()["errors"]]


# --- Content sniffing (pure) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (WEBP, "image/webp"),
        (AVIF, "image/avif"),
        (AVIF_COMPAT, "image/avif"),
        (HTML, None),
        (b"GIF89a" + b"\x00" * 20, None),
        (b"", None),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
        (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", None),
    ],
)
def test_detect_content_type(head, expected):
    assert detect_content_type(head[:64]) == expected


class CountingStream:
    """An endless upload that records how much was read from it."""

    def __init__(self):
        self.read_bytes = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_bytes += size
        return b"x" * size


def test_read_limited_stops_as_soon_as_the_limit_is_passed():
    stream = CountingStream()

    with pytest.raises(UploadTooLargeError):
        asyncio.run(read_limited(stream, max_bytes=100_000, chunk_size=4096))

    # Stopped within one chunk of the limit instead of reading forever.
    assert 100_000 < stream.read_bytes <= 100_000 + 4096


def test_read_limited_returns_small_files_whole():
    class Once:
        def __init__(self):
            self.chunks = [b"abc", b"def", b""]

        async def read(self, size: int = -1) -> bytes:
            return self.chunks.pop(0)

    assert asyncio.run(read_limited(Once(), max_bytes=10)) == b"abcdef"


# --- Endpoint ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "content_type", "ext"),
    [
        (PNG, "image/png", "png"),
        (JPEG, "image/jpeg", "jpg"),
        (WEBP, "image/webp", "webp"),
        (AVIF, "image/avif", "avif"),
    ],
)
def test_project_images_are_stored(admin, storage, content, content_type, ext):
    response = upload(admin, content, name="screenshot.dat")

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"key", "public_url", "content_type", "size"}
    assert re.fullmatch(
        rf"{re.escape(settings.s3_upload_prefix)}projects/[0-9a-f-]{{36}}\.{ext}", body["key"]
    )
    assert body["content_type"] == content_type
    assert body["size"] == len(content)
    assert body["public_url"].endswith(body["key"])
    assert storage.put == [
        {
            "key": body["key"],
            "data": content,
            "content_type": content_type,
            "cache_control": UPLOAD_CACHE_CONTROL,
        }
    ]


def test_resume_pdf_is_stored_in_the_resume_folder(admin, storage):
    response = upload(admin, PDF, name="cv.pdf", purpose="resume")

    assert response.status_code == 201, response.text
    key = response.json()["key"]
    assert key.startswith(f"{settings.s3_upload_prefix}resume/") and key.endswith(".pdf")
    assert storage.put[0]["content_type"] == "application/pdf"


def test_missing_purpose_defaults_to_project_image(admin, storage):
    response = upload(admin, PNG)

    assert response.status_code == 201
    assert "/projects/" in response.json()["key"]


@pytest.mark.parametrize(
    ("content", "name", "declared", "purpose"),
    [
        (PDF, "photo.png", "image/png", "project_image"),  # a PDF renamed .png
        (HTML, "cv.pdf", "application/pdf", "resume"),  # HTML renamed .pdf
        (PNG, "cv.pdf", "application/pdf", "resume"),  # an image as a résumé
        (HTML, "page.png", "image/png", "project_image"),
    ],
)
def test_contents_decide_the_type_not_the_name(admin, storage, content, name, declared, purpose):
    response = upload(admin, content, name=name, purpose=purpose, declared=declared)

    assert response.status_code == 422
    assert error_locs(response) == [["body", "file"]]
    assert storage.put == []


def test_empty_file_is_rejected(admin, storage):
    response = upload(admin, b"")

    assert response.status_code == 422
    assert "empty" in response.json()["errors"][0]["msg"].lower()
    assert storage.put == []


def test_file_over_the_limit_is_rejected(admin, storage, monkeypatch):
    monkeypatch.setattr(settings, "s3_max_upload_mb", 1)
    too_big = PNG + b"\x00" * (1024 * 1024)

    response = upload(admin, too_big)

    assert response.status_code == 422
    assert error_locs(response) == [["body", "file"]]
    assert "at most 1 MB" in response.json()["errors"][0]["msg"]
    assert storage.put == []


def test_oversized_content_length_is_refused_before_reading(admin, storage, monkeypatch):
    monkeypatch.setattr(settings, "s3_max_upload_mb", 1)
    headers = {**CSRF, "Content-Length": str(50 * 1024 * 1024)}

    response = admin.post(URL, content=b"x", headers=headers)

    assert response.status_code == 422
    assert storage.put == []


def test_unknown_purpose_is_rejected(admin, storage):
    response = upload(admin, PNG, purpose="avatar")

    assert response.status_code == 422
    assert error_locs(response) == [["body", "purpose"]]


def test_missing_file_is_rejected(admin, storage):
    response = admin.post(URL, data={"purpose": "resume"}, headers=CSRF)

    assert response.status_code == 422
    assert error_locs(response) == [["body", "file"]]


def test_s3_failure_is_502_without_leaking_details(admin, storage, caplog):
    storage.put_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "User arn:aws:iam::123:user/secret-user"}},
        "PutObject",
    )

    response = upload(admin, PNG)

    assert response.status_code == 502
    body = response.json()
    assert body["errors"] == {"code": "storage_error"}
    assert "AccessDenied" not in response.text
    assert "secret-user" not in response.text


def test_uploads_disabled_without_a_bucket(client, fake_repo):
    app.dependency_overrides[get_content_service] = ContentFakes(storage=None).service
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))

    response = upload(client, PNG)

    assert response.status_code == 503
    assert response.json()["errors"] == {"code": "uploads_disabled"}


def test_anonymous_is_401(client, storage):
    app.dependency_overrides[get_content_service] = ContentFakes(storage).service

    assert upload(client, PNG).status_code == 401
    assert storage.put == []


def test_non_superuser_is_403(client, fake_repo, storage):
    app.dependency_overrides[get_content_service] = ContentFakes(storage).service
    login_as(client, make_user(fake_repo, email="user@example.com", is_superuser=False))

    assert upload(client, PNG).status_code == 403
    assert storage.put == []


def test_needs_csrf_header_and_allowed_origin(admin, storage):
    no_header = upload(admin, PNG, headers={"Origin": "http://localhost:5173"})
    bad_origin = upload(
        admin, PNG, headers={"X-Requested-With": "portfolio-admin", "Origin": "https://evil.test"}
    )

    assert no_header.status_code == 403
    assert no_header.json()["errors"] == {"code": "csrf"}
    assert bad_origin.status_code == 403
    assert storage.put == []


def test_upload_is_audit_logged(admin, storage):
    from loguru import logger

    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        response = upload(admin, PDF, purpose="resume")
    finally:
        logger.remove(sink)

    assert response.status_code == 201
    assert any("admin@example.com uploaded a resume file" in m for m in messages)


def test_upload_is_rate_limited(admin, storage):
    statuses = [upload(admin, PNG).status_code for _ in range(21)]

    assert statuses[:20] == [201] * 20
    assert statuses[20] == 429


def test_csrf_header_is_documented_for_swagger():
    """The CSRF header appears as an input on mutating admin routes in the OpenAPI schema."""
    from src.main import app

    spec = app.openapi()
    for path, method in (
        ("/api/v1/admin/uploads/file", "post"),
        ("/api/v1/admin/profile", "put"),
        ("/api/v1/admin/projects/{project_id}", "delete"),
    ):
        operation = spec["paths"][path][method]
        names = {(p["in"], p["name"]) for p in operation.get("parameters", [])}
        assert ("header", "X-Requested-With") in names, (path, method)
