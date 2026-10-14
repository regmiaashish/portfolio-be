"""Site content: admin inputs (with the contract's limits) and public/admin outputs.

The limits mirror docs/ADMIN_CMS_CONTRACT.md ("Validation"); the frontend enforces the same.
"""

import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from src.core.config import settings


def _text(max_length: int) -> type[str]:
    return Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)
    ]


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("must be an http:// or https:// URL")
    return value


HttpUrlText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(_http_url),
]
Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$"
    ),
]
StackItem = _text(30)

SocialKind = Literal["github", "linkedin", "instagram", "x", "website"]
ImageContentType = Literal["image/png", "image/jpeg", "image/webp", "image/avif"]
UploadContentType = Literal[
    "image/png", "image/jpeg", "image/webp", "image/avif", "application/pdf"
]
UploadPurpose = Literal["project_image", "resume"]

EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/avif": "avif",
}

RESUME_CONTENT_TYPES: dict[str, str] = {"application/pdf": "pdf"}

# What each upload purpose accepts, and the folder (under S3_UPLOAD_PREFIX) its keys live in.
UPLOAD_RULES: dict[str, tuple[dict[str, str], str]] = {
    "project_image": (EXTENSIONS, "projects/"),
    "resume": (RESUME_CONTENT_TYPES, "resume/"),
}


def project_image_key_pattern() -> re.Pattern[str]:
    """Keys /admin/uploads issues: <prefix>projects/<uuid4>.<ext>. Anything else is rejected."""
    extensions = "|".join(EXTENSIONS.values())
    return re.compile(
        rf"^{re.escape(settings.s3_upload_prefix)}projects/"
        rf"[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}\.({extensions})$"
    )


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Inputs ------------------------------------------------------------------------------


class SocialInput(_Input):
    kind: SocialKind
    label: _text(30)
    href: HttpUrlText


class PhoneInput(_Input):
    display: _text(40)
    href: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=60)]

    @field_validator("href")
    @classmethod
    def tel_link(cls, value: str) -> str:
        if not value.startswith("tel:"):
            raise ValueError("must start with tel:")
        return value


class ProfileInput(_Input):
    name: _text(100)
    role: _text(100)
    location: _text(60)
    tagline: _text(160)
    summary: _text(800)
    email: Annotated[EmailStr, Field(max_length=254)]
    phone: PhoneInput | None = None
    socials: Annotated[list[SocialInput], Field(max_length=8)] = []
    resume_url: HttpUrlText | None = None


class ImageInput(_Input):
    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    alt: _text(200)
    width: Annotated[int, Field(ge=1, le=10000)]
    height: Annotated[int, Field(ge=1, le=10000)]

    @field_validator("key")
    @classmethod
    def issued_upload_key(cls, value: str) -> str:
        if not project_image_key_pattern().match(value):
            raise ValueError(
                f"must be a key issued by /admin/uploads ({settings.s3_upload_prefix}projects/...)"
            )
        return value


class CaseStudyInput(_Input):
    problem: _text(2000)
    role: _text(2000)
    decisions: Annotated[list[_text(300)], Field(min_length=1, max_length=8)]
    result: _text(2000)


class ProjectInput(_Input):
    slug: Slug
    name: _text(100)
    category: _text(60)
    summary: _text(300)
    stack: Annotated[list[StackItem], Field(max_length=15)] = []
    repo_url: HttpUrlText | None = None
    demo_url: HttpUrlText | None = None
    image: ImageInput | None = None
    case_study: CaseStudyInput | None = None
    published: bool = False


class CareerInput(_Input):
    role: _text(100)
    company: _text(100)
    period: _text(20)
    summary: _text(600)


class SkillGroupInput(_Input):
    title: _text(60)
    description: _text(300)
    skills: Annotated[list[StackItem], Field(min_length=1, max_length=20)]


class OrderInput(_Input):
    ids: list[UUID]


class UploadRequest(_Input):
    # Declared before content_type so the content_type validator can see it.
    purpose: UploadPurpose = "project_image"
    filename: _text(200)
    content_type: UploadContentType
    size: Annotated[int, Field(ge=1)]

    @field_validator("content_type")
    @classmethod
    def type_matches_purpose(cls, value: str, info: ValidationInfo) -> str:
        purpose = info.data.get("purpose", "project_image")
        allowed, _folder = UPLOAD_RULES[purpose]
        if value not in allowed:
            raise ValueError(f"must be one of {', '.join(allowed)} for purpose {purpose}")
        return value

    @field_validator("size")
    @classmethod
    def within_limit(cls, value: int) -> int:
        limit = settings.s3_max_upload_mb * 1024 * 1024
        if value > limit:
            raise ValueError(f"must be at most {settings.s3_max_upload_mb} MB")
        return value


# --- Outputs -----------------------------------------------------------------------------


class PhoneOut(BaseModel):
    display: str
    href: str


class SocialOut(BaseModel):
    kind: SocialKind
    label: str
    href: str


class ProfileOut(BaseModel):
    name: str
    role: str
    location: str
    tagline: str
    summary: str
    email: str
    phone: PhoneOut | None
    socials: list[SocialOut]
    resume_url: str | None


class ImageOut(BaseModel):
    url: str
    alt: str
    width: int
    height: int


class CaseStudyOut(BaseModel):
    problem: str
    role: str
    decisions: list[str]
    result: str


class ProjectOut(BaseModel):
    id: UUID
    slug: str
    name: str
    category: str
    summary: str
    stack: list[str]
    repo_url: str | None
    demo_url: str | None
    image: ImageOut | None
    case_study: CaseStudyOut | None


class CareerOut(BaseModel):
    id: UUID
    role: str
    company: str
    period: str
    summary: str


class SkillGroupOut(BaseModel):
    id: UUID
    title: str
    description: str
    skills: list[str]


class ContentOut(BaseModel):
    profile: ProfileOut
    projects: list[ProjectOut]
    career: list[CareerOut]
    skills: list[SkillGroupOut]
    updated_at: datetime


class AdminImageOut(ImageOut):
    key: str


class AdminProjectOut(ProjectOut):
    image: AdminImageOut | None
    published: bool
    position: int


class AdminCareerOut(CareerOut):
    position: int


class AdminSkillGroupOut(SkillGroupOut):
    position: int


class AdminContentOut(BaseModel):
    # null until the profile has been saved once (fresh database): the admin can create it.
    profile: ProfileOut | None
    projects: list[AdminProjectOut]
    career: list[AdminCareerOut]
    skills: list[AdminSkillGroupOut]
    updated_at: datetime | None


class StoredUploadOut(BaseModel):
    """A file stored through POST /admin/uploads/file."""

    key: str
    public_url: str
    content_type: str
    size: int


class UploadOut(BaseModel):
    url: str
    fields: dict[str, str]
    key: str
    public_url: str
    max_bytes: int
