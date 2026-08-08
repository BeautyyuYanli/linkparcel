"""File-control-plane contracts independent of any real storage service."""

from collections.abc import AsyncIterator
from typing import cast

import pytest
from opendal import AsyncOperator
from opendal.types import Entry, PresignedRequest

from oh_my_pastebin.files import (
    choose_key,
    issue_download_grant,
    issue_upload_grant,
    list_files,
    validate_key,
)
from oh_my_pastebin.models import DownloadRequest, UploadRequest


class _Metadata:
    def __init__(self, *, is_file: bool) -> None:
        self.is_file = is_file


class _Entry:
    def __init__(self, path: str, *, is_file: bool = True) -> None:
        self.path = path
        self.metadata = _Metadata(is_file=is_file)


class _PresignedRequest:
    def __init__(self, method: str, url: str, headers: dict[str, str]) -> None:
        self.method = method
        self.url = url
        self.headers = headers


class _Operator:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries
        self.list_calls = 0
        self.write_calls: list[tuple[str, int, str | None]] = []
        self.read_calls: list[tuple[str, int]] = []

    async def list(self, path: str) -> AsyncIterator[Entry]:
        assert path == ""
        self.list_calls += 1

        async def iterate() -> AsyncIterator[Entry]:
            for entry in self.entries:
                yield cast(Entry, entry)

        return iterate()

    async def presign_write(
        self, path: str, expire_second: int, *, content_type: str | None = None
    ) -> PresignedRequest:
        self.write_calls.append((path, expire_second, content_type))
        return cast(
            PresignedRequest,
            _PresignedRequest("PUT", "custom://write", {"content-type": content_type or ""}),
        )

    async def presign_read(self, path: str, expire_second: int) -> PresignedRequest:
        self.read_calls.append((path, expire_second))
        return cast(PresignedRequest, _PresignedRequest("GET", "custom://read", {}))


@pytest.mark.parametrize(
    "key",
    ["", "   ", ".", "..", "a/b", "a\\b", "a\0b", "a\nb", "x" * 201],
)
def test_validate_key_rejects_invalid_single_segment_names(key: str) -> None:
    with pytest.raises(ValueError):
        validate_key(key)


def test_validate_key_preserves_valid_name_including_edge_whitespace() -> None:
    assert validate_key(" report.pdf ") == " report.pdf "


def test_choose_key_preserves_unused_name_and_uses_first_free_suffix() -> None:
    assert choose_key("report.pdf", set()) == "report.pdf"
    assert choose_key("report.pdf", {"report.pdf", "report-2.pdf"}) == "report-1.pdf"
    assert choose_key("report.pdf", {"report.pdf", "report-1.pdf"}) == "report-2.pdf"
    assert choose_key("report-2.pdf", {"report-2.pdf"}) == "report-2-1.pdf"


def test_choose_key_rejects_candidate_over_length_limit() -> None:
    name = f"{'x' * 196}.txt"

    with pytest.raises(ValueError, match="200"):
        choose_key(name, {name})


async def test_list_files_ignores_directories_and_sorts_keys() -> None:
    fake = _Operator([_Entry("z.txt"), _Entry("folder/", is_file=False), _Entry("a.txt")])

    result = await list_files(cast(AsyncOperator, fake))

    assert [file.key for file in result.files] == ["a.txt", "z.txt"]
    assert fake.list_calls == 1
    assert fake.write_calls == []
    assert fake.read_calls == []


@pytest.mark.parametrize(
    ("upload_request", "expected_key", "expected_content_type"),
    [
        (
            UploadRequest(name="report.pdf", content_type="application/pdf"),
            "report-1.pdf",
            "application/pdf",
        ),
        (UploadRequest(name="new.bin"), "new.bin", "application/octet-stream"),
    ],
)
async def test_issue_upload_grant_lists_once_and_forwards_presign_shape(
    upload_request: UploadRequest,
    expected_key: str,
    expected_content_type: str,
) -> None:
    fake = _Operator([_Entry("report.pdf"), _Entry("report-2.pdf")])

    grant = await issue_upload_grant(
        cast(AsyncOperator, fake),
        upload_request,
        ttl_seconds=300,
    )

    assert grant.model_dump() == {
        "key": expected_key,
        "method": "PUT",
        "upload_url": "custom://write",
        "headers": {"content-type": expected_content_type},
    }
    assert fake.list_calls == 1
    assert fake.write_calls == [(expected_key, 300, expected_content_type)]


async def test_issue_download_grant_does_not_check_existence() -> None:
    fake = _Operator([])

    grant = await issue_download_grant(
        cast(AsyncOperator, fake), DownloadRequest(key="missing.txt"), ttl_seconds=3600
    )

    assert grant.model_dump() == {"key": "missing.txt", "download_url": "custom://read"}
    assert fake.list_calls == 0
    assert fake.read_calls == [("missing.txt", 3600)]
