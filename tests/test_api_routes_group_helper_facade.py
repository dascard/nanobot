from pathlib import Path


def test_api_routes_group_helpers_are_app_helper_facades():
    import api.routes as routes
    from app.group_ingress import helpers

    expected = {
        "_normalize_onebot_segments": helpers.normalize_onebot_segments,
        "_extract_mentions_from_segments": helpers.extract_mentions_from_segments,
        "_normalize_group_mentions": helpers.normalize_group_mentions,
        "_normalize_group_reply_to": helpers.normalize_group_reply_to,
        "_derive_group_direction": helpers.derive_group_direction,
        "_detect_group_bot_sender": helpers.detect_group_bot_sender,
        "_build_group_message_meta": helpers.build_group_message_meta,
        "_safe_group_client_meta": helpers.safe_group_client_meta,
        "_group_sticker_payloads": helpers.group_sticker_payloads,
        "_render_segments_to_text": helpers.render_segments_to_text,
        "_build_group_message_text": helpers.build_group_message_text,
        "_register_group_stickers_from_message": helpers.register_group_stickers_from_message,
        "_annotate_group_timing_event": helpers.annotate_group_timing_event,
        "_normalize_reply_for_duplicate": helpers.normalize_reply_for_duplicate,
        "_pop_bridge_reply_meta": helpers.pop_bridge_reply_meta,
        "_derive_group_agent_result": helpers.derive_group_agent_result,
        "_find_recent_duplicate_group_reply": helpers.find_recent_duplicate_group_reply,
        "_log_group_no_reply": helpers.log_group_no_reply,
        "_persist_group_bridge_reply": helpers.persist_group_bridge_reply,
        "_derive_group_trigger_reason": helpers.derive_group_trigger_reason,
    }
    for name, target in expected.items():
        assert getattr(routes, name) is target


def test_api_routes_group_helper_split_keeps_routes_file_under_3000_lines():
    line_count = len(Path("api/routes.py").read_text(encoding="utf-8").splitlines())
    assert line_count < 3000
