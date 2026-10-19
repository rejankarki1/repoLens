"""Safely reads a downloaded GitHub archive ZIP without extracting it to disk.

Independent of GitHub, HTTP, FastAPI, the database, and Redis - it only knows how to
open a local ZIP file and hand back validated, size-bounded, classified source files
in memory. Never executes or imports anything from the archive; never follows
symlinks; never trusts a ZIP entry's declared size as the sole protection against
oversized content.

Path-based exclusions (excluded directories, lockfiles, binary extensions, credential
filenames) are applied before any decompression happens. Content-based checks (UTF-8
decoding, NUL-byte/binary detection, generated-file and secret detection) necessarily
run after a member's bytes have been read, but still within the same bounded read -
nothing is decompressed twice, and nothing beyond the configured limits is ever read.

The caller (e.g. the code that used `GitHubArchiveService` to download the archive)
owns the archive file and is responsible for cleaning it up - this module never
deletes the archive it reads.
"""

from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from apps.api.config import Settings, get_settings
from apps.api.services.github_archive import MalformedArchiveError

_CHUNK_SIZE = 65536
_DRIVE_LETTER_PATTERN = re.compile(r"^[A-Za-z]:")
_TOTAL_BYTES_CAP_SENTINEL = "total_bytes_cap"


class ArchiveReadError(Exception):
    """Base error for archive reading failures."""


class UnsafeArchiveError(ArchiveReadError):
    """The archive contains a path or structure that cannot be safely processed."""


class SkipReason(str, Enum):
    TOO_LARGE = "too_large"
    SYMLINK = "symlink"
    EMPTY = "empty"
    EXCLUDED_DIRECTORY = "excluded_directory"
    DEPENDENCY_LOCKFILE = "dependency_lockfile"
    BINARY_EXTENSION = "binary_extension"
    MINIFIED_OR_SOURCEMAP = "minified_or_sourcemap"
    CREDENTIAL_FILENAME = "credential_filename"
    BINARY_CONTENT = "binary_content"
    NOT_UTF8 = "not_utf8"
    GIT_LFS_POINTER = "git_lfs_pointer"
    GENERATED_FILE = "generated_file"
    SECRET_DETECTED = "secret_detected"


class Category(str, Enum):
    SOURCE = "source"
    TEST = "test"
    DOCS = "docs"
    CONFIG = "config"
    OTHER = "other"


@dataclass(frozen=True)
class SourceFile:
    """A single accepted, decoded, classified file from the archive."""

    path: str
    language: str
    category: Category
    content: str
    sha256: str
    size_bytes: int
    line_count: int


@dataclass(frozen=True)
class SkippedEntry:
    path: str
    reason: SkipReason
    size_bytes: int | None


@dataclass(frozen=True)
class ArchiveReadResult:
    """The outcome of reading one archive.

    `total_bytes` is the sum of actual decompressed bytes read across every member
    processed (including members subsequently skipped, e.g. for being too large or
    for failing a content check) - never a sum of ZIP-declared sizes, which are
    never trusted.
    """

    commit_sha: str
    entries: tuple[SourceFile, ...]
    skipped: tuple[SkippedEntry, ...]
    skip_counts: Mapping[str, int]
    truncated: bool
    limit_hit: str | None
    total_bytes: int


# --- Path-based exclusions (checked before decompression) -----------------------

_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        "node_modules", "vendor", "dist", "build", "out", "target",
        ".git", ".next", ".nuxt", "coverage",
        "__pycache__", ".venv", "venv", ".tox",
    }
)

_DEPENDENCY_LOCKFILES = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
        "Pipfile.lock", "Cargo.lock", "go.sum",
    }
)

_BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff", ".tif",
        ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm",
        ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".exe", ".dll", ".so", ".dylib", ".o", ".obj", ".class", ".pyc", ".pyo", ".whl", ".jar", ".war", ".bin",
    }
)

_SAFE_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample"})


def _is_excluded_directory(normalized_path: str) -> bool:
    segments = normalized_path.split("/")[:-1]
    return any(segment in _EXCLUDED_DIRECTORY_NAMES for segment in segments)


