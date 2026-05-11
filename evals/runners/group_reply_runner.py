"""Group reply / reply_contract suite runner。mock Bridge，不依赖真实模型。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_group_reply_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    is_at_bot = inp.get("is_at_bot", False)
    is_reply_to_bot = inp.get("is_reply_to_bot", False)
    is_directed_to_other = inp.get("is_directed_to_other", False)
    mentions = inp.get("mentions", [])
    reply_to_message_id = inp.get("reply_to_message_id", "")
    trigger_reason = inp.get("trigger_reason", "")
    self_id = inp.get("self_id", "")

    # 调用真实 should_suppress_directed_to_other
    from core.group_runtime.runtime import should_suppress_directed_to_other, GroupPendingMessage

    pm = GroupPendingMessage(
        sender_id=inp.get("sender_id", "user1"),
        sender_name=inp.get("sender_name", ""),
        message=inp.get("message", ""),
        is_at_bot=is_at_bot,
        is_reply_to_bot=is_reply_to_bot,
        trigger_reason=trigger_reason or ("ambient" if not (is_at_bot or is_reply_to_bot) else "at_bot"),
        is_directed_to_other=is_directed_to_other,
        self_id=self_id,
    )
    suppressed = should_suppress_directed_to_other([pm])

    if suppressed:
        out.should_reply = False
        out.tool_calls = []
        out.db_writes["directed_to_other"] = True
        return out

    # 需要回复的场景
    if is_at_bot or is_reply_to_bot or inp.get("message", ""):
        out.should_reply = True
        out.tool_calls = ["reply"]

        # quote 契约：引用 bot 消息时应用 quote
        if is_reply_to_bot and reply_to_message_id:
            out.reply_meta["send_mode"] = "quote"
            out.reply_meta["reply_to_message_id"] = reply_to_message_id
        elif is_at_bot:
            out.reply_meta["send_mode"] = "mention"
        else:
            out.reply_meta["send_mode"] = "normal"

        # mentions 处理
        if mentions:
            out.reply_meta["mentions"] = [str(m.get("user_id", m) if isinstance(m, dict) else m) for m in mentions]

        out.reply_text = "mock reply"
    else:
        out.should_reply = False
        out.tool_calls = []

    return out
