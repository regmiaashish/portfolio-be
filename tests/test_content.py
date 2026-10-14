"""Site content and the admin API (Phase 4.5, docs/ADMIN_CMS_CONTRACT.md)."""

import asyncio
import base64
import json
import sys
import uuid
from pathlib import Path

import pytest

from src.core.config import settings
from src.core.dependencies import get_content_service
from src.main import app
from src.schemas.content_schema import project_image_key_pattern
from tests.conftest import login_as, make_user
from tests.content_fakes import ContentFakes, FakeStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from seed_content import load_seed, seed_content

CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}
ADMIN = "/api/v1/admin"
IMAGE_KEY = f"{settings.s3_upload_prefix}projects/{uuid.uuid4()}.png"


@pytest.fixture
def fakes() -> ContentFakes:
    return ContentFakes(FakeStorage())


@pytest.fixture
def seeded(fakes) -> ContentFakes:
    asyncio.run(seed_content(fakes.service(), load_seed()))
    return fakes


@pytest.fixture
def content_client(client, fakes):
    app.dependency_overrides[get_content_service] = fakes.service
    return client


@pytest.fixture
def admin_client(content_client, fake_repo):
    login_as(content_client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    return content_client


def project_body(**overrides):
    body = {
        "slug": "new-project",
        "name": "New project",
        "category": "Web application",
        "summary": "Something worth showing.",
        "stack": ["FastAPI", "PostgreSQL"],
        "repo_url": "https://github.com/example/new",
        "demo_url": None,
        "image": None,
        "case_study": None,
        "published": True,
    }
    body.update(overrides)
    return body


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    if code:
        assert body["errors"]["code"] == code
    return body


# --- Public content ------------------------------------------------------------------------


def test_public_content_has_the_contract_shape(content_client, seeded):
    response = content_client.get("/api/v1/content")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"profile", "projects", "career", "skills", "updated_at"}
    assert body["profile"]["name"] == "Aashish Regmi"
    assert body["profile"]["phone"] == {"display": "+977 9840046034", "href": "tel:+9779840046034"}
    assert [s["kind"] for s in body["profile"]["socials"]] == ["github", "linkedin", "instagram"]
    assert [p["slug"] for p in body["projects"]] == [
        "fagoon-upgrade",
        "gains-garage",
        "alliance",
        "jarvis",
        "cricket-management-system",
    ]
    assert set(body["projects"][0]) == {
        "id",
        "slug",
        "name",
        "category",
        "summary",
        "stack",
        "repo_url",
        "demo_url",
        "image",
        "case_study",
    }
    assert [c["period"] for c in body["career"]] == ["Now", "2026", "2024"]
    assert [s["title"] for s in body["skills"]] == ["Backend", "AI integration", "Infrastructure"]


def test_public_content_hides_unpublished_projects(content_client, seeded):
    hidden = next(iter(seeded.projects.items.values()))
    hidden.published = False

    slugs = [p["slug"] for p in content_client.get("/api/v1/content").json()["projects"]]

    assert hidden.slug not in slugs
    assert len(slugs) == 4


def test_public_content_cache_headers_and_etag(content_client, seeded):
    first = content_client.get("/api/v1/content")

    assert first.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=600"
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and not etag.startswith("W/")

    not_modified = content_client.get("/api/v1/content", headers={"If-None-Match": etag})
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["etag"] == etag

    stale = content_client.get("/api/v1/content", headers={"If-None-Match": '"something-else"'})
    assert stale.status_code == 200


def test_etag_changes_when_content_changes(content_client, seeded):
    before = content_client.get("/api/v1/content").headers["etag"]
    seeded.profiles.profile.tagline = "A new tagline."

    after = content_client.get("/api/v1/content")

    assert after.headers["etag"] != before
    assert after.json()["profile"]["tagline"] == "A new tagline."


def test_public_content_503_when_not_seeded(content_client):
    assert_error(content_client.get("/api/v1/content"), 503, "content_unavailable")


