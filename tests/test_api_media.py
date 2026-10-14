"""Uploaded files served through the API (Phase 6.2, docs/ADMIN_CMS_CONTRACT.md)."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from src.core.config import settings
from src.core.dependencies import get_content_service, get_storage
from src.main import app
from src.schemas.content_schema import is_issued_key
from src.services.media_service import MediaService
from src.services.storage_service import S3Storage, StoredObject
from tests.conftest import login_as, make_user
from tests.content_fakes import ContentFakes, FakeStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from seed_content import load_seed, seed_content

MEDIA = "/api/v1/media"
CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}
API_MEDIA = "https://api.aashish-regmi.com.np/api/v1/media"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
LAST_MODIFIED = "Wed, 14 Oct 2026 09:30:00 GMT"


def image_key(ext: str = "png") -> str:
    return f"{settings.s3_upload_prefix}projects/{uuid.uuid4()}.{ext}"


def resume_key() -> str:
    return f"{settings.s3_upload_prefix}resume/{uuid.uuid4()}.pdf"


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def media(client, storage):
    app.dependency_overrides[get_storage] = lambda: storage
    return client


def put(storage: FakeStorage, key: str, data: bytes, content_type: str) -> None:
    asyncio.run(storage.put_object(key, data, content_type, "public, max-age=31536000, immutable"))


# --- Serving ----------------------------------------------------------------------------------


def test_serves_an_uploaded_image_with_cache_headers(media, storage):
    key = image_key()
    put(storage, key, PNG, "image/png")

    response = media.get(f"{MEDIA}/{key}")

    assert response.status_code == 200
    assert response.content == PNG
    headers = response.headers
    assert headers["content-type"] == "image/png"
    assert headers["content-length"] == str(len(PNG))
    assert headers["etag"] == storage.objects[key].etag
    assert headers["last-modified"] == LAST_MODIFIED
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    assert headers["content-disposition"] == "inline"
    assert headers["x-content-type-options"] == "nosniff"
    # The résumé page embeds the PDF in an <iframe>/<object>: never forbid framing.
    assert "x-frame-options" not in headers
    assert storage.fetched == [key]


def test_serves_a_resume_pdf_inline(media, storage):
    key = resume_key()
    put(storage, key, PDF, "application/pdf")

    response = media.get(f"{MEDIA}/{key}")

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "inline"


@pytest.mark.parametrize(
    ("ext", "expected"),
    [("jpg", "image/jpeg"), ("webp", "image/webp"), ("avif", "image/avif")],
)
def test_content_type_follows_the_issued_extension(media, storage, ext, expected):
    key = image_key(ext)
    # Even if the stored metadata were tampered with, the key's extension decides the type.
    put(storage, key, b"bytes", "text/html")

    response = media.get(f"{MEDIA}/{key}")

    assert response.headers["content-type"] == expected


def test_head_returns_headers_without_a_body(media, storage):
    key = image_key()
    put(storage, key, PNG, "image/png")

    response = media.head(f"{MEDIA}/{key}")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(PNG))
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == storage.objects[key].etag


@pytest.mark.parametrize("if_none_match", ["{etag}", "W/{etag}", '"other", {etag}', "*"], ids=str)
def test_if_none_match_returns_304(media, storage, if_none_match):
    key = image_key()
    put(storage, key, PNG, "image/png")
    etag = storage.objects[key].etag

    response = media.get(
        f"{MEDIA}/{key}", headers={"If-None-Match": if_none_match.format(etag=etag)}
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_stale_etag_returns_the_file(media, storage):
    key = image_key()
    put(storage, key, PNG, "image/png")

    response = media.get(f"{MEDIA}/{key}", headers={"If-None-Match": '"stale"'})

    assert response.status_code == 200
    assert response.content == PNG


# --- Only issued keys -------------------------------------------------------------------------

UUID = "0b4b5f0e-8f3a-4c9e-9a1d-2f6c7e8d9a0b"
PREFIX = settings.s3_upload_prefix


@pytest.mark.parametrize(
    "key",
    [
        f"other/projects/{UUID}.png",  # outside the prefix
        f"projects/{UUID}.png",  # prefix missing
        f"{PREFIX}avatars/{UUID}.png",  # wrong folder
        f"{PREFIX}resume/{UUID}.png",  # image in the résumé folder
        f"{PREFIX}projects/{UUID}.pdf",  # PDF in the images folder
        f"{PREFIX}projects/not-a-uuid.png",  # bad uuid
        f"{PREFIX}projects/{UUID.upper()}.png",  # we only issue lowercase
        f"{PREFIX}projects/{UUID}.gif",  # other extension
        f"{PREFIX}projects/{UUID}.html",
        f"{PREFIX}projects/{UUID}.png.html",
        f"{PREFIX}projects/{UUID}",
        f"{PREFIX}projects/{UUID}.png/extra",
        f"{PREFIX}projects/sub/{UUID}.png",
        f"{PREFIX}projects/",
        f"{PREFIX}",
        ".env",
    ],
)
def test_other_keys_are_404_and_never_reach_storage(media, storage, key):
    put(storage, key, b"secret", "text/plain")  # even if such an object existed
    storage.fetched.clear()

    response = media.get(f"{MEDIA}/{key}")

    assert response.status_code == 404
    assert response.json()["errors"] == {"code": "media_not_found"}
    assert storage.fetched == []


@pytest.mark.parametrize(
    "path",
    [
        f"{PREFIX}projects/%2e%2e/%2e%2e/.env",  # encoded traversal
        f"{PREFIX}projects/..%2f..%2f{UUID}.png",
        f"{PREFIX.rstrip('/')}%2Fprojects%2F{UUID}.png",  # encoded slashes (decode to a valid key)
        f"{PREFIX}projects%2F{UUID}.png",
        f"{PREFIX}projects/{UUID}%2Epng",
        f"{PREFIX}projects/{UUID}.png%00",
        f"{PREFIX}projects/{UUID}.png%0A",  # Starlette's route regex would drop the "\n"
    ],
)
def test_encoded_paths_are_404_and_never_reach_storage(media, storage, path):
    put(storage, f"{PREFIX}projects/{UUID}.png", PNG, "image/png")

    response = media.get(f"{MEDIA}/{path}")

    assert response.status_code == 404
    assert storage.fetched == []


@pytest.mark.parametrize(
    "key",
    [
        f"{PREFIX}projects/../{UUID}.png",
        f"{PREFIX}projects/../../{PREFIX}projects/{UUID}.png",
        f"../{PREFIX}projects/{UUID}.png",
        f"/{PREFIX}projects/{UUID}.png",
        f"{PREFIX}projects/{UUID}.png\n",
        f"{PREFIX}projects/{UUID}.png\x00",
        f"{PREFIX}projects//{UUID}.png",
    ],
)
def test_traversal_and_control_characters_are_not_issued_keys(key):
    # httpx normalises "../" before sending, so check the validator (and the service) directly.
    storage = FakeStorage()

    assert not is_issued_key(key)
    with pytest.raises(Exception) as caught:
        asyncio.run(MediaService(storage).get(key))
    assert getattr(caught.value, "status_code", None) == 404
    assert storage.fetched == []


def test_issued_keys_are_recognised():
    assert is_issued_key(f"{PREFIX}projects/{UUID}.png")
    assert is_issued_key(f"{PREFIX}projects/{UUID}.jpg")
    assert is_issued_key(f"{PREFIX}resume/{UUID}.pdf")


def test_missing_object_is_404(media, storage):
    key = image_key()

    response = media.get(f"{MEDIA}/{key}")

    assert response.status_code == 404
    assert response.json()["errors"] == {"code": "media_not_found"}
    assert storage.fetched == [key]


def test_503_when_uploads_are_disabled(client):
    app.dependency_overrides[get_storage] = lambda: None

    response = client.get(f"{MEDIA}/{image_key()}")

    assert response.status_code == 503
    assert response.json()["errors"] == {"code": "uploads_disabled"}


def test_invalid_key_is_404_even_when_uploads_are_disabled(client):
    app.dependency_overrides[get_storage] = lambda: None

    assert client.get(f"{MEDIA}/.env").status_code == 404


def test_storage_failure_is_502(media, storage):
    storage.get_error = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    response = media.get(f"{MEDIA}/{image_key()}")

    assert response.status_code == 502
    assert response.json()["errors"] == {"code": "storage_error"}


def test_media_is_rate_limited(media):
    statuses = [media.get(f"{MEDIA}/nope.png").status_code for _ in range(241)]

    assert statuses[:240] == [404] * 240
    assert statuses[240] == 429


def test_media_is_documented_once_in_openapi():
    paths = app.openapi()["paths"]

    assert list(paths[f"{MEDIA}/{{key}}"]) == ["get"]


# --- S3Storage.get_object (botocore stubbed, no network) --------------------------------------


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def read(self) -> bytes:
        return self.data

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def s3_with(client: FakeS3Client) -> S3Storage:
    storage = S3Storage.__new__(S3Storage)
    storage.client = client
    storage.bucket = "bucket"
    return storage


def test_s3_get_object_reads_the_whole_body():
    body = FakeBody(PNG)
    modified = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    client = FakeS3Client(
        {"Body": body, "ContentType": "image/png", "ETag": '"abc"', "LastModified": modified}
    )

    stored = asyncio.run(s3_with(client).get_object("k.png"))

    assert stored == StoredObject(
        body=PNG,
        content_type="image/png",
        content_length=len(PNG),
        etag='"abc"',
        last_modified=modified,
    )
    assert body.closed
    assert client.calls == [{"Bucket": "bucket", "Key": "k.png"}]


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_s3_get_object_missing_is_none(code):
    client = FakeS3Client(error=ClientError({"Error": {"Code": code}}, "GetObject"))

    assert asyncio.run(s3_with(client).get_object("k.png")) is None


def test_s3_get_object_other_errors_raise():
    client = FakeS3Client(error=ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject"))

    with pytest.raises(ClientError):
        asyncio.run(s3_with(client).get_object("k.png"))


# --- Public URLs with S3_PUBLIC_BASE_URL=<API>/api/v1/media -----------------------------------


@pytest.fixture
def via_api(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_base_url", API_MEDIA)


@pytest.fixture
def admin(client, fake_repo, storage, via_api):
    fakes = ContentFakes(storage)
    asyncio.run(seed_content(fakes.service(), load_seed()))
    app.dependency_overrides[get_content_service] = fakes.service
    app.dependency_overrides[get_storage] = lambda: storage
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    return client


def media_path(url: str) -> str:
    assert url.startswith(f"{API_MEDIA}/"), url
    return url.removeprefix("https://api.aashish-regmi.com.np")


def test_uploaded_image_url_points_at_this_endpoint_and_serves(admin, storage):
    upload = admin.post(
        "/api/v1/admin/uploads/file",
        data={"purpose": "project_image"},
        files={"file": ("shot.png", PNG, "image/png")},
        headers=CSRF,
    ).json()

    assert upload["public_url"] == f"{API_MEDIA}/{upload['key']}"
    served = admin.get(media_path(upload["public_url"]))
    assert served.status_code == 200
    assert served.content == PNG


def test_content_api_image_url_points_at_this_endpoint(admin, storage):
    key = image_key()
    put(storage, key, PNG, "image/png")
    project = {
        "slug": "media-demo",
        "name": "Media demo",
        "category": "Web application",
        "summary": "Served via the API.",
        "stack": ["FastAPI"],
        "repo_url": None,
        "demo_url": None,
        "case_study": None,
        "published": True,
        "image": {"key": key, "alt": "Screenshot", "width": 1200, "height": 750},
    }
    assert admin.post("/api/v1/admin/projects", json=project, headers=CSRF).status_code == 201

    image = admin.get("/api/v1/content").json()["projects"][-1]["image"]

    assert image["url"] == f"{API_MEDIA}/{key}"
    assert admin.get(media_path(image["url"])).content == PNG


def upload_resume(client) -> dict:
    return client.post(
        "/api/v1/admin/uploads/file",
        data={"purpose": "resume"},
        files={"file": ("cv.pdf", PDF, "application/pdf")},
        headers=CSRF,
    ).json()


def save_resume(client, url: str):
    profile = load_seed()["profile"].model_dump(mode="json")
    profile["resume_url"] = url
    response = client.put("/api/v1/admin/profile", json=profile, headers=CSRF)
    assert response.status_code == 200, response.text


def test_uploaded_resume_is_served_downloaded_and_cleaned_up(admin, storage):
    first = upload_resume(admin)
    assert first["public_url"] == f"{API_MEDIA}/{first['key']}"
    save_resume(admin, first["public_url"])

    # The public page links (and embeds) the résumé through this endpoint.
    resume_url = admin.get("/api/v1/content").json()["profile"]["resume_url"]
    assert resume_url == first["public_url"]
    served = admin.get(media_path(resume_url))
    assert served.content == PDF
    assert served.headers["content-type"] == "application/pdf"

    # The download button is still a presigned S3 GET with an attachment disposition.
    download = admin.get("/api/v1/resume/download", follow_redirects=False)
    assert download.status_code == 302
    assert download.headers["location"] == (
        f"https://fake-bucket.s3.amazonaws.com/{first['key']}?signed=1"
    )

    # Replacing it deletes the old PDF: the API URL is recognised as our own upload.
    second = upload_resume(admin)
    save_resume(admin, second["public_url"])
    assert storage.deleted == [first["key"]]
