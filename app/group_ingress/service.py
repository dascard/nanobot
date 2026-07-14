"""群聊统一入口服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NoReturn

from app.group_ingress import helpers as h
from app.group_ingress import recovery
from app.group_ingress import response_contract
from core.database import ChatLog, User, release_clean_session_transaction
from core.inbound_claim_lifecycle import (
    InboundClaimOwner,
    InboundClaimOwnershipLostError,
)
from core.inbound_idempotency import (
    ClaimDecisionKind,
    CompletedInboundResponse,
    InboundClaimKey,
    acquire_inbound_claim,
    normalize_inbound_claim_key,
)
from core.identity import build_identity_vars, is_super_user_id
from core.moderation import check_message_moderation_db
from core.sqlite_retry import is_sqlite_locked_error
from core.sqlite_retry import run_sqlite_locked_retry

logger = logging.getLogger("nanobot.group_ingress")


def _raise_primary_with_secondary(
    primary: BaseException,
    secondary: BaseException,
    *,
    label: str,
) -> NoReturn:
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        try:
            add_note(f"{label}: {type(secondary).__name__}: {secondary}")
        except BaseException:
            pass
    raise primary from secondary


@dataclass
class GroupIngressResult:
    """群聊业务执行结果；结算语义不从 HTTP payload 反推。"""

    payload: dict[str, Any]
    completion: CompletedInboundResponse | None = None
    technical_error: BaseException | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise TypeError("payload 必须是 dict")
        if self.completion is not None and type(self.completion) is not CompletedInboundResponse:
            raise TypeError("completion 必须是 CompletedInboundResponse 或 null")
        if self.technical_error is not None and not isinstance(self.technical_error, BaseException):
            raise TypeError("technical_error 必须是 BaseException 或 null")
        has_completion = self.completion is not None
        has_error = self.technical_error is not None
        if has_completion == has_error:
            raise ValueError("completion 与 technical_error 必须恰好一个非空")


class GroupIngressService:
    """处理 /group/message 的业务流程。"""

    def __init__(self, *, db: Any, background_tasks: Any = None, bridge_provider: Any = None):
        self.db = db
        self.background_tasks = background_tasks
        self.bridge_provider = bridge_provider

    def _platform_from_request(self, req: Any) -> str:
        client_meta = getattr(req, "client_meta", None)
        client_meta = client_meta if isinstance(client_meta, dict) else {}
        return str(client_meta.get("platform") or "qq").strip().lower() or "qq"

    def _business_result(
        self,
        req: Any,
        *,
        outcome: str,
        reply: str = "",
        reply_meta: dict[str, Any] | None = None,
        generation: int | None = None,
        reason: str = "",
        delay_seconds: int | float | None = None,
        diagnostics: dict[str, Any] | None = None,
        duplicate_reply: dict[str, Any] | None = None,
        hard_rule: str = "",
    ) -> GroupIngressResult:
        completion = response_contract.build_completed_group_response(
            outcome=outcome,
            reply=reply,
            reply_meta=reply_meta,
            generation=generation,
            reason=str(reason or "")[:120],
            delay_seconds=delay_seconds,
            diagnostics=diagnostics,
            duplicate_reply=duplicate_reply,
            hard_rule=hard_rule,
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(req, completion),
            completion=completion,
        )

    def _technical_result(
        self,
        req: Any,
        error: BaseException,
        *,
        reason: str,
        reply_meta: dict[str, Any] | None = None,
        generation: int | None = None,
        delay_seconds: int | float | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> GroupIngressResult:
        return GroupIngressResult(
            payload=response_contract.technical_group_response_payload(
                req,
                reason=str(reason or "")[:120],
                reply_meta=reply_meta,
                generation=generation,
                delay_seconds=delay_seconds,
                diagnostics=diagnostics,
            ),
            technical_error=error,
        )

    def _rollback_request_session_best_effort(self) -> None:
        try:
            self.db.rollback()
        except BaseException as exc:
            try:
                logger.error("[GroupMsg] request Session rollback failed: %r", exc)
            except BaseException:
                pass

    async def _run_owned_request(
        self,
        owner: InboundClaimOwner,
        request_factory: Callable[[], Awaitable[GroupIngressResult]],
    ) -> GroupIngressResult:
        """竞速群聊业务与 owner 不可用信号，并完整回收两个 task。"""

        business_task = asyncio.create_task(
            request_factory(),
            name="group-ingress-business",
        )
        unusable_task = asyncio.create_task(
            owner.wait_unusable(),
            name="group-claim-unusable",
        )
        try:
            done, _ = await asyncio.wait(
                {business_task, unusable_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if business_task in done and unusable_task in done:
                result = await business_task
                await owner.checkpoint()
                return result
            if unusable_task in done:
                business_task.cancel()
                await asyncio.gather(business_task, return_exceptions=True)
                try:
                    await unusable_task
                except BaseException:
                    raise
                raise RuntimeError("owner unusable watcher 非预期正常返回")

            result = await business_task
            if unusable_task.done():
                await owner.checkpoint()
            return result
        finally:
            if not unusable_task.done():
                unusable_task.cancel()
            await asyncio.gather(unusable_task, return_exceptions=True)
            if not business_task.done():
                business_task.cancel()
            await asyncio.gather(business_task, return_exceptions=True)

    async def handle(self, req: Any) -> dict[str, Any]:
        db = self.db
        group_user_id = h.normalize_group_session_id(req.group_id)
        release_clean_session_transaction(
            db,
            label="group_before_inbound_claim",
            logger=logger,
        )
        claim_key = normalize_inbound_claim_key(
            self._platform_from_request(req),
            "group",
            group_user_id,
            req.message_id,
        )
        claim_decision = acquire_inbound_claim(db, claim_key)
        if claim_key is not None:
            req.message_id = claim_key.message_id
        elif claim_decision.kind is ClaimDecisionKind.BYPASS:
            req.message_id = None

        if claim_decision.kind is ClaimDecisionKind.DUPLICATE_INFLIGHT:
            return response_contract.duplicate_inflight_group_response_payload(req)

        if claim_decision.kind is ClaimDecisionKind.REPLAY:
            completed_response = claim_decision.response
            if completed_response is None:
                raise RuntimeError("completed group claim 缺少完成结果")
            return response_contract.completed_group_response_payload(req, completed_response)

        owner: InboundClaimOwner | None = None
        attempt_count = 0
        if claim_decision.kind is ClaimDecisionKind.ACQUIRED:
            if claim_decision.handle is None:
                raise RuntimeError("acquired group claim 缺少 owner handle")
            owner = InboundClaimOwner(claim_decision.handle)
            attempt_count = claim_decision.handle.attempt_count
        elif claim_decision.kind is not ClaimDecisionKind.BYPASS:
            raise RuntimeError(f"未知 group claim decision: {claim_decision.kind}")

        fail_settlement_attempted = False
        try:
            if owner is not None:
                await owner.start()
                result = await self._run_owned_request(
                    owner,
                    lambda: self._execute_request(
                        req,
                        group_user_id=group_user_id,
                        attempt_count=attempt_count,
                        claim_key=claim_key,
                        claim_owner=owner,
                    ),
                )
            else:
                result = await self._execute_request(
                    req,
                    group_user_id=group_user_id,
                    attempt_count=attempt_count,
                    claim_key=None,
                    claim_owner=None,
                )
            if not isinstance(result, GroupIngressResult):
                raise TypeError("群聊业务体必须返回 GroupIngressResult")

            if result.technical_error is not None:
                self._rollback_request_session_best_effort()
                if owner is not None:
                    fail_settlement_attempted = True
                    try:
                        failed = await owner.fail(result.technical_error)
                    except BaseException as settlement_error:
                        _raise_primary_with_secondary(
                            result.technical_error,
                            settlement_error,
                            label="群聊技术失败的 claim fail 结算再次失败",
                        )
                    if failed is not True:
                        _raise_primary_with_secondary(
                            result.technical_error,
                            InboundClaimOwnershipLostError(
                                "群聊技术失败结算时已失去 claim owner"
                            ),
                            label="群聊技术失败的 claim fail 结算失权",
                        )
                return result.payload

            if owner is not None:
                if result.completion is None:
                    raise RuntimeError("group business result 缺少 completion")
                release_clean_session_transaction(
                    db,
                    label="group_before_claim_complete",
                    logger=logger,
                )
                completed = await owner.complete(result.completion)
                if completed is not True:
                    raise InboundClaimOwnershipLostError(
                        "群聊成功结算时已失去 claim owner"
                    )
            return result.payload
        except BaseException as exc:
            self._rollback_request_session_best_effort()
            if owner is not None and not fail_settlement_attempted:
                fail_settlement_attempted = True
                try:
                    await owner.fail(exc)
                except BaseException as settlement_error:
                    _raise_primary_with_secondary(
                        exc,
                        settlement_error,
                        label="群聊异常路径的 claim fail 结算再次失败",
                    )
            raise

    async def _execute_request(
        self,
        req: Any,
        *,
        group_user_id: str,
        attempt_count: int,
        claim_key: InboundClaimKey | None,
        claim_owner: InboundClaimOwner | None,
    ) -> GroupIngressResult:
        from core.timing_runtime import get_group_runtime

        db = self.db
        message_text = h.build_group_message_text(req)
        fingerprint_meta = h.build_group_message_meta(req, [])
        fingerprint_stickers = h.group_sticker_payloads(req)
        request_sha256 = ""
        if claim_key is not None:
            try:
                request_sha256 = recovery.group_business_input_sha256(
                    recovery.build_group_business_input(
                        req,
                        key=claim_key,
                        message_text=message_text,
                        message_meta=fingerprint_meta,
                        sticker_payloads=fingerprint_stickers,
                    )
                )
            except (TypeError, ValueError) as exc:
                return self._technical_result(
                    req,
                    exc,
                    reason="inbound_request_invalid",
                )

        existing_ambient = None
        if req.message_id:
            existing_ambient = (
                db.query(ChatLog)
                .filter(
                    ChatLog.session_id == group_user_id,
                    ChatLog.message_id == req.message_id,
                    ChatLog.role == "ambient",
                )
                .order_by(ChatLog.created_at.asc(), ChatLog.id.asc())
                .first()
            )
        if attempt_count == 1 and existing_ambient is not None:
            logger.info(
                "[GroupMsg] legacy duplicate ignored group=%s message_id=%s",
                group_user_id,
                req.message_id,
            )
            return self._business_result(
                req,
                outcome="no_reply",
                reason="duplicate_message",
            )

        reuse_ambient = attempt_count > 1 and existing_ambient is not None

        logger.info("[GroupMsg] recv group=%s sender=%s len=%d at=%s reply=%s",
                    req.group_id, req.sender_name, len(message_text or ""),
                    req.is_at_bot, req.is_reply_to_bot)

        if reuse_ambient:
            ambient_log = existing_ambient
            try:
                meta = recovery.verify_group_request_sha256(
                    ambient_log.meta_json,
                    request_sha256,
                )
                recovered = recovery.load_group_recoverable_completion(
                    db,
                    key=claim_key,
                    request_sha256=request_sha256,
                )
            except recovery.GroupRequestMismatchError as exc:
                return self._technical_result(
                    req,
                    exc,
                    reason="inbound_request_mismatch",
                )
            except recovery.GroupRecoveryCorruptError as exc:
                return self._technical_result(
                    req,
                    exc,
                    reason=f"inbound_recovery_corrupt: {exc}",
                )

            if recovered is not None:
                return GroupIngressResult(
                    payload=response_contract.completed_group_response_payload(
                        req,
                        recovered,
                    ),
                    completion=recovered,
                )
            logger.info(
                "[GroupMsg] ambient_reused group=%s message_id=%s attempt=%d",
                group_user_id,
                req.message_id,
                attempt_count,
            )
        else:
            registered_stickers = h.register_group_stickers_from_message(
                db,
                req,
                background_tasks=self.background_tasks,
            )
            if registered_stickers and self.background_tasks is None:
                release_clean_session_transaction(
                    db,
                    label="group_before_sticker_preview",
                    logger=logger,
                )
                await self._cache_registered_sticker_previews(registered_stickers)
            try:
                self._sync_group_user(group_user_id, req.session_name or "")
            except Exception as exc:
                if is_sqlite_locked_error(exc):
                    logger.warning("[GroupMsg] db locked while syncing group user group=%s: %s", req.group_id, exc)
                    return self._technical_result(
                        req,
                        exc,
                        reason="db_locked:group_user_sync",
                    )
                raise

            formatted = f"[{req.sender_name}]: {message_text}" if message_text else ""
            meta = h.build_group_message_meta(req, registered_stickers)
            if registered_stickers:
                meta["registered_sticker_ids"] = [item["id"] for item in registered_stickers]
            if claim_key is not None:
                meta = recovery.attach_group_request_fingerprint(
                    meta,
                    request_sha256,
                )
            self._schedule_image_precache(
                meta.get("files"),
                group_id=req.group_id,
                message_id=req.message_id or "",
            )
            try:
                ambient_log = self._save_ambient_log(
                    group_user_id=group_user_id,
                    sender_name=req.sender_name,
                    session_name=req.session_name,
                    formatted=formatted,
                    message_id=req.message_id,
                    meta=meta,
                )
            except Exception as exc:
                if is_sqlite_locked_error(exc):
                    logger.warning("[GroupMsg] db locked while saving ambient group=%s: %s", req.group_id, exc)
                    return self._technical_result(
                        req,
                        exc,
                        reason="db_locked:ambient_log",
                    )
                raise
            logger.info("[GroupMsg] ambient_saved group=%s message_id=%s", group_user_id, req.message_id or "-")

        from core.context_builder import build_timing_recent_context
        recent_ctx = build_timing_recent_context(
            db,
            group_user_id,
            limit=5,
            exclude_message_ids=[req.message_id] if req.message_id else None,
        )

        bot_sender_kind = str(meta.get("sender", {}).get("bot_sender_kind") or "")
        if bot_sender_kind == "current_bot":
            result = {
                "action": "no_reply",
                "reason": f"bot_sender:{bot_sender_kind}",
                "generation": 0,
                "hard_rule": "bot_sender_no_timing",
            }
            h.annotate_group_timing_event(
                db, ambient_log, result,
                trigger_reason="bot_sender",
                latency_ms=0,
            )
            return self._business_result(
                req,
                outcome="no_reply",
                generation=0,
                reason=f"bot_sender:{bot_sender_kind}",
                hard_rule="bot_sender_no_timing",
            )

        if h.check_user_blocked(db, req.sender_id, target_type="group", group_id=req.group_id):
            logger.info("[GroupMsg] blocked group=%s sender=%s", req.group_id, req.sender_id)
            h.annotate_group_timing_event(
                db, ambient_log,
                {"action": "no_reply", "reason": "user_blocked", "generation": 0},
                trigger_reason="user_blocked",
                latency_ms=0,
            )
            return self._business_result(
                req,
                outcome="blocked",
                reason="user_blocked",
                generation=0,
            )

        from core.group_runtime.ids import normalize_group_stream_id
        stream_id = normalize_group_stream_id(req.group_id)
        mod_result = check_message_moderation_db(db, message_text, chat_stream_id=stream_id)
        if mod_result:
            meta["moderation"] = {
                "matched": True,
                "match_type": "content_rule",
                "pattern": mod_result["pattern"],
                "rule_id": mod_result.get("rule_id"),
                "category": mod_result.get("category", ""),
                "rule_match_type": mod_result.get("match_type", "contains"),
                "scope_type": mod_result.get("scope_type", ""),
                "reason": mod_result.get("reason", ""),
                "no_reply": mod_result["no_reply"],
                "no_learn": mod_result["no_learn"],
                "no_context": mod_result["no_context"],
            }

            def operation() -> None:
                ambient_log.meta_json = json.dumps(meta, ensure_ascii=False)
                db.commit()

            run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="group_moderation_meta",
                logger=logger,
            )
            if mod_result["no_reply"]:
                logger.info("[GroupMsg] content-blocked group=%s pattern=%s",
                            req.group_id, mod_result["pattern"])
                h.annotate_group_timing_event(
                    db, ambient_log,
                    {"action": "no_reply", "reason": "content_blocked", "generation": 0},
                    trigger_reason="content_blocked",
                    latency_ms=0,
                )
                return self._business_result(
                    req,
                    outcome="blocked",
                    reason="content_blocked",
                    generation=0,
                )

        reason = h.derive_group_trigger_reason(req)
        logger.info("[GroupMsg] trigger=%s enter_timing=true", reason)

        runtime = get_group_runtime()
        t0 = _time.time()
        try:
            ambient_meta = meta
            timing_message = {
                "sender_id": req.sender_id,
                "sender_name": req.sender_name,
                "message": message_text,
                "message_id": req.message_id or "",
                "is_reply_to_bot": bool(ambient_meta.get("directed", {}).get("reply_to_bot")),
                "is_at_bot": bool(ambient_meta.get("directed", {}).get("at_bot")),
                "segments": ambient_meta.get("segments", []),
                "mentions": ambient_meta.get("mentions", []),
                "reply_to": ambient_meta.get("reply_to"),
                "directed": ambient_meta.get("directed", {}),
                "is_directed_to_other": bool(ambient_meta.get("directed", {}).get("directed_to_other")),
                "self_id": ambient_meta.get("bot", {}).get("self_id", ""),
                "bot_id": ambient_meta.get("bot", {}).get("bot_id", ""),
                "bot_name": ambient_meta.get("bot", {}).get("bot_name", ""),
                "is_other_bot": bot_sender_kind in {"explicit_bot", "client_meta"},
            }
            talk_value = h.get_group_talk_value(group_user_id)
            client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
            platform = str(client_meta.get("platform") or "qq").strip() or "qq"
            release_clean_session_transaction(db, label="group_before_timing_gate", logger=logger)
            result = await runtime.process_message(
                req.group_id,
                timing_message,
                session_name=req.session_name or "",
                bot_aliases=list(req.bot_aliases or []),
                trigger_reason=reason,
                recent_context=recent_ctx,
                talk_value=talk_value,
                platform=platform,
            )
            elapsed_ms = int((_time.time() - t0) * 1000)
            action = result.get("action", "no_reply")
            h.annotate_group_timing_event(
                db, ambient_log, result,
                trigger_reason=reason,
                latency_ms=elapsed_ms,
            )

            logger.info(
                "[GroupMsg] group=%s trigger=%s -> %s delay=%s gen=%d latency=%dms cooldown=%.0fs reason=%.80s",
                req.group_id, reason, action,
                result.get("delay_seconds"), result.get("generation", 0),
                elapsed_ms, result.get("cooldown_ago", 0) or 0,
                str(result.get("reason", ""))[:80],
            )

            if action != "continue":
                outcome = {
                    "wait": "wait",
                    "blocked": "blocked",
                    "silent": "silent",
                }.get(action, "no_reply")
                return self._business_result(
                    req,
                    outcome=outcome,
                    delay_seconds=result.get("delay_seconds"),
                    generation=result.get("generation", 0),
                    reason=str(result.get("reason", ""))[:120],
                )

        except Exception as exc:
            logger.warning("[GroupMsg] group=%s FAILED: %s", req.group_id, exc)
            return self._technical_result(
                req,
                exc,
                reason=f"error: {exc}",
            )

        return await self._continue_to_bridge(
            req=req,
            result=result,
            reason=reason,
            group_user_id=group_user_id,
            message_text=message_text,
            ambient_meta=ambient_meta,
            runtime=runtime,
            claim_key=claim_key,
            claim_owner=claim_owner,
            request_sha256=request_sha256,
        )

    async def _continue_to_bridge(
        self,
        *,
        req: Any,
        result: dict,
        reason: str,
        group_user_id: str,
        message_text: str,
        ambient_meta: dict,
        runtime: Any,
        claim_key: InboundClaimKey | None,
        claim_owner: InboundClaimOwner | None,
        request_sha256: str,
    ) -> GroupIngressResult:
        db = self.db
        try:
            if self.bridge_provider is None:
                from nanobot_kt.bridge import get_bridge

                bridge = get_bridge()
            else:
                bridge = self.bridge_provider()
            source_message_ids = [
                str(x) for x in (result.get("source_message_ids") or [])
                if str(x).strip()
            ]
            bridge_files = self._collect_bridge_files(
                group_user_id=group_user_id,
                source_message_ids=source_message_ids,
                ambient_meta=ambient_meta,
            )
            chat_query = str(result.get("pending_text") or "").strip()
            if not chat_query:
                chat_query = h.format_group_planner_message(
                    sender_name=req.sender_name,
                    content=message_text,
                    message_id=req.message_id or "",
                )
                source_message_ids = [req.message_id] if req.message_id else []
            memory_header, history_messages, ctx_debug = h.build_chat_context(
                db, group_user_id, user_id=group_user_id,
                is_group=True, group_id=req.group_id,
                exclude_message_ids=source_message_ids,
                current_user_input=chat_query,
            )
            sender_id = str(getattr(req, "sender_id", "") or getattr(req, "user_id", "") or "")
            is_super_user = is_super_user_id(sender_id)
            identity_vars = build_identity_vars(
                sender_id=sender_id,
                bot_name=ambient_meta.get("bot", {}).get("bot_name", ""),
                bot_aliases=list(req.bot_aliases or []),
                is_super_user=is_super_user,
            )
            client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
            platform = str(client_meta.get("platform") or "qq").strip().lower() or "qq"
            bridge_meta = {
                "chat_type": "group",
                "platform": platform,
                "user_id": group_user_id,
                "session_id": group_user_id,
                "sender_name": req.sender_name,
                "sender_id": sender_id,
                "is_group": True,
                "is_superuser": is_super_user,
                "history_header": memory_header,
                "history_messages": history_messages,
                "group_id": req.group_id,
                "session_name": req.session_name or "",
                "trigger_reason": reason,
                "message_id": req.message_id or "",
                "files": bridge_files,
                "timing_decision": "continue",
                "source_message_ids": source_message_ids,
                "context_debug": ctx_debug,
                "self_id": ambient_meta.get("bot", {}).get("self_id", ""),
                "bot_id": ambient_meta.get("bot", {}).get("bot_id", ""),
                "bot_name": ambient_meta.get("bot", {}).get("bot_name", ""),
                "bot_aliases": list(req.bot_aliases or []),
                **identity_vars,
            }
            enriched = f"<user_input>\n{chat_query}\n</user_input>"
            release_clean_session_transaction(db, label="group_before_bridge", logger=logger)
        except Exception as exc:
            logger.error("[GroupMsg] bridge failed group=%s: %s", req.group_id, exc)
            return self._technical_result(
                req,
                exc,
                reason=f"bridge_error: {exc}",
            )

        if claim_owner is not None:
            await claim_owner.checkpoint()

        try:
            reply = await bridge.handle_message(
                enriched, session_id=group_user_id, user_id=group_user_id,
                metadata=bridge_meta,
            )
            answer = reply if isinstance(reply, str) else str(reply or "")
            reply_meta = h.pop_bridge_reply_meta(bridge, group_user_id)
        except Exception as exc:
            logger.error("[GroupMsg] bridge failed group=%s: %s", req.group_id, exc)
            return self._technical_result(
                req,
                exc,
                reason=f"bridge_error: {exc}",
            )

        if claim_owner is not None:
            await claim_owner.checkpoint()

        try:
            if answer.strip():
                duplicate = h.find_recent_duplicate_group_reply(db, group_user_id, answer)
                if duplicate:
                    agent_result = "duplicate_reply_suppressed"
                    h.log_group_no_reply(db, group_user_id, chat_query, agent_result, req.message_id)
                    return self._business_result(
                        req,
                        outcome="no_reply",
                        reason=agent_result,
                        duplicate_reply=duplicate,
                        generation=result.get("generation", 0),
                    )
                business_result = self._business_result(
                    req,
                    outcome="respond",
                    reply=answer,
                    reply_meta=reply_meta,
                    generation=result.get("generation", 0),
                    reason=str(result.get("reason", ""))[:120],
                )
                assert business_result.completion is not None
                h.persist_group_bridge_reply(
                    db,
                    group_user_id=group_user_id,
                    sender_name=req.sender_name,
                    session_name=req.session_name or "",
                    query=chat_query,
                    answer=answer,
                    bot_name=ambient_meta.get("bot", {}).get("bot_name", "") or "nanobot",
                    message_id=req.message_id,
                    source_message_ids=source_message_ids,
                    reply_meta=reply_meta,
                    claim_key=claim_key,
                    request_sha256=request_sha256,
                    completion=business_result.completion,
                )
                runtime.note_bot_replied(req.group_id)
                return business_result
            else:
                agent_result = h.derive_group_agent_result(bridge, group_user_id, reply_meta)
                h.log_group_no_reply(db, group_user_id, chat_query, agent_result, req.message_id)
                if agent_result == "prompt_v2_audit_failed":
                    diagnostics = {
                            "timing_action": result.get("action", "continue"),
                            "agent_result": agent_result,
                    }
                    return self._technical_result(
                        req,
                        RuntimeError("prompt_v2_audit_failed"),
                        reply_meta=reply_meta,
                        generation=result.get("generation", 0),
                        reason=agent_result,
                        diagnostics=diagnostics,
                    )
                return self._business_result(
                    req,
                    outcome="no_reply",
                    generation=result.get("generation", 0),
                    reason=agent_result,
                )

        except Exception as exc:
            logger.error("[GroupMsg] bridge failed group=%s: %s", req.group_id, exc)
            return self._technical_result(
                req,
                exc,
                reason=f"bridge_error: {exc}",
            )

    def _sync_group_user(self, group_user_id: str, session_name: str) -> None:
        db = self.db

        def operation() -> None:
            user = db.query(User).filter(User.id == group_user_id).first()
            if not user:
                db.add(User(id=group_user_id, name=session_name or ""))
                db.commit()
                return
            if session_name and user.name != session_name:
                user.name = session_name
                db.commit()

        run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label="group_user_sync",
            logger=logger,
        )

    def _save_ambient_log(
        self,
        *,
        group_user_id: str,
        sender_name: str,
        session_name: str,
        formatted: str,
        message_id: str,
        meta: dict,
    ) -> ChatLog:
        db = self.db

        def operation() -> ChatLog:
            ambient_log = ChatLog(
                user_id=group_user_id,
                session_id=group_user_id,
                sender_name=sender_name,
                session_name=session_name,
                role="ambient",
                content=formatted,
                processed=1,
                message_id=message_id,
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            db.add(ambient_log)
            db.commit()
            db.refresh(ambient_log)
            return ambient_log

        return run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label="group_ambient_log",
            logger=logger,
        )

    def _schedule_image_precache(self, files: Any, *, group_id: str, message_id: str) -> None:
        normalized = h.normalize_files(files)
        if not normalized or self.background_tasks is None:
            return
        self.background_tasks.add_task(
            _precache_group_images_bg,
            normalized,
            group_id=group_id,
            message_id=message_id,
        )

    async def _cache_registered_sticker_previews(self, registered_stickers: list[dict]) -> None:
        from core.sticker_preview_jobs import cache_sticker_preview_bg

        for item in registered_stickers:
            sticker_id = int(item.get("id") or 0)
            if not sticker_id:
                continue
            try:
                await asyncio.to_thread(cache_sticker_preview_bg, sticker_id)
            except Exception as exc:
                logger.warning("[StickerPreview] register cache failed id=%s: %s", sticker_id, exc)

    def _collect_bridge_files(
        self,
        *,
        group_user_id: str,
        source_message_ids: list[str],
        ambient_meta: dict,
    ) -> list[str]:
        files: list[str] = []

        def add_many(values: Any) -> None:
            for item in h.normalize_files(values):
                if item not in files:
                    files.append(item)

        add_many(ambient_meta.get("files"))
        if not source_message_ids:
            return files

        rows = (
            self.db.query(ChatLog)
            .filter(
                ChatLog.session_id == group_user_id,
                ChatLog.role == "ambient",
                ChatLog.message_id.in_(source_message_ids),
            )
            .all()
        )
        for row in rows:
            add_many(h.safe_meta(row.meta_json).get("files"))
        return files


def _precache_group_images_bg(files: list[str], *, group_id: str, message_id: str) -> None:
    from nanobot_kt.image_pipeline import precache_image_sources

    results = precache_image_sources(
        files,
        source_type="group_message",
        source_name_prefix=f"group_{group_id}_{message_id or 'message'}",
    )
    ok_count = sum(1 for item in results if item.get("ok"))
    if ok_count < len(results):
        logger.warning(
            "[GroupMsg] image precache partial group=%s message_id=%s ok=%d total=%d",
            group_id,
            message_id or "-",
            ok_count,
            len(results),
        )