def test_public_content_rate_limited(content_client, seeded):
    statuses = [content_client.get("/api/v1/content").status_code for _ in range(61)]

    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


def test_public_image_url_uses_the_public_base(content_client, seeded, monkeypatch):
    monkeypatch.setattr(settings, "s3_public_base_url", "https://cdn.example.com/")
    project = next(iter(seeded.projects.items.values()))
    project.image_key, project.image_alt = IMAGE_KEY, "Screenshot"
    project.image_width, project.image_height = 1200, 750

    image = content_client.get("/api/v1/content").json()["projects"][0]["image"]

    assert image == {
        "url": f"https://cdn.example.com/{IMAGE_KEY}",
        "alt": "Screenshot",
        "width": 1200,
        "height": 750,
    }


# --- Admin authentication and CSRF ---------------------------------------------------------


def test_admin_requires_login(content_client, seeded):
    assert content_client.get(f"{ADMIN}/content").status_code == 401
    assert (
        content_client.post(f"{ADMIN}/projects", json=project_body(), headers=CSRF).status_code
        == 401
    )


def test_admin_requires_superuser(content_client, seeded, fake_repo):
    login_as(content_client, make_user(fake_repo, email="user@example.com", is_superuser=False))

    assert content_client.get(f"{ADMIN}/content").status_code == 403
    assert (
        content_client.delete(f"{ADMIN}/projects/{uuid.uuid4()}", headers=CSRF).status_code == 403
    )


def test_csrf_header_required(admin_client, fakes):
    response = admin_client.post(
        f"{ADMIN}/projects", json=project_body(), headers={"Origin": CSRF["Origin"]}
    )

    assert_error(response, 403, "csrf")
    assert fakes.projects.items == {}


def test_csrf_origin_must_be_allowed(admin_client, fakes):
    for origin in ("https://evil.example", None):
        headers = {"X-Requested-With": "portfolio-admin"}
        if origin:
            headers["Origin"] = origin
        assert_error(
            admin_client.post(f"{ADMIN}/projects", json=project_body(), headers=headers),
            403,
            "csrf",
        )
    assert fakes.projects.items == {}


def test_csrf_runs_before_body_validation(admin_client):
    # A forged request never gets as far as having its body inspected.
    assert_error(admin_client.post(f"{ADMIN}/projects", json={}), 403, "csrf")


def test_reads_do_not_need_csrf(admin_client, seeded):
    assert admin_client.get(f"{ADMIN}/content").status_code == 200


# --- Admin reads ---------------------------------------------------------------------------


def test_admin_content_includes_unpublished_and_positions(admin_client, seeded):
    hidden = next(iter(seeded.projects.items.values()))
    hidden.published = False

    body = admin_client.get(f"{ADMIN}/content").json()

    assert len(body["projects"]) == 5
    assert body["projects"][0]["published"] is False
    assert [p["position"] for p in body["projects"]] == [0, 1, 2, 3, 4]
    assert all("position" in item for item in body["career"] + body["skills"])


def test_admin_content_on_an_empty_database(admin_client):
    body = admin_client.get(f"{ADMIN}/content").json()

    assert body == {"profile": None, "projects": [], "career": [], "skills": [], "updated_at": None}


# --- Profile -------------------------------------------------------------------------------


def test_update_profile(admin_client, seeded):
    profile = admin_client.get("/api/v1/content").json()["profile"]
    profile.update(tagline="Backends that stay up.", phone=None)
    profile["socials"].append({"kind": "x", "label": "X", "href": "https://x.com/example"})

    response = admin_client.put(f"{ADMIN}/profile", json=profile, headers=CSRF)

    assert response.status_code == 200
    assert response.json()["tagline"] == "Backends that stay up."
    assert response.json()["phone"] is None
    assert admin_client.get("/api/v1/content").json()["profile"]["socials"][-1]["kind"] == "x"


