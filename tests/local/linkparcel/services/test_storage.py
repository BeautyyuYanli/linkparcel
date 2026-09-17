"""Storage startup checks against the subset of OpenDAL capabilities the app uses."""

from typing import cast

import pytest
from opendal import AsyncOperator

from linkparcel.services.storage import ensure_operator_ready


class _Capabilities:
    list = True
    presign_write = True
    presign_read = True
    write_with_content_type = False
    write_with_content_disposition = False


class _Operator:
    def __init__(self, capabilities: _Capabilities) -> None:
        self.capabilities = capabilities
        self.checked = False

    def capability(self) -> _Capabilities:
        return self.capabilities

    async def check(self) -> None:
        self.checked = True


async def test_ensure_operator_ready_checks_backend_after_capabilities() -> None:
    fake = _Operator(_Capabilities())

    await ensure_operator_ready(cast(AsyncOperator, fake))

    assert fake.checked


@pytest.mark.parametrize(
    "capability_name",
    ["list", "presign_write", "presign_read"],
)
async def test_ensure_operator_ready_reports_each_missing_capability_without_checking(
    capability_name: str,
) -> None:
    capabilities = _Capabilities()
    setattr(capabilities, capability_name, False)
    fake = _Operator(capabilities)

    with pytest.raises(RuntimeError, match=capability_name):
        await ensure_operator_ready(cast(AsyncOperator, fake))

    assert not fake.checked
