"""Tests for the safe archive reader. All archives are synthetic, in-memory ZIPs."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

import hashlib

from apps.api.config import Settings
from apps.api.services.archive_reader import (
    Category,
    SkipReason,
    UnsafeArchiveError,
    _validate_raw_name,
    read_archive,
)
from apps.api.services.github_archive import MalformedArchiveError

SHA = "c" * 40


def _write_zip(tmp_path: Path, members: dict, symlinks: dict[str, str] | None = None) -> Path:
    """`members` maps raw zip entry names to content (str/bytes), or None for an explicit directory."""
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            if content is None:
                zf.writestr(name if name.endswith("/") else f"{name}/", "")
            else:
                zf.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return path


def _write_raw_zip(tmp_path: Path, raw_entries: dict) -> Path:
    """Like `_write_zip` but uses explicit `ZipInfo` objects, bypassing any arcname normalization."""
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in raw_entries.items():
            zf.writestr(zipfile.ZipInfo(name), content)
    return path


# 1-4: normal shape, root stripping, nested dirs, deterministic order


def test_normal_archive_with_multiple_files(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "repo-main/": None,
            "repo-main/README.md": "hello",
            "repo-main/src/": None,
            "repo-main/src/main.py": "print(1)",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"README.md", "src/main.py"}
    assert result.commit_sha == SHA


def test_root_prefix_is_stripped(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"owner-repo-abc123/file.txt": "x"})
    result = read_archive(path, SHA)
    assert result.entries[0].path == "file.txt"


def test_nested_directory_structure_preserved(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root/a/b/c/deep.txt": "x"})
    result = read_archive(path, SHA)
    assert result.entries[0].path == "a/b/c/deep.txt"


def test_entries_are_in_deterministic_sorted_order(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/zebra.txt": "z",
            "root/apple.txt": "a",
            "root/mango.txt": "m",
        },
    )
    result = read_archive(path, SHA)
    assert [e.path for e in result.entries] == ["apple.txt", "mango.txt", "zebra.txt"]


# 5-11: unsafe path / structure rejections


def test_absolute_path_rejected(tmp_path: Path) -> None:
    path = _write_raw_zip(tmp_path, {"/etc/passwd": b"x"})
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


def test_parent_traversal_rejected(tmp_path: Path) -> None:
    path = _write_raw_zip(tmp_path, {"root/../../etc/passwd": b"x"})
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


def test_backslash_rejected(tmp_path: Path) -> None:
    path = _write_raw_zip(tmp_path, {"root\\windows\\evil": b"x"})
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


def test_windows_drive_letter_path_rejected(tmp_path: Path) -> None:
    path = _write_raw_zip(tmp_path, {"C:/Windows/evil": b"x"})
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


def test_nul_byte_rejected_by_validator() -> None:
    # zipfile itself truncates a NUL byte out of a filename on write/read, so this
    # specific case is tested directly against the validation helper.
    with pytest.raises(UnsafeArchiveError):
        _validate_raw_name("root/bad\x00name", max_path_length=1024)


def test_excessive_path_length_rejected(tmp_path: Path) -> None:
    long_name = "root/" + ("a" * 2000)
    path = _write_raw_zip(tmp_path, {long_name: b"x"})
    settings = Settings(archive_reader_max_path_length=1024)
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA, settings=settings)


def test_duplicate_normalized_paths_rejected(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("root/file.txt", "first")
        zf.writestr("root/file.txt", "second")
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


def test_multiple_archive_roots_rejected(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root-one/a.txt": "x", "root-two/b.txt": "y"})
    with pytest.raises(UnsafeArchiveError):
        read_archive(path, SHA)


# 12: symlinks skipped, not followed


def test_symlink_entries_skipped_without_following(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {"root/real.txt": "hello"},
        symlinks={"root/link.txt": "real.txt"},
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.txt"}
    assert len(result.skipped) == 1
    assert result.skipped[0].path == "link.txt"
    assert result.skipped[0].reason == SkipReason.SYMLINK


# 13: empty files and directories


def test_empty_files_and_directories_handled(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/": None,
            "root/empty.txt": "",
            "root/subdir/": None,
            "root/real.txt": "content",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.txt"}
    assert {s.path for s in result.skipped} == {"empty.txt"}
    assert result.skipped[0].reason == SkipReason.EMPTY


# 14-18: resource limits


def test_individual_file_size_limit_enforced(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root/big.txt": "x" * 1000})
    settings = Settings(archive_reader_max_file_bytes=100)
    result = read_archive(path, SHA, settings=settings)
    assert result.entries == ()
    assert result.skipped[0].reason == SkipReason.TOO_LARGE
    assert result.skipped[0].path == "big.txt"


def test_total_byte_limit_produces_truncated_result(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {"root/a.txt": "x" * 100, "root/b.txt": "x" * 100, "root/c.txt": "x" * 100},
    )
    settings = Settings(archive_reader_max_total_bytes=150, archive_reader_max_file_bytes=1_000_000)
    result = read_archive(path, SHA, settings=settings)
    assert result.truncated is True
    assert result.limit_hit == "max_total_bytes"
    assert result.total_bytes <= 150


def test_file_count_limit_truncates_only_when_work_remains(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root/a.txt": "a", "root/b.txt": "b"})
    settings = Settings(archive_reader_max_files_accepted=2)
    result = read_archive(path, SHA, settings=settings)
    assert len(result.entries) == 2
    assert result.truncated is False
    assert result.limit_hit is None


def test_file_count_limit_truncates_when_more_entries_remain(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root/a.txt": "a", "root/b.txt": "b", "root/c.txt": "c"})
    settings = Settings(archive_reader_max_files_accepted=2)
    result = read_archive(path, SHA, settings=settings)
    assert len(result.entries) == 2
    assert result.truncated is True
    assert result.limit_hit == "max_files_accepted"


def test_entry_count_limit_produces_truncated_result(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {f"root/file{i}.txt": "x" for i in range(10)})
    settings = Settings(archive_reader_max_entries_scanned=3)
    result = read_archive(path, SHA, settings=settings)
    assert result.truncated is True
    assert result.limit_hit == "max_entries_scanned"
    assert len(result.entries) < 10


def test_tampered_declared_size_never_produces_a_silent_false_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ZIP entry whose declared size disagrees with its real content must never be
    silently accepted as if the declared size were trustworthy.

    We let zipfile honestly write 200 real bytes, then patch the `ZipInfo.file_size`
    seen by the reader down to a small value, to prove the reader doesn't just skip
    straight to reading `max_file_bytes` worth and call it a day based on that field.
    CPython's own `zipfile` ties decompression length to the (tampered) declared size
    and verifies it against the stream's CRC, so a dishonest `file_size` surfaces as a
    `BadZipFile` from the standard library itself - which the reader maps to the typed
    `MalformedArchiveError` rather than returning a bogus success. Either outcome
    (caught by our own byte-counting, or by zipfile's CRC check) is acceptable; a
    silent accept of mismatched content is not, and that's what this test guards.
    """
    path = _write_zip(tmp_path, {"root/big.txt": "x" * 200})

    original_infolist = zipfile.ZipFile.infolist

    def lying_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = original_infolist(self)
        for info in infos:
            if info.filename.endswith("big.txt"):
                info.file_size = 1  # lie: declare far smaller than the real content
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lying_infolist)

    settings = Settings(archive_reader_max_file_bytes=50)
    with pytest.raises(MalformedArchiveError):
        read_archive(path, SHA, settings=settings)