def test_profile_can_be_created_on_an_empty_database(admin_client, fakes):
    profile = load_seed()["profile"].model_dump(mode="json")

    assert admin_client.put(f"{ADMIN}/profile", json=profile, headers=CSRF).status_code == 200
    assert admin_client.get("/api/v1/content").status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "n" * 101},
        {"location": "l" * 61},
        {"tagline": "t" * 161},
        {"summary": "s" * 801},
        {"email": "not-an-email"},
        {"phone": {"display": "+977", "href": "9840046034"}},
        {"socials": [{"kind": "myspace", "label": "M", "href": "https://myspace.com"}]},
        {"socials": [{"kind": "github", "label": "G", "href": "javascript:alert(1)"}]},
        {"socials": [{"kind": "github", "label": "G", "href": "https://github.com/x"}] * 9},
        {"unexpected": "field"},
    ],
)
def test_profile_validation(admin_client, seeded, change):
    profile = load_seed()["profile"].model_dump(mode="json")
    profile.update(change)

    assert_error(admin_client.put(f"{ADMIN}/profile", json=profile, headers=CSRF), 422)


# --- Projects ------------------------------------------------------------------------------


def test_create_project_appends_at_the_end(admin_client, seeded):
    response = admin_client.post(f"{ADMIN}/projects", json=project_body(), headers=CSRF)

    assert response.status_code == 201
    created = response.json()
    assert created["slug"] == "new-project"
    assert created["position"] == 5
    assert created["published"] is True
    assert admin_client.get("/api/v1/content").json()["projects"][-1]["slug"] == "new-project"


def test_project_with_image_and_case_study(admin_client, fakes):
    body = project_body(
        image={"key": IMAGE_KEY, "alt": "Dashboard", "width": 1200, "height": 750},
        case_study={
            "problem": "Slow reports.",
            "role": "Backend lead.",
            "decisions": ["Cache aggressively", "Move work to a queue"],
            "result": "Reports in under a second.",
        },
    )

    created = admin_client.post(f"{ADMIN}/projects", json=body, headers=CSRF).json()

    assert created["image"]["key"] == IMAGE_KEY
    assert created["image"]["url"].endswith(IMAGE_KEY)
    assert created["case_study"]["decisions"] == ["Cache aggressively", "Move work to a queue"]


def test_duplicate_slug_is_409(admin_client, seeded):
    response = admin_client.post(
        f"{ADMIN}/projects", json=project_body(slug="jarvis"), headers=CSRF
    )

    assert_error(response, 409)


def test_update_project_and_slug_conflict(admin_client, seeded):
    jarvis = next(p for p in seeded.projects.items.values() if p.slug == "jarvis")
    url = f"{ADMIN}/projects/{jarvis.id}"

    renamed = admin_client.put(url, json=project_body(slug="jarvis", name="Jarvis 2"), headers=CSRF)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Jarvis 2"

    assert_error(admin_client.put(url, json=project_body(slug="alliance"), headers=CSRF), 409)


def test_unknown_project_is_404(admin_client, seeded):
    url = f"{ADMIN}/projects/{uuid.uuid4()}"

    assert_error(admin_client.put(url, json=project_body(), headers=CSRF), 404)
    assert_error(admin_client.delete(url, headers=CSRF), 404)


