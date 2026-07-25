"""安全内容 Rule Engine 与兼容 Adapter 测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _descriptor(
    rule_id: str,
    *,
    pattern: str,
    match_kind="contains",
    actions=("no_learn",),
    priority: int = 100,
    scope="global",
    scope_id: str = "",
    failure_policy="fail_open",
    input_max_length: int = 4096,
):
    from core.content_rules import (
        ContentRuleAction,
        ContentRuleAuditPolicy,
        ContentRuleDescriptor,
        ContentRuleFailurePolicy,
        ContentRuleMatchKind,
        ContentRuleScope,
    )

    return ContentRuleDescriptor(
        rule_id=rule_id,
        version="1.0.0",
        owner_module="tests.content_rules",
        scope=ContentRuleScope(scope),
        scope_id=scope_id,
        match_kind=ContentRuleMatchKind(match_kind),
        pattern=pattern,
        actions=tuple(ContentRuleAction(item) for item in actions),
        priority=priority,
        input_max_length=input_max_length,
        match_max_count=1,
        failure_policy=ContentRuleFailurePolicy(failure_policy),
        audit_policy=ContentRuleAuditPolicy.MATCH_ONLY,
        positive_examples=(pattern,),
        negative_examples=("不匹配",),
        performance_budget_ms=10,
        source="builtin",
        web_editable=False,
    )


def test_content_rule_descriptor_declares_fixed_contract_and_rejects_gaps():
    from core.content_rules import (
        ContentRuleAction,
        ContentRuleMatchKind,
        ContentRuleScope,
    )

    descriptor = _descriptor("content.test", pattern="屏蔽")

    assert {item.value for item in ContentRuleAction} == {
        "block",
        "no_reply",
        "no_context",
        "no_learn",
        "redact",
        "signal",
    }
    assert descriptor.scope is ContentRuleScope.GLOBAL
    assert descriptor.match_kind is ContentRuleMatchKind.CONTAINS
    assert descriptor.positive_examples == ("屏蔽",)
    with pytest.raises(ValueError, match="owner"):
        replace(descriptor, owner_module="")


def test_engine_orders_rules_and_combines_actions_without_permission_elevation():
    from core.content_rules import (
        ContentRuleEngine,
        ContentRuleInput,
    )

    engine = ContentRuleEngine((
        _descriptor(
            "content.context",
            pattern="敏感",
            actions=("no_context",),
            priority=20,
        ),
        _descriptor(
            "content.learning",
            pattern="敏感",
            actions=("no_learn",),
            priority=10,
        ),
    ))

    result = engine.evaluate(ContentRuleInput(message="包含敏感内容"))

    assert result.matched_rule_ids == (
        "content.learning",
        "content.context",
    )
    assert {item.value for item in result.actions} == {
        "no_context",
        "no_learn",
    }
    assert result.failures == ()


def test_session_scope_requires_exact_canonical_scope_id():
    from core.content_rules import ContentRuleEngine, ContentRuleInput

    engine = ContentRuleEngine((
        _descriptor(
            "content.session",
            pattern="命中",
            scope="session",
            scope_id="qq:42:group",
        ),
    ))

    matched = engine.evaluate(ContentRuleInput(
        message="命中",
        chat_stream_id="qq:42:group",
    ))
    missed = engine.evaluate(ContentRuleInput(
        message="命中",
        chat_stream_id="qq:43:group",
    ))

    assert matched.matched_rule_ids == ("content.session",)
    assert missed.matched_rule_ids == ()


def test_rule_failure_policy_is_explicit_for_invalid_legacy_regex():
    from core.content_rules import ContentRuleEngine, ContentRuleInput

    fail_open = ContentRuleEngine((
        _descriptor(
            "content.regex_open",
            pattern="(",
            match_kind="regex",
            actions=("no_reply",),
            failure_policy="fail_open",
        ),
    )).evaluate(ContentRuleInput(message="任意消息"))
    fail_closed = ContentRuleEngine((
        _descriptor(
            "content.regex_closed",
            pattern="(",
            match_kind="regex",
            actions=("no_reply",),
            failure_policy="fail_closed",
        ),
    )).evaluate(ContentRuleInput(message="任意消息"))

    assert fail_open.matched_rule_ids == ()
    assert {item.value for item in fail_open.actions} == set()
    assert fail_open.failures[0].code == "invalid_pattern"
    assert fail_closed.matched_rule_ids == ("content.regex_closed",)
    assert {item.value for item in fail_closed.actions} == {"no_reply"}
    assert fail_closed.failures[0].code == "invalid_pattern"


def test_input_limit_uses_failure_policy_instead_of_unbounded_matching():
    from core.content_rules import ContentRuleEngine, ContentRuleInput

    result = ContentRuleEngine((
        _descriptor(
            "content.bounded",
            pattern="末尾",
            actions=("no_context",),
            failure_policy="fail_closed",
            input_max_length=4,
        ),
    )).evaluate(ContentRuleInput(message="很长的消息末尾"))

    assert result.matched_rule_ids == ("content.bounded",)
    assert result.failures[0].code == "input_too_long"
    assert {item.value for item in result.actions} == {"no_context"}


def test_web_rule_validation_allows_safe_subset_and_rejects_regex():
    from core.content_rules import (
        ContentRuleAction,
        ContentRuleMatchKind,
        ContentRuleScope,
        validate_web_content_rule,
    )

    validate_web_content_rule(
        pattern="不得学习",
        match_kind=ContentRuleMatchKind.CONTAINS,
        scope=ContentRuleScope.SESSION,
        scope_id="qq:42:group",
        actions=(ContentRuleAction.NO_LEARN,),
    )
    with pytest.raises(ValueError, match="regex"):
        validate_web_content_rule(
            pattern="(a+)+$",
            match_kind=ContentRuleMatchKind.REGEX,
            scope=ContentRuleScope.GLOBAL,
            scope_id="",
            actions=(ContentRuleAction.NO_REPLY,),
        )


def test_legacy_moderation_facade_uses_engine_and_preserves_result_shape():
    from core.moderation import check_message_moderation

    result = check_message_moderation(
        "敏感内容",
        chat_stream_id="qq:42:group",
        rules=[
            {
                "rule_id": 7,
                "pattern": "敏感",
                "match_type": "contains",
                "scope_type": "session",
                "chat_stream_id": "qq:42:group",
                "no_reply": False,
                "no_learn": True,
                "no_context": False,
                "enabled": True,
            }
        ],
    )

    assert result == {
        "pattern": "敏感",
        "match_type": "contains",
        "rule_id": 7,
        "category": "no_learn",
        "reason": "",
        "scope_type": "session",
        "no_reply": False,
        "no_learn": True,
        "no_context": False,
    }


def test_admin_create_rejects_arbitrary_regex_before_database_write():
    from api.admin.chat_config_routes import (
        ContentBlockRuleCreate,
        create_content_block_rule,
    )

    body = ContentBlockRuleCreate(
        pattern="(a+)+$",
        match_type="regex",
        scope_type="global",
        no_reply=1,
    )
    with pytest.raises(HTTPException) as exc_info:
        create_content_block_rule(
            body,
            request=SimpleNamespace(),
            db=SimpleNamespace(),
            _auth=None,
        )

    assert exc_info.value.status_code == 400
    assert "regex" in str(exc_info.value.detail)