# 19: corrupt zip data


def test_corrupt_zip_data_raises_malformed_archive_error(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(MalformedArchiveError):
        read_archive(path, SHA)


# 20: nothing extracted to disk


def test_no_contents_are_written_to_disk(tmp_path: Path) -> None:
    path = _write_zip(tmp_path, {"root/file.txt": "secret content"})
    before = set(tmp_path.iterdir())
    read_archive(path, SHA)
    after = set(tmp_path.iterdir())
    assert before == after  # only the archive itself is present, nothing new appeared


# 21: skip_counts matches skipped entries


def test_skip_counts_match_skipped_entries(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {"root/empty.txt": "", "root/empty2.txt": "", "root/big.txt": "x" * 1000},
        symlinks={"root/link.txt": "empty.txt"},
    )
    settings = Settings(archive_reader_max_file_bytes=100)
    result = read_archive(path, SHA, settings=settings)

    counts: dict[str, int] = {}
    for entry in result.skipped:
        counts[entry.reason.value] = counts.get(entry.reason.value, 0) + 1

    assert dict(result.skip_counts) == counts
    assert result.skip_counts["empty"] == 2
    assert result.skip_counts["symlink"] == 1
    assert result.skip_counts["too_large"] == 1


# --- Phase 2.2B: classification -------------------------------------------------


def _entry(result, path: str):
    for entry in result.entries:
        if entry.path == path:
            return entry
    raise AssertionError(f"{path!r} not found in entries: {[e.path for e in result.entries]}")


def test_classification_source_test_docs_config(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/src/app.py": "print('hi')",
            "root/tests/test_app.py": "def test_x(): assert True",
            "root/README.md": "# Title",
            "root/config.yml": "key: value",
        },
    )
    result = read_archive(path, SHA)
    assert _entry(result, "src/app.py").category == Category.SOURCE
    assert _entry(result, "tests/test_app.py").category == Category.TEST
    assert _entry(result, "README.md").category == Category.DOCS
    assert _entry(result, "config.yml").category == Category.CONFIG


