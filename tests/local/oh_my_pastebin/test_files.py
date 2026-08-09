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


class _Capabilities:
    def __init__(self, *, content_type: bool = True, content_disposition: bool = True) -> None:
        self.write_with_content_type = content_type
        self.write_with_content_disposition = content_disposition


class _Operator:
    def __init__(
        self,
        entries: list[_Entry],
        *,
        content_type: bool = True,
        content_disposition: bool = True,
    ) -> None:
        self.entries = entries
        self.capabilities = _Capabilities(
            content_type=content_type,
            content_disposition=content_disposition,
        )
        self.list_calls = 0
        self.write_calls: list[tuple[str, int, dict[str, str]]] = []
        self.read_calls: list[tuple[str, int]] = []

    def capability(self) -> _Capabilities:
        return self.capabilities

    async def list(self, path: str) -> AsyncIterator[Entry]:
        assert path == ""
        self.list_calls += 1

        async def iterate() -> AsyncIterator[Entry]:
            for entry in self.entries:
                yield cast(Entry, entry)

        return iterate()

    async def presign_write(
        self,
        path: str,
        expire_second: int,
        **options: str,
    ) -> PresignedRequest:
        self.write_calls.append((path, expire_second, options))
        return cast(
            PresignedRequest,
            _PresignedRequest("PUT", "custom://write", {"x-signed": "yes"}),
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
    ("content_type", "content_disposition", "expected_options"),
    [
        (
            True,
            True,
            {
                "content_type": "application/octet-stream",
                "content_disposition": "attachment; filename*=UTF-8''report-1.pdf",
            },
        ),
        (True, False, {"content_type": "application/octet-stream"}),
        (
            False,
            True,
            {"content_disposition": "attachment; filename*=UTF-8''report-1.pdf"},
        ),
        (False, False, {}),
    ],
)
async def test_issue_upload_grant_uses_only_supported_content_metadata(
    content_type: bool,
    content_disposition: bool,
    expected_options: dict[str, str],
) -> None:
    fake = _Operator(
        [_Entry("report.pdf"), _Entry("report-2.pdf")],
        content_type=content_type,
        content_disposition=content_disposition,
    )

    await issue_upload_grant(
        cast(AsyncOperator, fake),
        UploadRequest(name="report.pdf"),
        ttl_seconds=300,
    )

    assert fake.list_calls == 1
    assert fake.write_calls == [("report-1.pdf", 300, expected_options)]


async def test_issue_upload_grant_forwards_presigned_request_components() -> None:
    fake = _Operator([], content_type=False, content_disposition=False)

    grant = await issue_upload_grant(
        cast(AsyncOperator, fake),
        UploadRequest(name="new.bin"),
        ttl_seconds=300,
    )

    assert grant.model_dump() == {
        "key": "new.bin",
        "method": "PUT",
        "upload_url": "custom://write",
        "headers": {"x-signed": "yes"},
    }


async def test_issue_upload_grant_encodes_unicode_final_key_in_disposition() -> None:
    fake = _Operator(
        [_Entry("报告.aab")],
        content_type=False,
        content_disposition=True,
    )

    await issue_upload_grant(
        cast(AsyncOperator, fake),
        UploadRequest(name="报告.aab"),
        ttl_seconds=300,
    )

    assert fake.write_calls == [
        (
            "报告-1.aab",
            300,
            {"content_disposition": ("attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A-1.aab")},
        )
    ]


async def test_issue_download_grant_does_not_check_existence() -> None:
    fake = _Operator([])

    grant = await issue_download_grant(
        cast(AsyncOperator, fake), DownloadRequest(key="missing.txt"), ttl_seconds=3600
    )

    assert grant.model_dump() == {"key": "missing.txt", "download_url": "custom://read"}
    assert fake.list_calls == 0
    assert fake.read_calls == [("missing.txt", 3600)]