def _is_dependency_lockfile(basename: str) -> bool:
    return basename in _DEPENDENCY_LOCKFILES


def _is_minified_or_sourcemap(basename: str) -> bool:
    lowered = basename.lower()
    return lowered.endswith(".map") or lowered.endswith(".min.js") or lowered.endswith(".min.css")


def _is_binary_extension(basename: str) -> bool:
    return Path(basename).suffix.lower() in _BINARY_EXTENSIONS


def _is_credential_filename(basename: str) -> bool:
    if basename in _SAFE_ENV_TEMPLATE_NAMES:
        return False
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename.endswith(".pem") or basename.endswith(".key"):
        return True
    if basename.startswith("id_rsa"):
        return True
    if basename == "credentials.json":
        return True
    return False


def _path_exclusion_reason(normalized_path: str) -> SkipReason | None:
    """Check every path-based exclusion rule; return the first reason that applies, if any."""
    basename = Path(normalized_path).name
    if _is_excluded_directory(normalized_path):
        return SkipReason.EXCLUDED_DIRECTORY
    if _is_dependency_lockfile(basename):
        return SkipReason.DEPENDENCY_LOCKFILE
    if _is_minified_or_sourcemap(basename):
        return SkipReason.MINIFIED_OR_SOURCEMAP
    if _is_binary_extension(basename):
        return SkipReason.BINARY_EXTENSION
    if _is_credential_filename(basename):
        return SkipReason.CREDENTIAL_FILENAME
    return None


# --- Content-based checks (run after a bounded read) ----------------------------

_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v"

_GENERATED_MARKERS = ("@generated", "do not edit", "automatically generated", "code generated")

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)