def test_language_detection(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/a.py": "x",
            "root/b.ts": "x",
            "root/c.go": "x",
            "root/Dockerfile": "FROM python:3",
            "root/Makefile": "all:\n\techo hi",
            "root/mystery.xyz": "x",
        },
    )
    result = read_archive(path, SHA)
    assert _entry(result, "a.py").language == "python"
    assert _entry(result, "b.ts").language == "typescript"
    assert _entry(result, "c.go").language == "go"
    assert _entry(result, "Dockerfile").language == "dockerfile"
    assert _entry(result, "Makefile").language == "makefile"
    assert _entry(result, "mystery.xyz").language == "unknown"
    # An unrecognized extension is still kept as source, not excluded.
    assert _entry(result, "mystery.xyz").category == Category.OTHER


# --- Phase 2.2B: path-based exclusions (before decompression) ------------------


def test_excluded_directories_and_dependency_lockfiles(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/node_modules/lodash/index.js": "module.exports = {}",
            "root/vendor/lib/thing.go": "package lib",
            "root/package-lock.json": "{}",
            "root/yarn.lock": "x",
            "root/src/real.py": "print(1)",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"src/real.py"}
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["node_modules/lodash/index.js"] == SkipReason.EXCLUDED_DIRECTORY
    assert reasons["vendor/lib/thing.go"] == SkipReason.EXCLUDED_DIRECTORY
    assert reasons["package-lock.json"] == SkipReason.DEPENDENCY_LOCKFILE
    assert reasons["yarn.lock"] == SkipReason.DEPENDENCY_LOCKFILE


