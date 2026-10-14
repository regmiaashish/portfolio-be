"""Helpers for uploads that pass through the API (Phase 6.1): type sniffing and bounded reads.

Pure functions only (no settings, no I/O beyond the file object passed in), so they are easy to
test exhaustively. The file's *contents* decide its type; names and declared types are ignored.
"""

from typing import Protocol

# Enough bytes to see every signature below, including an ISO-BMFF `ftyp` box's brand list.
SNIFF_BYTES = 64
READ_CHUNK_BYTES = 64 * 1024

_AVIF_BRANDS = {b"avif", b"avis"}


def detect_content_type(head: bytes) -> str | None:
    """The media type implied by a file's first bytes, or None if it isn't one we accept."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 16 and head[4:8] == b"ftyp" and _has_avif_brand(head):
        return "image/avif"
    return None


def _has_avif_brand(head: bytes) -> bool:
    # ftyp box: size (4) | "ftyp" (4) | major brand (4) | minor version (4) | compatible brands...
    box_size = int.from_bytes(head[:4], "big")
    end = min(box_size, len(head)) if box_size >= 16 else len(head)
    brands = {head[8:12]}
    brands.update(head[offset : offset + 4] for offset in range(16, end - 3, 4))
    return bool(brands & _AVIF_BRANDS)


class UploadTooLargeError(ValueError):
    """The upload exceeded the byte limit; reading stopped as soon as it did."""


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


async def read_limited(
    source: AsyncReadable, max_bytes: int, chunk_size: int = READ_CHUNK_BYTES
) -> bytes:
    """Read `source` in chunks, failing as soon as more than `max_bytes` have arrived."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await source.read(chunk_size)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"exceeds {max_bytes} bytes")
        chunks.append(chunk)
