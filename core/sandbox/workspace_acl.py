"""Sandbox grant 与 Workspace owner 的关闭式交叉校验。"""

from __future__ import annotations


def workspace_matches_sandbox_grant(
    workspace: object,
    grant: object,
) -> bool:
    """确认 grant 只能指向自身会话拥有的 Workspace。"""

    chat_type = str(getattr(grant, "chat_type", "") or "").strip().lower()
    if chat_type == "group":
        expected_owner_type = "group"
        expected_owner_id = str(
            getattr(grant, "external_session_id", "") or ""
        )
    elif chat_type == "private":
        # 私聊 Sandbox 当前按会话 grant 隔离；grant.id 是稳定、不可由
        # 请求方提交的 owner key，避免同一用户元数据桥接不同会话。
        expected_owner_type = "user"
        expected_owner_id = str(getattr(grant, "id", "") or "")
    else:
        return False

    return bool(
        workspace is not None
        and expected_owner_id
        and str(getattr(workspace, "platform", "") or "")
        == str(getattr(grant, "platform", "") or "")
        and str(getattr(workspace, "owner_type", "") or "")
        == expected_owner_type
        and str(getattr(workspace, "owner_id", "") or "")
        == expected_owner_id
    )


__all__ = ["workspace_matches_sandbox_grant"]