def test_binary_and_media_file_exclusions(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/logo.png": "binary-ish",
            "root/clip.mp4": "binary-ish",
            "root/bundle.min.js": "x",
            "root/bundle.js.map": "x",
            "root/real.py": "print(1)",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.py"}
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["logo.png"] == SkipReason.BINARY_EXTENSION
    assert reasons["clip.mp4"] == SkipReason.BINARY_EXTENSION
    assert reasons["bundle.min.js"] == SkipReason.MINIFIED_OR_SOURCEMAP
    assert reasons["bundle.js.map"] == SkipReason.MINIFIED_OR_SOURCEMAP


def test_credential_filenames_excluded_but_env_templates_kept(tmp_path: Path) -> None:
    path = _write_zip(
        tmp_path,
        {
            "root/.env": "SECRET=x",
            "root/.env.local": "SECRET=x",
            "root/.env.example": "SECRET=changeme",
            "root/.env.sample": "SECRET=changeme",
            "root/id_rsa": "fake key material",
            "root/server.pem": "fake cert",
            "root/credentials.json": "{}",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {".env.example", ".env.sample"}
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons[".env"] == SkipReason.CREDENTIAL_FILENAME
    assert reasons[".env.local"] == SkipReason.CREDENTIAL_FILENAME
    assert reasons["id_rsa"] == SkipReason.CREDENTIAL_FILENAME
    assert reasons["server.pem"] == SkipReason.CREDENTIAL_FILENAME
    assert reasons["credentials.json"] == SkipReason.CREDENTIAL_FILENAME


def test_path_exclusions_never_decompress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Path-excluded entries must never reach the chunked decompression step at all."""
    path = _write_zip(tmp_path, {"root/node_modules/big.js": "x" * 10, "root/real.py": "print(1)"})

    import apps.api.services.archive_reader as reader_module

    original_read_member = reader_module._read_member
    opened_paths: list[str] = []

    def tracking_read_member(zf, info, **kwargs):
        opened_paths.append(info.filename)
        return original_read_member(zf, info, **kwargs)

    monkeypatch.setattr(reader_module, "_read_member", tracking_read_member)
    read_archive(path, SHA)

    assert "root/node_modules/big.js" not in opened_paths
    assert "root/real.py" in opened_paths


# --- Phase 2.2B: content validation (after bounded reading) --------------------


def test_utf8_decoding_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("root/plain.py", "print('ok')")
        zf.writestr("root/bom.py", b"\xef\xbb\xbf" + b"print('bom')")
    result = read_archive(path, SHA)
    assert _entry(result, "plain.py").content == "print('ok')"
    # The BOM is stripped from the decoded text.
    assert _entry(result, "bom.py").content == "print('bom')"


def test_nul_bytes_and_invalid_utf8_rejected(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("root/binary.dat", b"abc\x00def")
        zf.writestr("root/invalid.txt", b"\xff\xfe\xfd")
        zf.writestr("root/real.py", "print(1)")
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.py"}
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["binary.dat"] == SkipReason.BINARY_CONTENT
    assert reasons["invalid.txt"] == SkipReason.NOT_UTF8


def test_git_lfs_pointer_and_generated_file_skipped(tmp_path: Path) -> None:
    lfs_pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:" + "a" * 64 + "\n"
        "size 12345\n"
    )
    generated = "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage pb\n"
    path = _write_zip(
        tmp_path,
        {
            "root/model.bin.lfs": lfs_pointer,
            "root/generated.pb.go": generated,
            "root/real.py": "print(1)",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.py"}
    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["model.bin.lfs"] == SkipReason.GIT_LFS_POINTER
    assert reasons["generated.pb.go"] == SkipReason.GENERATED_FILE


def test_secret_detection_without_leaking_matched_value(tmp_path: Path) -> None:
    secret_value = "AKIAABCDEFGHIJKLMNOP"
    path = _write_zip(
        tmp_path,
        {
            "root/config.py": f"AWS_KEY = '{secret_value}'",
            "root/private.key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n-----END RSA PRIVATE KEY-----",
            "root/real.py": "print(1)",
        },
    )
    result = read_archive(path, SHA)
    assert {e.path for e in result.entries} == {"real.py"}

    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["config.py"] == SkipReason.SECRET_DETECTED
    # private.key is excluded by filename before content is even read.
    assert reasons["private.key"] == SkipReason.CREDENTIAL_FILENAME

    report = repr(result.skipped) + repr(result.skip_counts)
    assert secret_value not in report


def test_sha256_hash_and_line_count_correct(tmp_path: Path) -> None:
    content = "line one\nline two\nline three"
    path = _write_zip(tmp_path, {"root/file.py": content})
    result = read_archive(path, SHA)
    entry = _entry(result, "file.py")
    assert entry.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert entry.line_count == 3
    assert entry.size_bytes == len(content.encode("utf-8"))