def test_delete_project(admin_client, seeded):
    jarvis = next(p for p in seeded.projects.items.values() if p.slug == "jarvis")

    response = admin_client.delete(f"{ADMIN}/projects/{jarvis.id}", headers=CSRF)

    assert response.status_code == 204
    assert jarvis.id not in seeded.projects.items


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "Has Spaces"},
        {"slug": "UPPER"},
        {"slug": "-leading"},
        {"slug": "s" * 61},
        {"name": "n" * 101},
        {"category": "c" * 61},
        {"summary": "s" * 301},
        {"summary": "   "},
        {"stack": ["x"] * 16},
        {"stack": ["s" * 31]},
        {"repo_url": "ftp://example.com/repo"},
        {"repo_url": "https://" + "a" * 300},
        {"demo_url": "not a url"},
        {"case_study": {"problem": "p", "role": "r", "decisions": [], "result": "x"}},
        {"case_study": {"problem": "p", "role": "r", "decisions": ["d"] * 9, "result": "x"}},
        {"image": {"key": IMAGE_KEY, "alt": "", "width": 10, "height": 10}},
        {"image": {"key": IMAGE_KEY, "alt": "a", "width": 0, "height": 10}},
        {"image": {"key": IMAGE_KEY, "alt": "a", "width": 10, "height": 10001}},
        {"extra": True},
    ],
)
def test_project_validation(admin_client, seeded, change):
    assert_error(
        admin_client.post(f"{ADMIN}/projects", json=project_body(**change), headers=CSRF), 422
    )


@pytest.mark.parametrize(
    "key",
    [
        "elsewhere/projects/0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0e.png",
        f"{settings.s3_upload_prefix}other/0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0e.png",
        f"{settings.s3_upload_prefix}projects/../../secrets.png",
        f"{settings.s3_upload_prefix}projects/my-photo.png",
        f"{settings.s3_upload_prefix}projects/0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0e.html",
    ],
)
def test_image_key_outside_issued_pattern_is_422(admin_client, seeded, key):
    body = project_body(image={"key": key, "alt": "a", "width": 10, "height": 10})

    assert_error(admin_client.post(f"{ADMIN}/projects", json=body, headers=CSRF), 422)


def test_replacing_an_image_deletes_the_old_object(admin_client, fakes):
    old = {"key": IMAGE_KEY, "alt": "Old", "width": 10, "height": 10}
    new_key = f"{settings.s3_upload_prefix}projects/{uuid.uuid4()}.webp"
    created = admin_client.post(
        f"{ADMIN}/projects", json=project_body(image=old), headers=CSRF
    ).json()

    admin_client.put(
        f"{ADMIN}/projects/{created['id']}",
        json=project_body(image={**old, "key": new_key}),
        headers=CSRF,
    )

    assert fakes.storage.deleted == [IMAGE_KEY]


def test_keeping_the_same_image_deletes_nothing(admin_client, fakes):
    image = {"key": IMAGE_KEY, "alt": "Same", "width": 10, "height": 10}
    created = admin_client.post(
        f"{ADMIN}/projects", json=project_body(image=image), headers=CSRF
    ).json()

    admin_client.put(
        f"{ADMIN}/projects/{created['id']}",
        json=project_body(image=image, name="Renamed"),
        headers=CSRF,
    )

    assert fakes.storage.deleted == []


def test_removing_the_image_and_deleting_the_project_delete_objects(admin_client, fakes):
    image = {"key": IMAGE_KEY, "alt": "Img", "width": 10, "height": 10}
    created = admin_client.post(
        f"{ADMIN}/projects", json=project_body(image=image), headers=CSRF
    ).json()
    admin_client.put(
        f"{ADMIN}/projects/{created['id']}", json=project_body(image=None), headers=CSRF
    )
    assert fakes.storage.deleted == [IMAGE_KEY]

    second_key = f"{settings.s3_upload_prefix}projects/{uuid.uuid4()}.jpg"
    other = admin_client.post(
        f"{ADMIN}/projects",
        json=project_body(slug="other", image={**image, "key": second_key}),
        headers=CSRF,
    ).json()
    admin_client.delete(f"{ADMIN}/projects/{other['id']}", headers=CSRF)

    assert fakes.storage.deleted == [IMAGE_KEY, second_key]


def test_storage_failure_never_fails_the_request(admin_client, fakes):
    image = {"key": IMAGE_KEY, "alt": "Img", "width": 10, "height": 10}
    created = admin_client.post(
        f"{ADMIN}/projects", json=project_body(image=image), headers=CSRF
    ).json()
    fakes.storage.fail_delete = True

    response = admin_client.delete(f"{ADMIN}/projects/{created['id']}", headers=CSRF)

    assert response.status_code == 204


