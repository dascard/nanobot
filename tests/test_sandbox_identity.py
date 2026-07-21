import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.identity import Principal, derive_principal


def test_private_principal_is_derived_only_from_trusted_context():
    principal = derive_principal({
        "platform": "qq",
        "chat_type": "private",
        "user_id": "10001",
        "owner_id": "模型伪造 owner",
        "workspace_id": "模型伪造 workspace",
    })

    assert principal == Principal(platform="qq", owner_type="user", owner_id="10001")


def test_group_principal_is_disabled_by_default():
    with pytest.raises(SandboxServiceError) as raised:
        derive_principal({
            "platform": "qq",
            "chat_type": "group",
            "user_id": "sender",
            "group_id": "group-42",
        })

    assert raised.value.code is SandboxErrorCode.SANDBOX_NOT_ENABLED


def test_group_principal_uses_group_owner_when_explicitly_enabled():
    principal = derive_principal(
        {
            "platform": "qq",
            "chat_type": "group",
            "user_id": "sender",
            "group_id": "group-42",
        },
        group_enabled=True,
    )

    assert principal == Principal(platform="qq", owner_type="group", owner_id="group-42")


@pytest.mark.parametrize("field", ["platform", "user_id"])
def test_missing_trusted_identity_is_rejected(field):
    context = {"platform": "qq", "chat_type": "private", "user_id": "10001"}
    context[field] = ""

    with pytest.raises(SandboxServiceError) as raised:
        derive_principal(context)

    assert raised.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