def _decode_utf8(raw: bytes) -> str | None:
    """Strict UTF-8 decode, transparently accepting (and stripping) a UTF-8 BOM."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _is_git_lfs_pointer(text: str) -> bool:
    first_line = text.splitlines()[0] if text else ""
    return first_line.startswith(_LFS_POINTER_PREFIX)


def _looks_generated(text: str) -> bool:
    header = "\n".join(text.splitlines()[:5]).lower()
    return any(marker in header for marker in _GENERATED_MARKERS)


def _contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


# --- Classification ---------------------------------------------------------------

_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".java": "java",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".c": "c", ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "rst",
    ".yml": "yaml", ".yaml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
}

_LANGUAGE_BY_BASENAME = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
}

_TEST_DIR_PATTERN = re.compile(r"(^|/)(tests?|__tests__|specs?)(/|$)", re.IGNORECASE)
_TEST_NAME_PATTERN = re.compile(r"([._-]test|test[._-]|[._-]spec|spec[._-])", re.IGNORECASE)

_DOC_EXTENSIONS = frozenset({".md", ".rst"})
_DOC_BASENAMES = frozenset({"readme", "changelog", "license", "contributing", "authors", "notice"})
_DOC_DIRECTORY_NAMES = frozenset({"docs", "doc"})

_CONFIG_EXTENSIONS = frozenset({".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json"})
_CONFIG_BASENAMES = frozenset({"dockerfile", "makefile", ".gitignore", ".editorconfig", ".dockerignore"})


def _detect_language(basename: str) -> str:
    language = _LANGUAGE_BY_BASENAME.get(basename.lower())
    if language:
        return language
    return _LANGUAGE_BY_EXTENSION.get(Path(basename).suffix.lower(), "unknown")


def _classify_category(normalized_path: str, language: str) -> Category:
    segments = normalized_path.split("/")
    basename = segments[-1]
    directories = [segment.lower() for segment in segments[:-1]]
    stem = Path(basename).stem.lower()
    suffix = Path(basename).suffix.lower()

    if _TEST_DIR_PATTERN.search(normalized_path) or _TEST_NAME_PATTERN.search(basename):
        return Category.TEST
    if suffix in _DOC_EXTENSIONS or stem in _DOC_BASENAMES or any(d in _DOC_DIRECTORY_NAMES for d in directories):
        return Category.DOCS
    if suffix in _CONFIG_EXTENSIONS or basename.lower() in _CONFIG_BASENAMES or ".github" in directories:
        return Category.CONFIG
    if language != "unknown":
        return Category.SOURCE
    return Category.OTHER


def read_archive(
    archive_path: Path | str, commit_sha: str, *, settings: Settings | None = None
) -> ArchiveReadResult:
    """Read `archive_path` (a local ZIP file) into a bounded, validated, classified result.

    A plain synchronous function - async callers should run it off the event loop,
    e.g. `await asyncio.to_thread(read_archive, path, sha)`.
    """
    settings = settings or get_settings()
    archive_path = Path(archive_path)

    try:
        with zipfile.ZipFile(archive_path) as zf:
            return _read_archive(zf, commit_sha, settings)
    except zipfile.BadZipFile as exc:
        raise MalformedArchiveError(f"Archive at {archive_path} is not a valid ZIP file") from exc
    except OSError as exc:
        raise MalformedArchiveError(f"Could not read archive at {archive_path}") from exc


def _read_archive(zf: zipfile.ZipFile, commit_sha: str, settings: Settings) -> ArchiveReadResult:
    max_file_bytes = settings.archive_reader_max_file_bytes
    max_files_accepted = settings.archive_reader_max_files_accepted
    max_total_bytes = settings.archive_reader_max_total_bytes
    max_entries_scanned = settings.archive_reader_max_entries_scanned
    max_path_length = settings.archive_reader_max_path_length

    infos = sorted(zf.infolist(), key=lambda info: info.filename)

    entries: list[SourceFile] = []
    skipped: list[SkippedEntry] = []
    skip_counts: dict[SkipReason, int] = {}
    seen_normalized_files: set[str] = set()
    root: str | None = None
    total_bytes = 0
    truncated = False
    limit_hit: str | None = None

    def record_skip(path: str, reason: SkipReason, size_bytes: int | None) -> None:
        skipped.append(SkippedEntry(path=path, reason=reason, size_bytes=size_bytes))
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for scan_index, info in enumerate(infos, start=1):
        if scan_index > max_entries_scanned:
            truncated = True
            limit_hit = "max_entries_scanned"
            break

        raw_name = info.filename
        _validate_raw_name(raw_name, max_path_length)

        root_segment, rest = _split_root(raw_name)
        if root_segment is None:
            raise UnsafeArchiveError(f"Entry has no archive-root prefix: {raw_name!r}")
        if root is None:
            root = root_segment
        elif root_segment != root:
            raise UnsafeArchiveError(
                f"Archive has multiple roots ({root!r} and {root_segment!r}); expected exactly one"
            )

        if info.is_dir():
            continue

        normalized = rest.rstrip("/")
        if normalized == "":
            raise UnsafeArchiveError(f"Entry normalizes to an empty path: {raw_name!r}")

        if normalized in seen_normalized_files:
            raise UnsafeArchiveError(f"Duplicate normalized file path: {normalized!r}")
        seen_normalized_files.add(normalized)

        if _is_symlink(info):
            record_skip(normalized, SkipReason.SYMLINK, info.file_size)
            continue

        # Path-based exclusions - checked before any decompression happens.
        path_exclusion = _path_exclusion_reason(normalized)
        if path_exclusion is not None:
            record_skip(normalized, path_exclusion, info.file_size)
            continue

        if len(entries) >= max_files_accepted:
            truncated = True
            limit_hit = "max_files_accepted"
            break

        if info.file_size > max_file_bytes:
            record_skip(normalized, SkipReason.TOO_LARGE, info.file_size)
            continue

        outcome, data, bytes_read = _read_member(
            zf, info, max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes, total_bytes_so_far=total_bytes
        )
        total_bytes += bytes_read

        if outcome == _TOTAL_BYTES_CAP_SENTINEL:
            truncated = True
            limit_hit = "max_total_bytes"
            break
        if outcome == SkipReason.TOO_LARGE:
            record_skip(normalized, SkipReason.TOO_LARGE, bytes_read)
            continue
        if outcome == SkipReason.EMPTY:
            record_skip(normalized, SkipReason.EMPTY, 0)
            continue

        # Content-based checks - only reachable after a successful bounded read.
        raw_bytes = bytes(data)
        content_reason = _content_skip_reason(raw_bytes)
        if content_reason is not None:
            record_skip(normalized, content_reason, bytes_read)
            continue

        text = _decode_utf8(raw_bytes)
        assert text is not None  # _content_skip_reason already confirmed decodability

        basename = Path(normalized).name
        language = _detect_language(basename)
        category = _classify_category(normalized, language)
        entries.append(
            SourceFile(
                path=normalized,
                language=language,
                category=category,
                content=text,
                sha256=hashlib.sha256(raw_bytes).hexdigest(),
                size_bytes=bytes_read,
                line_count=len(text.splitlines()),
            )
        )

    return ArchiveReadResult(
        commit_sha=commit_sha,
        entries=tuple(entries),
        skipped=tuple(skipped),
        skip_counts=MappingProxyType({reason.value: count for reason, count in skip_counts.items()}),
        truncated=truncated,
        limit_hit=limit_hit,
        total_bytes=total_bytes,
    )


def _content_skip_reason(raw_bytes: bytes) -> SkipReason | None:
    """Run every content-based check in order; return the first reason to skip, if any.

    Never includes any matched content (secret values, file text) in its return value -
    only a typed reason, safe to log or report.
    """
    if b"\x00" in raw_bytes:
        return SkipReason.BINARY_CONTENT

    text = _decode_utf8(raw_bytes)
    if text is None:
        return SkipReason.NOT_UTF8

    if _is_git_lfs_pointer(text):
        return SkipReason.GIT_LFS_POINTER
    if _looks_generated(text):
        return SkipReason.GENERATED_FILE
    if _contains_secret(text):
        return SkipReason.SECRET_DETECTED

    return None


def _read_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    total_bytes_so_far: int,
) -> tuple[SkipReason | str | None, bytearray, int]:
    """Read one member in bounded chunks, never trusting `info.file_size` alone.

    Returns (outcome, data, bytes_read). `outcome` is `None` for a successful bounded
    read, a `SkipReason` for a per-file rejection, or `_TOTAL_BYTES_CAP_SENTINEL` if
    the global byte budget was exhausted mid-read. `data` is only populated when
    `outcome` is `None`.
    """
    data = bytearray()
    file_bytes = 0
    with zf.open(info) as member:
        while True:
            remaining_total_budget = max_total_bytes - total_bytes_so_far - file_bytes
            if remaining_total_budget <= 0:
                return _TOTAL_BYTES_CAP_SENTINEL, bytearray(), file_bytes

            chunk = member.read(min(_CHUNK_SIZE, remaining_total_budget))
            if not chunk:
                break

            data.extend(chunk)
            file_bytes += len(chunk)

            if file_bytes > max_file_bytes:
                return SkipReason.TOO_LARGE, bytearray(), file_bytes

    if file_bytes == 0:
        return SkipReason.EMPTY, data, 0

    return None, data, file_bytes


def _validate_raw_name(name: str, max_path_length: int) -> None:
    if not name:
        raise UnsafeArchiveError("Empty entry name in archive")
    if "\x00" in name:
        raise UnsafeArchiveError(f"NUL byte in entry name: {name!r}")
    if "\\" in name:
        raise UnsafeArchiveError(f"Backslash in entry name: {name!r}")
    if name.startswith("/"):
        raise UnsafeArchiveError(f"Absolute path in entry name: {name!r}")
    if _DRIVE_LETTER_PATTERN.match(name):
        raise UnsafeArchiveError(f"Windows drive-letter path in entry name: {name!r}")
    if len(name) > max_path_length:
        raise UnsafeArchiveError(f"Entry name exceeds maximum path length of {max_path_length}: {name!r}")
    if ".." in name.split("/"):
        raise UnsafeArchiveError(f"Parent directory traversal in entry name: {name!r}")


def _split_root(name: str) -> tuple[str | None, str]:
    """Split off the leading archive-root path component.

    Returns `(root, rest)`, or `(None, name)` if `name` has no `/` at all (i.e. no
    root component could be identified).
    """
    if "/" not in name:
        return None, name
    root, _, rest = name.partition("/")
    return root, rest


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_ISLNK(unix_mode)