# --- Ordering ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "repo"),
    [("projects", "projects"), ("career", "career"), ("skills", "skills")],
)
def test_reorder(admin_client, seeded, path, repo):
    ids = [str(item.id) for item in asyncio.run(getattr(seeded, repo).list_all())]
    reversed_ids = list(reversed(ids))

    response = admin_client.put(f"{ADMIN}/{path}/order", json={"ids": reversed_ids}, headers=CSRF)

    assert response.status_code == 204
    after = [str(item.id) for item in asyncio.run(getattr(seeded, repo).list_all())]
    assert after == reversed_ids


@pytest.mark.parametrize("path", ["projects", "career", "skills"])
def test_reorder_needs_exactly_all_ids(admin_client, seeded, path):
    repo = getattr(seeded, path)
    ids = [str(item.id) for item in asyncio.run(repo.list_all())]

    for bad in (
        ids[:-1],
        [*ids, str(uuid.uuid4())],
        [*ids[:-1], str(uuid.uuid4())],
        [*ids, ids[0]],
    ):
        assert_error(
            admin_client.put(f"{ADMIN}/{path}/order", json={"ids": bad}, headers=CSRF), 422
        )


# --- Career and skills ---------------------------------------------------------------------

CAREER = {"role": "Backend engineer", "company": "Acme", "period": "2027", "summary": "Built APIs."}
SKILLS = {"title": "Data", "description": "Pipelines.", "skills": ["Airflow", "dbt"]}


def test_career_crud(admin_client, seeded):
    created = admin_client.post(f"{ADMIN}/career", json=CAREER, headers=CSRF)
    assert created.status_code == 201
    entry_id = created.json()["id"]
    assert created.json()["position"] == 3

    updated = admin_client.put(
        f"{ADMIN}/career/{entry_id}", json={**CAREER, "period": "2028"}, headers=CSRF
    )
    assert updated.json()["period"] == "2028"

    assert admin_client.delete(f"{ADMIN}/career/{entry_id}", headers=CSRF).status_code == 204
    assert_error(admin_client.delete(f"{ADMIN}/career/{entry_id}", headers=CSRF), 404)


def test_skills_crud(admin_client, seeded):
    created = admin_client.post(f"{ADMIN}/skills", json=SKILLS, headers=CSRF)
    assert created.status_code == 201
    group_id = created.json()["id"]

    updated = admin_client.put(
        f"{ADMIN}/skills/{group_id}", json={**SKILLS, "skills": ["Spark"]}, headers=CSRF
    )
    assert updated.json()["skills"] == ["Spark"]

    assert admin_client.delete(f"{ADMIN}/skills/{group_id}", headers=CSRF).status_code == 204
    assert_error(admin_client.put(f"{ADMIN}/skills/{group_id}", json=SKILLS, headers=CSRF), 404)


@pytest.mark.parametrize(
    "change",
    [
        {"role": "r" * 101},
        {"company": ""},
        {"period": "p" * 21},
        {"summary": "s" * 601},
        {"extra": 1},
    ],
)
def test_career_validation(admin_client, seeded, change):
    assert_error(admin_client.post(f"{ADMIN}/career", json={**CAREER, **change}, headers=CSRF), 422)


@pytest.mark.parametrize(
    "change",
    [
        {"title": "t" * 61},
        {"description": "d" * 301},
        {"skills": []},
        {"skills": ["s"] * 21},
        {"skills": ["s" * 31]},
    ],
)
def test_skills_validation(admin_client, seeded, change):
    assert_error(admin_client.post(f"{ADMIN}/skills", json={**SKILLS, **change}, headers=CSRF), 422)


# --- Uploads -------------------------------------------------------------------------------

UPLOAD = {"filename": "screenshot.png", "content_type": "image/png", "size": 123_456}


