"""受限的主动研究伙伴运行时。

本模块只生成并核验研究草稿，不包含任何消息发布依赖。
"""

from __future__ import annotations

import asyncio
import ipaddress
import inspect
import json
import math
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote_to_bytes, urlsplit

from core.agent_runtime.gateway_contracts import ResearchAgentRuntimePort
from core.model_provider.response_normalization import strip_think_blocks
from core.proactive_diagnostics import (
    generation_failure_from_exception,
    normalize_research_reason_code,
    safe_research_error,
)
from core.runtime_tool_service import RESEARCH_TOOL_NAMES
from core.web_search.url_policy import canonicalize_http_url


_EXPLORATION_TOOLS = frozenset({"web_search"})
_RESEARCH_ALLOWED_TOOLS = RESEARCH_TOOL_NAMES
_URL_PROSE_DELIMITERS = (
    "<>()[]{}\"'"
    "（）［］【】｛｝〈〉《》「」『』“”‘’"
    "，。；：！？、"
)
_URL_PATTERN = re.compile(
    rf"https?://[^\s{re.escape(_URL_PROSE_DELIMITERS)}]+",
    re.IGNORECASE,
)
_URL_TRAILING_PUNCTUATION = ".,;:!?，。；：！？、"
_INVALID_PERCENT_ESCAPE_PATTERN = re.compile(r"%(?![0-9a-f]{2})", re.IGNORECASE)
_PROTOCOL_RELATIVE_URL_PATTERN = re.compile(
    r"(?<!:)//[a-z0-9][^\s<>()\[\]{}\"']+",
    re.IGNORECASE,
)
_WWW_URL_PATTERN = re.compile(
    r"(?<![a-z0-9_/@.-])www\.[a-z0-9][^\s<>()\[\]{}\"']+",
    re.IGNORECASE,
)
_BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![a-z0-9_@./:-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?:/[^\s<>()\[\]{}\"']*)?",
    re.IGNORECASE,
)
_BARE_IPV4_PATTERN = re.compile(
    r"(?<![\d./:])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"
)
_DANGEROUS_SCHEME_PATTERN = re.compile(
    rf"(?:^|[\s{re.escape(_URL_PROSE_DELIMITERS)}])"
    r"(?:javascript|data|vbscript|file|ftp|ftps|mailto|"
    r"tel|magnet|smb|ssh|ws|wss):",
    re.IGNORECASE,
)
_OUTBOUND_CONTROL_PATTERN = re.compile(
    r"\[(?:cq|generated_image|sticker):",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])",
    re.IGNORECASE,
)
_LONG_DIGIT_PATTERN = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_GROUPED_LONG_DIGIT_PATTERN = re.compile(
    r"(?<!\d)(?:\d[\s._-]*){7,}(?!\d)"
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:\b(?:api[_ -]?key|access[_ -]?token|authorization|bearer|password|"
    r"secret)\b\s*[:=]|\bsk-[a-z0-9_-]{8,})"
)
_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?:\bfile://|(?:^|\s)/(?:home|root|etc|proc|sys|var)/|"
    r"\b[a-z]:\\(?:users|windows)\\)"
)
_IP_LITERAL_PATTERN = re.compile(
    r"(?<![0-9a-f:])(?:\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-f:]+\])"
    r"(?![0-9a-f:])",
    re.IGNORECASE,
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_DESTINATION_PATTERN = re.compile(
    r"\[[^\]\r\n]*\]\(\s*<?([^\s)>]+)>?",
    re.IGNORECASE,
)
_QUERY_PATTERN = re.compile(r"^QUERY:\s*(.*?)\s*$")
_QUALITY_PATTERN = re.compile(r"^QUALITY:\s*([a-z_]+)\s*$", re.IGNORECASE)
_RESULT_TITLE_PATTERN = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_RESULT_URL_PATTERN = re.compile(r"^\s*URL:\s*(https?://\S+)\s*$", re.IGNORECASE)
_RESULT_SNIPPET_PATTERN = re.compile(r"^\s*摘要:\s*(.*?)\s*$")
_RESULT_TIME_PATTERN = re.compile(r"^\s*时间:\s*(.*?)\s*$")
_RESULT_COUNT_PATTERN = re.compile(r"^RESULT_COUNT:\s*(\d+)\s*$")
_RESULTS_BEGIN = "WEB_SEARCH_RESULTS_BEGIN"
_RESULTS_END = "WEB_SEARCH_RESULTS_END"
_RESULTS_MARKER = "RESULTS:"
_EMPTY_RESULTS = "(无搜索结果)"
_RESULTS_FOOTER = (
    "只能基于以上 WEB_SEARCH_RESULTS 回答；如果结果与用户问题不匹配，必须重新调用 "
    "web_search，不能使用其它网页记忆或臆测。"
)
_BRIDGE_STOP_TIMEOUT_SECONDS = 1.0
_MAX_SOURCE_TITLE_CHARS = 200
_MAX_SOURCE_URL_CHARS = 2048
_MAX_SOURCE_SNIPPET_CHARS = 500
_MAX_TOOL_CALL_ID_CHARS = 128
_MAX_EXECUTED_QUERY_CHARS = 1000
_SENSITIVE_CJK_TERMS = (
    "身份证",
    "身份证号",
    "银行卡",
    "银行卡号",
    "手机号",
    "手机号码",
    "护照号",
    "密码",
    "令牌",
    "密钥",
)


@dataclass(frozen=True)
class ResearchBudget:
    timeout_seconds: float = 120.0
    max_exploration_calls: int = 6
    min_sources: int = 2
    max_sources: int = 20
    max_draft_chars: int = 6000

    def __post_init__(self) -> None:
        if type(self.timeout_seconds) not in (int, float):
            raise TypeError("timeout_seconds 必须是有限正数")
        if not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是有限正数")
        if self.timeout_seconds > 900:
            raise ValueError("timeout_seconds 不能超过 900 秒")

        integer_limits = {
            "max_exploration_calls": (self.max_exploration_calls, 0, 50),
            "min_sources": (self.min_sources, 1, 50),
            "max_sources": (self.max_sources, 1, 50),
            "max_draft_chars": (self.max_draft_chars, 1, 100_000),
        }
        for name, (value, minimum, maximum) in integer_limits.items():
            if type(value) is not int:
                raise TypeError(f"{name} 必须是整数")
            if value < minimum or value > maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        if self.max_sources < self.min_sources:
            raise ValueError("max_sources 不能小于 min_sources")


@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    query: str
    user_id: str
    context_summary: str = ""
    budget: ResearchBudget = field(default_factory=ResearchBudget)


@dataclass(frozen=True)
class ResearchSource:
    tool_call_id: str
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchResult:
    request_id: str
    trace_id: str
    status: str
    reason_code: str = ""
    draft: str = ""
    sources: tuple[ResearchSource, ...] = ()
    exploration_calls: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sources"] = [source.to_dict() for source in self.sources]
        payload["reason_code"] = normalize_research_reason_code(self.reason_code)
        payload["error"] = safe_research_error(
            reason_code=payload["reason_code"],
            error=self.error,
        )
        return payload


def _contains_sensitive_search_data(value: str) -> bool:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    compact = re.sub(r"[\s·•._-]+", "", text)
    if (
        _EMAIL_PATTERN.search(text)
        or _EMAIL_PATTERN.search(compact)
        or _LONG_DIGIT_PATTERN.search(text)
        or _LONG_DIGIT_PATTERN.search(compact)
        or _GROUPED_LONG_DIGIT_PATTERN.search(text)
        or _SECRET_PATTERN.search(text)
        or _LOCAL_PATH_PATTERN.search(text)
        or any(term in compact for term in _SENSITIVE_CJK_TERMS)
    ):
        return True
    for match in _IP_LITERAL_PATTERN.finditer(text):
        literal = match.group(0).strip("[]")
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if not address.is_global:
            return True
    return False


def _contains_outbound_control_syntax(value: str) -> bool:
    return bool(_OUTBOUND_CONTROL_PATTERN.search(str(value or "")))


class ResearchToolBlockError(RuntimeError):
    """研究预算或安全策略拒绝工具调用。"""


class ResearchBudgetPlugin:
    """框架无关的研究探索预算守卫。"""

    name = "nanobot_research_budget"
    priority = 1

    def __init__(self, *, budget: ResearchBudget, research_query: str = ""):
        self.budget = budget
        self.research_query = str(research_query or "").strip()
        self.exploration_calls = 0
        self.blocked_calls = 0
        self.exhausted = False
        self._lock = asyncio.Lock()

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        tool_name = str(kwargs.get("tool_name") or "")
        if tool_name not in _RESEARCH_ALLOWED_TOOLS:
            raise ResearchToolBlockError(
                f"研究预设不允许调用工具 {tool_name or '<empty>'}"
            )
        if tool_name not in _EXPLORATION_TOOLS:
            return None
        if tool_name == "web_search":
            executed_query = args.get("query") if isinstance(args, dict) else None
            if (
                not isinstance(executed_query, str)
                or not executed_query.strip()
                or len(executed_query) > _MAX_EXECUTED_QUERY_CHARS
                or _contains_sensitive_search_data(executed_query)
            ):
                raise ResearchToolBlockError(
                    "研究搜索 query 不满足公开检索契约"
                )
            if self.research_query:
                from core.web_search.relevance import judge_executed_query_relevance

                if not judge_executed_query_relevance(
                    self.research_query,
                    executed_query,
                ).ok:
                    raise ResearchToolBlockError(
                        "研究搜索 query 超出原始研究主题"
                    )
        async with self._lock:
            limit = max(0, int(self.budget.max_exploration_calls))
            if self.exploration_calls >= limit:
                self.blocked_calls += 1
                self.exhausted = True
                raise ResearchToolBlockError(
                    f"研究探索调用已达到上限 {limit}"
                )
            self.exploration_calls += 1
        return None

    async def pre_subagent_run(self, task: str, **kwargs: Any) -> str | None:
        """研究预设不允许任何 SubAgent，避免绕过 ToolCall 工具上限。"""

        name = str(kwargs.get("name") or "")
        raise ResearchToolBlockError(
            f"研究预设不允许调用子 Agent {name or '<empty>'}"
        )

    async def snapshot(self) -> dict[str, int | bool]:
        async with self._lock:
            return {
                "exploration_calls": self.exploration_calls,
                "blocked_calls": self.blocked_calls,
                "exhausted": self.exhausted,
            }


def _canonical_url(raw: str) -> str:
    return canonicalize_http_url(raw)


def _strict_percent_decode(value: str) -> str | None:
    """严格解码 URL 组件；畸形 percent escape 或非法 UTF-8 返回 ``None``。"""

    if _INVALID_PERCENT_ESCAPE_PATTERN.search(value):
        return None
    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None


def _discarded_component_is_ambiguous(value: str) -> bool:
    decoded = _strict_percent_decode(value)
    return decoded is None or not decoded.isascii()


def _has_ambiguous_discarded_non_ascii(
    raw_url: str,
    canonical_url: str,
) -> bool:
    """判断 canonicalization 删除的 URL 部分是否可能粘连正文。"""

    try:
        raw_parts = urlsplit(raw_url)
        canonical_parts = urlsplit(canonical_url)
        remaining_query_items = list(
            parse_qsl(canonical_parts.query, keep_blank_values=True)
        )
    except (TypeError, ValueError):
        return True

    for raw_segment in raw_parts.query.split("&"):
        if not raw_segment:
            continue
        try:
            parsed_segment = parse_qsl(raw_segment, keep_blank_values=True)
        except ValueError:
            if _discarded_component_is_ambiguous(raw_segment):
                return True
            continue
        if len(parsed_segment) != 1:
            if _discarded_component_is_ambiguous(raw_segment):
                return True
            continue
        try:
            kept_index = remaining_query_items.index(parsed_segment[0])
        except ValueError:
            if _discarded_component_is_ambiguous(raw_segment):
                return True
        else:
            remaining_query_items.pop(kept_index)

    return bool(
        raw_parts.fragment
        and _discarded_component_is_ambiguous(raw_parts.fragment)
    )


def normalize_research_publication_text(
    text: str,
    sources: list[Any] | tuple[Any, ...],
) -> str:
    """把正文中的已核验 HTTP(S) URL 变体重写为 canonical URL。"""

    verified_urls: set[str] = set()
    for source in sources:
        raw_url = (
            source.get("url")
            if isinstance(source, dict)
            else getattr(source, "url", "")
        )
        canonical = _canonical_url(raw_url)
        if canonical:
            verified_urls.add(canonical)

    def replace_verified_url(match: re.Match[str]) -> str:
        token = match.group(0)
        candidate = token.rstrip(_URL_TRAILING_PUNCTUATION)
        trailing_punctuation = token[len(candidate):]
        canonical = _canonical_url(candidate)
        if not canonical or canonical not in verified_urls:
            return token
        if _has_ambiguous_discarded_non_ascii(candidate, canonical):
            return token
        return f"{canonical}{trailing_punctuation}"

    return _URL_PATTERN.sub(replace_verified_url, str(text or ""))


@dataclass(frozen=True)
class _URLScan:
    canonical_urls: frozenset[str]
    invalid_candidates: tuple[str, ...]


def _scan_url_references(text: str) -> _URLScan:
    raw_text = str(text or "")
    canonical_urls: set[str] = set()
    invalid_candidates: list[str] = []
    seen_candidates: set[str] = set()

    def inspect_candidate(candidate: str, *, require_absolute: bool = True) -> None:
        normalized = (
            str(candidate or "")
            .strip()
            .strip("<>")
            .rstrip(".,;:!?，。；：！？、")
        )
        if not normalized or normalized in seen_candidates:
            return
        seen_candidates.add(normalized)
        if (
            require_absolute
            and (
                normalized.startswith("//")
                or normalized.lower().startswith("www.")
            )
        ):
            invalid_candidates.append(normalized)
            return
        canonical = _canonical_url(normalized)
        if canonical:
            canonical_urls.add(canonical)
            if canonical != normalized:
                invalid_candidates.append(normalized)
        else:
            invalid_candidates.append(normalized)

    for match in _MARKDOWN_DESTINATION_PATTERN.finditer(raw_text):
        inspect_candidate(match.group(1))
    for pattern in (
        _URL_PATTERN,
        _PROTOCOL_RELATIVE_URL_PATTERN,
        _WWW_URL_PATTERN,
        _BARE_DOMAIN_PATTERN,
        _BARE_IPV4_PATTERN,
    ):
        for match in pattern.finditer(raw_text):
            inspect_candidate(match.group(0))
    if _DANGEROUS_SCHEME_PATTERN.search(raw_text):
        invalid_candidates.append("dangerous_scheme")
    return _URLScan(
        canonical_urls=frozenset(canonical_urls),
        invalid_candidates=tuple(dict.fromkeys(invalid_candidates)),
    )


def _canonical_urls_in(text: str) -> set[str]:
    return set(_scan_url_references(text).canonical_urls)


def _has_dangerous_link(text: str) -> bool:
    return bool(_scan_url_references(text).invalid_candidates)


def validate_research_publication_text(
    text: str,
    sources: list[Any] | tuple[Any, ...],
) -> str:
    """复核最终将发布的研究纯文本，返回空串表示通过。"""

    if _contains_outbound_control_syntax(text):
        return "unsafe_control_syntax"
    scan = _scan_url_references(text)
    verified_urls: set[str] = set()
    for source in sources:
        raw_url = (
            source.get("url")
            if isinstance(source, dict)
            else getattr(source, "url", "")
        )
        canonical = _canonical_url(raw_url)
        if canonical:
            verified_urls.add(canonical)
    if scan.invalid_candidates or set(scan.canonical_urls) - verified_urls:
        return "unverified_url"
    return ""


def _plain_source_title(raw: str) -> str:
    title = _MARKDOWN_LINK_PATTERN.sub(r"\1", str(raw or ""))
    title = _URL_PATTERN.sub("", title)
    title = _PROTOCOL_RELATIVE_URL_PATTERN.sub("", title)
    title = _WWW_URL_PATTERN.sub("", title)
    title = _BARE_DOMAIN_PATTERN.sub("", title)
    title = _BARE_IPV4_PATTERN.sub("", title)
    title = re.sub(r"[\r\n\t]+", " ", title)
    title = re.sub(r"[*_`<>]", "", title)
    title = title.translate(str.maketrans({"[": "［", "]": "］"}))
    title = re.sub(r"\s+", " ", title).strip(" -—:：")
    return (title or "网页来源")[:_MAX_SOURCE_TITLE_CHARS]


def _normalized_query(value: str) -> str:
    return " ".join(str(value or "").split())


def _search_envelope_metadata(tool_call: Any) -> tuple[str, str] | None:
    lines = str(getattr(tool_call, "result_preview", "") or "").splitlines()
    stripped_lines = [line.strip() for line in lines]
    if _RESULTS_MARKER not in stripped_lines:
        return None
    results_index = stripped_lines.index(_RESULTS_MARKER)
    query_matches = [
        match
        for line in stripped_lines[:results_index]
        if (match := _QUERY_PATTERN.fullmatch(line)) is not None
    ]
    quality_matches = [
        match
        for line in stripped_lines[:results_index]
        if (match := _QUALITY_PATTERN.fullmatch(line)) is not None
    ]
    if len(query_matches) != 1 or len(quality_matches) != 1:
        return None
    envelope_query = _normalized_query(query_matches[0].group(1))
    if not envelope_query:
        return None
    try:
        args = json.loads(str(getattr(tool_call, "args_json", "") or "{}"))
    except json.JSONDecodeError:
        return None
    args_query = _normalized_query(args.get("query")) if isinstance(args, dict) else ""
    if not args_query or args_query != envelope_query:
        return None
    return envelope_query, quality_matches[0].group(1).lower()


def _safe_body_prefix(text: str, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if max_chars <= 0:
        return ""
    if len(normalized) <= max_chars:
        return normalized

    cutoff = max_chars
    url_spans = [match.span() for match in _URL_PATTERN.finditer(normalized)]
    for start, end in url_spans:
        if start < cutoff < end:
            cutoff = start
            break

    prefix = normalized[:cutoff].rstrip()
    if not prefix:
        return ""

    search_start = max(0, len(prefix) - 120)
    for index in range(len(prefix) - 1, search_start - 1, -1):
        if prefix[index] not in "\n。！？!?；; \t":
            continue
        boundary = index if prefix[index].isspace() else index + 1
        if any(start < boundary < end for start, end in url_spans):
            continue
        bounded = prefix[:boundary].rstrip()
        if bounded:
            return bounded
    return prefix


def _sources_from_preview(tool_call: Any) -> list[ResearchSource]:
    metadata = _search_envelope_metadata(tool_call)
    if metadata is None or metadata[1] != "ok":
        return []
    lines = str(getattr(tool_call, "result_preview", "") or "").splitlines()
    stripped_lines = [line.strip() for line in lines]
    if (
        not stripped_lines
        or stripped_lines[0] != _RESULTS_BEGIN
        or stripped_lines[-1] != _RESULTS_END
        or stripped_lines.count(_RESULTS_BEGIN) != 1
        or stripped_lines.count(_RESULTS_END) != 1
        or stripped_lines.count(_RESULTS_MARKER) != 1
    ):
        return []

    count_matches = [
        match
        for line in stripped_lines
        if (match := _RESULT_COUNT_PATTERN.fullmatch(line)) is not None
    ]
    if len(count_matches) != 1:
        return []
    expected_count = int(count_matches[0].group(1))
    results_index = stripped_lines.index(_RESULTS_MARKER)
    count_index = next(
        index
        for index, line in enumerate(stripped_lines)
        if _RESULT_COUNT_PATTERN.fullmatch(line)
    )
    if count_index >= results_index:
        return []

    call_id = str(getattr(tool_call, "tool_call_id", "") or "")[
        :_MAX_TOOL_CALL_ID_CHARS
    ]
    sources: list[ResearchSource] = []
    result_lines = lines[results_index + 1 : -1]
    cursor = 0

    if expected_count == 0:
        if result_lines and result_lines[0].strip() == _EMPTY_RESULTS:
            cursor = 1
    else:
        for expected_index in range(1, expected_count + 1):
            if cursor >= len(result_lines):
                return []
            title_match = _RESULT_TITLE_PATTERN.fullmatch(result_lines[cursor])
            if (
                title_match is None
                or int(title_match.group(1)) != expected_index
                or not title_match.group(2).strip()
            ):
                return []
            title = _plain_source_title(title_match.group(2))
            cursor += 1

            if cursor >= len(result_lines):
                return []
            url_match = _RESULT_URL_PATTERN.fullmatch(result_lines[cursor])
            if url_match is None:
                return []
            url = _canonical_url(url_match.group(1))
            if not url or len(url) > _MAX_SOURCE_URL_CHARS:
                return []
            cursor += 1

            snippet = ""
            seen_snippet = False
            seen_time = False
            while cursor < len(result_lines):
                line = result_lines[cursor]
                if _RESULT_TITLE_PATTERN.fullmatch(line):
                    break
                snippet_match = _RESULT_SNIPPET_PATTERN.fullmatch(line)
                if snippet_match is not None and not seen_snippet:
                    snippet = snippet_match.group(1).strip()[:_MAX_SOURCE_SNIPPET_CHARS]
                    seen_snippet = True
                    cursor += 1
                    continue
                time_match = _RESULT_TIME_PATTERN.fullmatch(line)
                if time_match is not None and not seen_time:
                    seen_time = True
                    cursor += 1
                    continue
                break

            sources.append(
                ResearchSource(
                    tool_call_id=call_id,
                    title=title,
                    url=url,
                    snippet=snippet,
                )
            )

    remaining_lines = [line.strip() for line in result_lines[cursor:] if line.strip()]
    if remaining_lines not in ([], [_RESULTS_FOOTER]):
        return []
    if len(sources) != expected_count:
        return []
    return sources


def extract_verified_web_sources(
    tool_calls: list[Any],
    *,
    research_query: str = "",
) -> list[ResearchSource]:
    """仅从成功的 web_search trace 结果中提取网页来源。"""

    sources: list[ResearchSource] = []
    seen: set[str] = set()
    for call in tool_calls or []:
        if str(getattr(call, "tool_name", "") or "") != "web_search":
            continue
        if str(getattr(call, "status", "") or "").lower() != "success":
            continue
        metadata = _search_envelope_metadata(call)
        if metadata is None or metadata[1] != "ok":
            continue
        call_sources = _sources_from_preview(call)
        if research_query:
            from core.web_search.relevance import judge_executed_query_relevance

            if not judge_executed_query_relevance(
                research_query,
                metadata[0],
            ).ok:
                continue
        for source in call_sources:
            if source.url in seen:
                continue
            seen.add(source.url)
            sources.append(source)
    return sources


def _default_source_loader(trace_id: str) -> list[Any]:
    from core.database import SessionLocal, ToolCall

    session = SessionLocal()
    try:
        return (
            session.query(ToolCall)
            .filter(ToolCall.trace_id == trace_id)
            .order_by(ToolCall.started_at.asc(), ToolCall.tool_call_id.asc())
            .all()
        )
    finally:
        session.close()


async def _load_sources(
    source_loader: Callable[[str], list[Any]],
    trace_id: str,
) -> list[Any]:
    loaded = await asyncio.to_thread(source_loader, trace_id)
    if inspect.isawaitable(loaded):
        loaded = await loaded
    return list(loaded or [])


def _research_prompt(request: ResearchRequest) -> str:
    payload = json.dumps(
        {
            "request_id": request.request_id,
            "query": request.query,
            "context_summary": request.context_summary,
            "max_exploration_calls": request.budget.max_exploration_calls,
            "max_draft_chars": request.budget.max_draft_chars,
        },
        ensure_ascii=False,
    )
    from core.prompt_v2.task_templates import render_task_prompt

    return render_task_prompt(
        "proactive_research",
        {"pending_text": payload},
        fallback_text=f"执行受限研究任务：{payload}",
    )


def _source_block(sources: list[ResearchSource]) -> str:
    lines = ["来源（本次真实检索）："]
    for index, source in enumerate(sources, start=1):
        lines.append(f"{index}. {source.title}")
        lines.append(f"   {source.url}")
    return "\n".join(lines)


def _result(
    request: ResearchRequest,
    trace_id: str,
    *,
    status: str,
    reason_code: str = "",
    draft: str = "",
    sources: list[ResearchSource] | tuple[ResearchSource, ...] = (),
    exploration_calls: int = 0,
    error: str = "",
) -> ResearchResult:
    return ResearchResult(
        request_id=request.request_id,
        trace_id=trace_id,
        status=status,
        reason_code=normalize_research_reason_code(reason_code),
        draft=draft,
        sources=tuple(sources),
        exploration_calls=exploration_calls,
        error=safe_research_error(reason_code=reason_code, error=error),
    )


async def run_proactive_research(
    request: ResearchRequest,
    *,
    bridge_factory: Callable[[], ResearchAgentRuntimePort] | None = None,
    source_loader: Callable[[str], list[Any]] | None = None,
) -> ResearchResult:
    """运行一次研究任务并返回经 ToolCall 证据核验的草稿。"""

    trace_id = uuid.uuid4().hex
    if not request.query.strip():
        return _result(request, trace_id, status="blocked", reason_code="empty_query")
    if _contains_sensitive_search_data(request.query):
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="sensitive_query",
        )

    if bridge_factory is None:
        from core.agent_runtime.gateway import (
            create_research_agent_runtime,
        )

        bridge_factory = create_research_agent_runtime
    bridge: ResearchAgentRuntimePort | None = None
    lifecycle_task: asyncio.Task[tuple[str, list[Any], str]] | None = None
    budget_plugin = ResearchBudgetPlugin(
        budget=request.budget,
        research_query=request.query,
    )
    source_loader = source_loader or _default_source_loader

    async def execute_lifecycle() -> tuple[str, list[Any], str]:
        if bridge is None:
            raise RuntimeError("研究 Bridge 尚未初始化")
        await bridge.start()
        if not bridge.research_tool_guards_ready():
            return "", [], "tool_guard_unavailable"
        if not bridge.install_research_budget_guard(budget_plugin):
            return "", [], "budget_guard_unavailable"
        response = await bridge.handle_message(
            _research_prompt(request),
            user_id=request.user_id,
            session_id=f"research_{request.request_id}",
            sender_name="主动研究任务",
            metadata={
                "trace_id": trace_id,
                "message_id": request.request_id,
                "platform": "internal",
                "chat_type": "research",
                "is_group": False,
                "is_superuser": False,
                "user_id": request.user_id,
                "runtime_preset": "research",
                "dry_run": True,
            },
        )
        calls = await _load_sources(source_loader, trace_id)
        return str(response or ""), calls, ""

    try:
        bridge = bridge_factory()
        timeout_seconds = max(0.001, float(request.budget.timeout_seconds))
        lifecycle_task = asyncio.create_task(execute_lifecycle())
        done, _pending = await asyncio.wait(
            {lifecycle_task},
            timeout=timeout_seconds,
        )
        if lifecycle_task not in done:
            lifecycle_task.cancel()
            return _result(
                request,
                trace_id,
                status="blocked",
                reason_code="timeout",
                exploration_calls=budget_plugin.exploration_calls,
            )
        response, calls, guard_error = lifecycle_task.result()
        if guard_error:
            return _result(
                request,
                trace_id,
                status="blocked",
                reason_code=guard_error,
                exploration_calls=budget_plugin.exploration_calls,
            )
    except asyncio.CancelledError:
        if lifecycle_task is not None and not lifecycle_task.done():
            lifecycle_task.cancel()
        raise
    except Exception as exc:
        diagnostic = generation_failure_from_exception(exc)
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="runtime_error",
            exploration_calls=budget_plugin.exploration_calls,
            error=diagnostic.summary,
        )
    finally:
        if lifecycle_task is not None and not lifecycle_task.done():
            lifecycle_task.cancel()

            def consume_late_result(task: asyncio.Task[Any]) -> None:
                try:
                    task.result()
                except BaseException:
                    pass

            lifecycle_task.add_done_callback(consume_late_result)
        if bridge is not None:
            try:
                await asyncio.wait_for(
                    bridge.stop(),
                    timeout=_BRIDGE_STOP_TIMEOUT_SECONDS,
                )
            except Exception:
                pass

    budget_state = await budget_plugin.snapshot()
    if bool(budget_state["exhausted"]):
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="budget_exhausted",
            exploration_calls=int(budget_state["exploration_calls"]),
        )

    draft = strip_think_blocks(str(response or "")).strip()
    sources = extract_verified_web_sources(
        calls,
        research_query=request.query,
    )[
        : max(0, int(request.budget.max_sources))
    ]
    if not draft:
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="empty_draft",
            sources=sources,
            exploration_calls=budget_plugin.exploration_calls,
        )
    if len(sources) < max(1, int(request.budget.min_sources)):
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="insufficient_sources",
            sources=sources,
            exploration_calls=budget_plugin.exploration_calls,
        )

    draft = normalize_research_publication_text(draft, sources)
    publication_error = validate_research_publication_text(draft, sources)
    if publication_error:
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code=publication_error,
            sources=sources,
            exploration_calls=budget_plugin.exploration_calls,
        )

    source_block = _source_block(sources)
    max_chars = max(0, int(request.budget.max_draft_chars))
    body_limit = max_chars - len(source_block) - 2
    verified_body = _safe_body_prefix(draft, body_limit)
    if not verified_body:
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code="draft_budget_too_small",
            sources=sources,
            exploration_calls=budget_plugin.exploration_calls,
        )

    verified_draft = f"{verified_body}\n\n{source_block}"
    publication_error = validate_research_publication_text(
        verified_draft,
        sources,
    )
    if (
        len(verified_draft) > max_chars
        or publication_error
    ):
        reason_code = (
            "draft_budget_too_small"
            if len(verified_draft) > max_chars
            else publication_error
        )
        return _result(
            request,
            trace_id,
            status="blocked",
            reason_code=reason_code,
            sources=sources,
            exploration_calls=budget_plugin.exploration_calls,
        )
    return _result(
        request,
        trace_id,
        status="draft_ready",
        draft=verified_draft,
        sources=sources,
        exploration_calls=budget_plugin.exploration_calls,
    )