def test_upload_presign(admin_client, fakes):
    response = admin_client.post(f"{ADMIN}/uploads", json=UPLOAD, headers=CSRF)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"url", "fields", "key", "public_url", "max_bytes"}
    assert project_image_key_pattern().match(body["key"])
    assert body["key"].endswith(".png")
    assert body["max_bytes"] == settings.s3_max_upload_mb * 1024 * 1024
    assert body["public_url"].endswith(body["key"])
    assert fakes.storage.presigned == [
        {"key": body["key"], "content_type": "image/png", "max_bytes": body["max_bytes"]}
    ]


def test_upload_extension_follows_content_type_not_filename(admin_client):
    body = {**UPLOAD, "filename": "evil.html", "content_type": "image/jpeg"}

    key = admin_client.post(f"{ADMIN}/uploads", json=body, headers=CSRF).json()["key"]

    assert key.endswith(".jpg")
    assert "evil" not in key and ".html" not in key


@pytest.mark.parametrize(
    "change",
    [
        {"content_type": "text/html"},
        {"content_type": "image/svg+xml"},
        {"size": 0},
        {"size": settings.s3_max_upload_mb * 1024 * 1024 + 1},
        {"filename": ""},
    ],
)
def test_upload_validation(admin_client, change):
    assert_error(
        admin_client.post(f"{ADMIN}/uploads", json={**UPLOAD, **change}, headers=CSRF), 422
    )


def test_uploads_disabled_without_a_bucket(client, fake_repo):
    fakes = ContentFakes(storage=None)
    app.dependency_overrides[get_content_service] = fakes.service
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))

    response = client.post(f"{ADMIN}/uploads", json=UPLOAD, headers=CSRF)

    assert_error(response, 503, "uploads_disabled")


def test_upload_needs_csrf(admin_client):
    assert_error(admin_client.post(f"{ADMIN}/uploads", json=UPLOAD), 403, "csrf")


def test_s3_presigned_post_policy_pins_key_type_and_size():
    """The real S3Storage signs locally (no network): check the policy it produces."""
    from src.core.config import Settings
    from src.services.storage_service import S3Storage

    s3_settings = Settings(
        _env_file=None,
        s3_bucket_name="test-bucket",
        aws_region="eu-west-2",
        aws_access_key_id="AKIAEXAMPLEDUMMY0000",
        aws_secret_access_key="dummy-secret-for-local-signing-only",
    )
    key = f"portfolio/projects/{uuid.uuid4()}.webp"

    upload = S3Storage(s3_settings).presign_upload(key, "image/webp", 5 * 1024 * 1024)

    assert upload.url == "https://test-bucket.s3.eu-west-2.amazonaws.com/"
    assert upload.fields["key"] == key
    assert upload.fields["Content-Type"] == "image/webp"
    policy = json.loads(base64.b64decode(upload.fields["policy"]))
    conditions = policy["conditions"]
    assert {"key": key} in conditions
    assert {"bucket": "test-bucket"} in conditions
    assert {"Content-Type": "image/webp"} in conditions
    assert ["content-length-range", 1, 5 * 1024 * 1024] in conditions


# --- Seed ----------------------------------------------------------------------------------


def test_seed_is_idempotent(fakes):
    seed = load_seed()

    first = asyncio.run(seed_content(fakes.service(), seed))
    fakes.profiles.profile.tagline = "Edited in the admin."
    second = asyncio.run(seed_content(fakes.service(), seed))

    assert first == [
        "profile: created",
        "projects: created 5",
        "career: created 3",
        "skills: created 3",
    ]
    assert all(line.endswith("left unchanged") for line in second)
    assert len(fakes.projects.items) == 5
    assert fakes.profiles.profile.tagline == "Edited in the admin."


def test_seed_matches_the_validation_rules():
    seed = load_seed()

    assert seed["profile"].name == "Aashish Regmi"
    assert seed["projects"][0].slug == "fagoon-upgrade"
    assert all(p.published for p in seed["projects"])
    assert all(p.image is None for p in seed["projects"])
