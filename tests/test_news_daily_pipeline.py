"""news_daily EventCluster 管线单元测试——normalize_v2 / freshness / cluster / diversify / tool。"""
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.normalize_v2 import (
    parse_date,
    normalize_title,
    token_set,
    extract_entities,
    extract_topic_keys,
    compute_source_quality,
    normalize_articles,
)
from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.freshness import (
    compute_freshness,
    filter_fresh_articles,
    compute_cluster_freshness,
    can_be_top_story,
)
from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.cluster import (
    jaccard,
    article_similarity,
    cluster_articles,
)
from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.diversify import (
    score_clusters,
    select_diverse_clusters,
    pick_top_story,
    build_daily_report,
)
from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.models import (
    Article,
    EventCluster,
    NewsReport,
)
from creatures.nanobot.prompts.skills.news_search.news_daily.tool import (
    _route_mode,
    _apply_quotas,
    _report_to_digest,
)


# ── helpers ──────────────────────────────────────────────

@dataclass
class MockRawItem:
    """模拟 RSS 采集原始条目，包含 normalize_articles 需要的字段。"""
    id: str = ""
    title: str = ""
    url: str = ""
    source_name: str = ""
    source_group: str = "curated"
    domain: str = ""
    published_at: str = ""
    summary: str = ""
    detail_text: str = ""
    content_excerpt: str = ""


def make_article(aid="a1", title="Test", domain="example.com", source_group="ai_media",
                 published_at=None, source_name="Test Source", entity_keys=None,
                 topic_keys=None, is_official=False, summary="", freshness=0.85,
                 source_quality=0.75):
    """快捷构造 Article 对象。"""
    return Article(
        id=aid, title=title, url=f"https://{domain}/{aid}",
        source=source_name, source_group=source_group, domain=domain,
        published_at=published_at,
        title_norm=normalize_title(title),
        entity_keys=entity_keys or [],
        topic_keys=topic_keys or [],
        freshness_score=freshness,
        source_quality_score=source_quality,
        is_official=is_official,
        summary=summary,
    )


def make_cluster(cid="c1", articles=None, entities=None, keywords=None,
                 latest_seen=None, is_single_source=True, representative=None):
    """快捷构造 EventCluster。representative 未提供时自动选择 quality 最高的。"""
    if articles is None:
        articles = []
    if representative is None and articles:
        representative = max(articles, key=lambda a: a.source_quality_score)
    c = EventCluster(
        id=cid, title=articles[0].title if articles else "No Title",
        articles=articles, entities=entities or [],
        keywords=keywords or [], representative=representative,
    )
    c.latest_seen = latest_seen
    c.source_domains = {a.domain for a in articles}
    c.is_single_source = is_single_source
    return c


# ============================================================
# normalize_v2 测试
# ============================================================

class TestParseDate:
    """parse_date —— ISO 8601 / RFC 2822 / 常见变体。"""

    def test_iso_8601_with_t(self):
        dt = parse_date("2026-05-04T10:30:00")
        assert dt == datetime(2026, 5, 4, 10, 30, 0)

    def test_iso_8601_with_z(self):
        dt = parse_date("2026-05-04T08:00:00Z")
        assert dt is not None
        assert dt.date().isoformat() == "2026-05-04"

    def test_rfc_2822(self):
        dt = parse_date("Mon, 04 May 2026 10:30:00 GMT")
        assert dt is not None
        assert dt.date().isoformat() == "2026-05-04"

    def test_date_only_dash(self):
        dt = parse_date("2026-05-04")
        assert dt == datetime(2026, 5, 4)

    def test_date_only_slash(self):
        dt = parse_date("2026/05/04")
        assert dt == datetime(2026, 5, 4)

    def test_month_name_comma(self):
        dt = parse_date("May 04, 2026")
        assert dt == datetime(2026, 5, 4)

    def test_empty_string_returns_none(self):
        assert parse_date("") is None

    def test_nonsense_returns_none(self):
        assert parse_date("not a date") is None

    def test_datetime_with_tz_offset(self):
        dt = parse_date("2026-05-04T10:30:00+08:00")
        assert dt is not None
        assert dt.date().isoformat() == "2026-05-04"


class TestNormalizeTitle:
    def test_lowercase_and_strip_url(self):
        result = normalize_title("Hello World https://example.com")
        assert result == "hello world"

    def test_removes_punctuation(self):
        result = normalize_title("OpenAI's GPT-5: The Next Generation!")
        assert "openai" in result
        assert "gpt 5" in result
        assert "'" not in result

    def test_preserves_chinese(self):
        result = normalize_title("深度求索发布 DeepSeek-V3 模型")
        assert "深度求索" in result
        assert "deepseek" in result


class TestTokenSet:
    def test_filters_stop_words(self):
        tokens = token_set("OpenAI 发布 全新 模型 GPT-5")
        assert "发布" not in tokens
        assert "openai" in tokens
        assert "gpt" in tokens

    def test_filters_short_tokens(self):
        tokens = token_set("a b c AI model release")
        assert "a" not in tokens
        assert "ai" in tokens


class TestExtractEntities:
    def test_kimi_entity(self):
        ents = extract_entities("Kimi 发布新模型，月之暗面最新产品")
        assert "kimi" in ents

    def test_openai_entity(self):
        ents = extract_entities("OpenAI 宣布 ChatGPT 支持新功能 GPT-5")
        assert "openai" in ents

    def test_multiple_entities(self):
        ents = extract_entities("OpenAI GPT-5 vs Anthropic Claude 4 vs DeepSeek V3")
        assert "openai" in ents
        assert "anthropic" in ents
        assert "deepseek" in ents

    def test_no_entity(self):
        ents = extract_entities("今天天气真好")
        assert ents == []


class TestExtractTopicKeys:
    def test_model_release(self):
        keys = extract_topic_keys("OpenAI 发布 GPT-5 新模型开源")
        assert "model_release" in keys

    def test_benchmark(self):
        keys = extract_topic_keys("Claude 在 SWE-bench 评测中达到新纪录")
        assert "benchmark" in keys

    def test_research(self):
        keys = extract_topic_keys("新论文在 arxiv 上发布，研究 transformer 架构")
        assert "research" in keys


class TestComputeSourceQuality:
    def test_core_provider(self):
        assert compute_source_quality("core_provider") == 1.0

    def test_core_platform(self):
        assert compute_source_quality("core_platform") == 0.9

    def test_unknown_default(self):
        assert compute_source_quality("some_random_group") == 0.3


class TestNormalizeArticles:
    """normalize_articles —— 从原始条目构造 Article 列表。"""

    def test_basic_normalization(self):
        now = datetime.now()
        items = [
            MockRawItem(
                id="a1", title="OpenAI 发布 GPT-5",
                url="https://openai.com/gpt5",
                source_name="OpenAI Blog", source_group="core_provider",
                domain="openai.com",
                published_at=now.strftime("%Y-%m-%d"),
                summary="GPT-5 achieves new benchmarks",
            ),
        ]
        articles = normalize_articles(items)
        assert len(articles) == 1
        a = articles[0]
        assert a.id == "a1"
        assert a.title == "OpenAI 发布 GPT-5"
        assert a.source_group == "core_provider"
        assert a.source_quality_score == 1.0
        assert a.is_official is True
        assert "openai" in a.entity_keys

    def test_unknown_time(self):
        items = [
            MockRawItem(id="a1", title="Some News", url="https://x.com/news",
                        source_name="X", domain="x.com",
                        published_at=""),
        ]
        articles = normalize_articles(items)
        assert len(articles) == 1
        assert articles[0].is_time_unknown is True
        assert articles[0].published_at is None

    def test_infers_missing_id(self):
        items = [
            MockRawItem(id="", title="Test Article", url="https://example.com/test",
                        source_name="Example", domain="example.com"),
        ]
        articles = normalize_articles(items)
        assert len(articles) == 1
        assert len(articles[0].id) == 12  # MD5[:12]

    def test_entity_extraction_from_title_and_summary(self):
        items = [
            MockRawItem(id="a1", title="Kimi 发布新版本",
                        url="https://moonshot.cn/kimi",
                        source_name="Moonshot", domain="moonshot.cn",
                        summary="月之暗面推出 Kimi 大模型更新"),
        ]
        articles = normalize_articles(items)
        assert "kimi" in articles[0].entity_keys

    def test_topic_extraction(self):
        items = [
            MockRawItem(id="a1", title="新论文发布 transformer 架构改进",
                        url="https://arxiv.org/abs/test",
                        source_name="Arxiv", domain="arxiv.org",
                        source_group="research",
                        summary="研究团队发布关于 transformer 的新论文"),
        ]
        articles = normalize_articles(items)
        assert "research" in articles[0].topic_keys


# ============================================================
# freshness 测试
# ============================================================

class TestComputeFreshness:
    def test_within_6_hours(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=2))
        assert compute_freshness(a, now) == 1.0

    def test_6_to_24_hours(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=12))
        assert compute_freshness(a, now) == 0.85

    def test_24_to_48_hours(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=36))
        assert compute_freshness(a, now) == 0.65

    def test_48_to_72_hours(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=60))
        assert compute_freshness(a, now) == 0.35

    def test_over_72_hours(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=80))
        assert compute_freshness(a, now) == 0.0

    def test_unknown_time(self):
        now = datetime.now()
        a = make_article(published_at=None)
        score = compute_freshness(a, now)
        assert score == 0.0
        assert a.is_time_unknown is True


class TestFilterFreshArticles:
    """超过 DAILY_FRESHNESS_HOURS(48h) 的文章被过滤。"""

    def test_keeps_recent_articles(self):
        now = datetime.now()
        articles = [
            make_article("a1", published_at=now - timedelta(hours=6)),
            make_article("a2", published_at=now - timedelta(hours=24)),
        ]
        kept = filter_fresh_articles(articles, now)
        assert len(kept) == 2

    def test_filters_old_articles(self):
        now = datetime.now()
        articles = [
            make_article("a1", published_at=now - timedelta(hours=6)),
            make_article("a2", published_at=now - timedelta(hours=50)),
        ]
        kept = filter_fresh_articles(articles, now)
        assert len(kept) == 1
        assert kept[0].id == "a1"
        assert articles[1].is_low_freshness is True

    def test_filters_unknown_time(self):
        """无时间文章直接过滤。"""
        now = datetime.now()
        articles = [
            make_article("a1", published_at=now - timedelta(hours=6)),
            make_article("a2", published_at=None),
        ]
        kept = filter_fresh_articles(articles, now)
        assert len(kept) == 1
        assert kept[0].id == "a1"


class TestCanBeTopStory:
    def test_recent_multi_source_eligible(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=3))
        c = make_cluster("c1", articles=[a],
                         latest_seen=now - timedelta(hours=3),
                         is_single_source=False)
        assert can_be_top_story(c, now) is True

    def test_single_source_unofficial_not_eligible(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=3), is_official=False)
        c = make_cluster("c1", articles=[a],
                         latest_seen=now - timedelta(hours=3),
                         is_single_source=True)
        assert can_be_top_story(c, now) is False

    def test_single_source_official_eligible(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=3), is_official=True)
        c = make_cluster("c1", articles=[a],
                         latest_seen=now - timedelta(hours=3),
                         is_single_source=True)
        assert can_be_top_story(c, now) is True

    def test_too_old_not_eligible(self):
        now = datetime.now()
        a = make_article(published_at=now - timedelta(hours=40))
        c = make_cluster("c1", articles=[a],
                         latest_seen=now - timedelta(hours=40),
                         is_single_source=False)
        assert can_be_top_story(c, now) is False


# ============================================================
# cluster 测试
# ============================================================

class TestJaccard:
    def test_identical_sets(self):
        assert jaccard({1, 2, 3}, {1, 2, 3}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard({1, 2}, {3, 4}) == 0.0

    def test_partial_overlap(self):
        assert jaccard({1, 2, 3}, {2, 3, 4}) == 2 / 4  # 0.5

    def test_empty_set(self):
        assert jaccard(set(), {1, 2}) == 0.0
        assert jaccard({1, 2}, set()) == 0.0


class TestArticleSimilarity:
    def test_same_entity_same_topic_high_similarity(self):
        now = datetime.now()
        a1 = make_article("a1", "Kimi 发布新版本", entity_keys=["kimi"],
                          topic_keys=["model_release"], published_at=now)
        a2 = make_article("a2", "月之暗面 Kimi 更新", entity_keys=["kimi"],
                          topic_keys=["model_release"], published_at=now)
        sim = article_similarity(a1, a2)
        # entity 0.45 + topic 0.25 + time 0.05 = 0.75
        assert sim > 0.6

    def test_different_entities_low_similarity(self):
        now = datetime.now()
        a1 = make_article("a1", "OpenAI 发布 GPT-5", entity_keys=["openai"],
                          topic_keys=["model_release"], published_at=now)
        a2 = make_article("a2", "Kimi 发布新版本", entity_keys=["kimi"],
                          topic_keys=["model_release"], published_at=now)
        sim = article_similarity(a1, a2)
        # entity 0.0 + topic 0.25 + some title overlap
        assert sim < 0.5

    def test_same_topic_no_entity(self):
        now = datetime.now()
        a1 = make_article("a1", "新模型发布", entity_keys=[],
                          topic_keys=["model_release"], published_at=now)
        a2 = make_article("a2", "AI 大模型发布", entity_keys=[],
                          topic_keys=["model_release"], published_at=now)
        sim = article_similarity(a1, a2)
        # topic 0.25 + title "新模型发布" vs "AI大模型发布" overlaps on "模型发布"
        assert sim > 0.2


class TestClusterArticles:
    """同事件多篇文章合并为一个 EventCluster。"""

    def test_same_event_articles_clustered(self):
        """Kimi 事件 3 篇文章 → 1 个 cluster。"""
        now = datetime.now()
        articles = [
            make_article("a1", "Kimi 发布新模型 K2", domain="moonshot.cn",
                         entity_keys=["kimi"], topic_keys=["model_release"],
                         published_at=now - timedelta(hours=2),
                         source_quality=1.0),
            make_article("a2", "月之暗面推出 Kimi K2 大模型", domain="jiqizhixin.com",
                         entity_keys=["kimi"], topic_keys=["model_release"],
                         published_at=now - timedelta(hours=4),
                         source_quality=0.75),
            make_article("a3", "Kimi K2 benchmarks 碾压同行", domain="zhihu.com",
                         entity_keys=["kimi"], topic_keys=["benchmark"],
                         published_at=now - timedelta(hours=6),
                         source_quality=0.55),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1, f"Expected 1 cluster, got {len(clusters)}"
        assert len(clusters[0].articles) == 3
        assert clusters[0].is_single_source is False
        assert len(clusters[0].source_domains) == 3

    def test_different_events_separate(self):
        """OpenAI + Kimi + Meta 不同事件分开。"""
        now = datetime.now()
        articles = [
            make_article("a1", "OpenAI 发布 GPT-5", domain="openai.com",
                         entity_keys=["openai"], topic_keys=["model_release"],
                         published_at=now),
            make_article("a2", "Kimi 发布新版本", domain="moonshot.cn",
                         entity_keys=["kimi"], topic_keys=["model_release"],
                         published_at=now),
            make_article("a3", "Meta 开源 Llama 4", domain="meta.com",
                         entity_keys=["meta"], topic_keys=["model_release"],
                         published_at=now),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) >= 2, f"Expected >= 2, got {len(clusters)}"

    def test_similarity_threshold_respected(self):
        """不同主题文章不被合并。"""
        now = datetime.now()
        articles = [
            make_article("a1", "AI 融资新闻", entity_keys=[],
                         topic_keys=["funding"], published_at=now),
            make_article("a2", "AI 安全监管法案通过", entity_keys=[],
                         topic_keys=["policy"], published_at=now),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 2

    def test_representative_highest_quality(self):
        """代表文章是 quality 最高的。"""
        now = datetime.now()
        articles = [
            make_article("a1", "GPT-5 发布", domain="zhihu.com",
                         entity_keys=["openai"], topic_keys=["model_release"],
                         published_at=now, source_quality=0.55),
            make_article("a2", "OpenAI GPT-5", domain="openai.com",
                         entity_keys=["openai"], topic_keys=["model_release"],
                         published_at=now - timedelta(hours=1),
                         source_quality=1.0, is_official=True),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        assert clusters[0].representative.id == "a2"

    def test_entity_and_keywords_aggregated(self):
        """Cluster 聚合所有文章 entity/topic。"""
        now = datetime.now()
        articles = [
            make_article("a1", "Kimi K2 发布", domain="moonshot.cn",
                         entity_keys=["kimi"], topic_keys=["model_release"],
                         published_at=now),
            make_article("a2", "Kimi 新模型评测", domain="jiqizhixin.com",
                         entity_keys=["kimi"], topic_keys=["benchmark"],
                         published_at=now),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        assert "kimi" in clusters[0].entities
        assert len(clusters[0].keywords) >= 2


# ============================================================
# diversify 测试
# ============================================================

class TestScoreClusters:
    def test_scoring_produces_valid_range(self):
        now = datetime.now()
        a = make_article("a1", "Test", published_at=now - timedelta(hours=2))
        c = make_cluster("c1", articles=[a], entities=["openai"],
                         keywords=["model_release"],
                         latest_seen=now - timedelta(hours=2),
                         is_single_source=True)
        clusters = score_clusters([c], now)
        assert 0 < clusters[0].final_score <= 1.0

    def test_major_entity_boosted(self):
        now = datetime.now()
        a = make_article("a1", "OpenAI", published_at=now - timedelta(hours=2))
        c1 = make_cluster("c1", articles=[a], entities=["openai"],
                          latest_seen=now - timedelta(hours=2))
        c2 = make_cluster("c2", articles=[a], entities=["unknown_entity"],
                          latest_seen=now - timedelta(hours=2))
        clusters = score_clusters([c1, c2], now)
        scores = {c.id: c.final_score for c in clusters}
        assert scores["c1"] > scores["c2"], f"Major entity should score higher: {scores}"

    def test_single_source_penalty(self):
        now = datetime.now()
        a1 = make_article("a1", "News", published_at=now - timedelta(hours=2))
        a2 = make_article("a2", "News Too", domain="other.com",
                          published_at=now - timedelta(hours=2))
        c_single = make_cluster("cs", articles=[a1], is_single_source=True,
                                latest_seen=now - timedelta(hours=2))
        c_multi = make_cluster("cm", articles=[a1, a2],
                               is_single_source=False,
                               latest_seen=now - timedelta(hours=2))
        score_clusters([c_single, c_multi], now)
        # single-source unofficial gets -0.18 penalty
        assert c_single.final_score < c_multi.final_score


class TestSelectDiverseClusters:
    """多样性选择——entity quota / domain quota / freshness gate。"""

    def test_same_entity_limited(self):
        """同实体最多 MAX_SAME_ENTITY_CLUSTERS_DAILY 个。"""
        from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.config import (
            MAX_SAME_ENTITY_CLUSTERS_DAILY,
        )
        now = datetime.now()
        clusters = []
        for i in range(5):
            a = make_article(f"a{i}", f"Kimi 新闻 {i}", domain=f"source{i}.com",
                             entity_keys=["kimi"], topic_keys=["model_release"],
                             published_at=now - timedelta(hours=i))
            c = make_cluster(f"c{i}", articles=[a], entities=["kimi"],
                             latest_seen=now - timedelta(hours=i))
            c.final_score = 1.0 - i * 0.01
            clusters.append(c)

        selected = select_diverse_clusters(clusters, now)
        assert len(selected) == MAX_SAME_ENTITY_CLUSTERS_DAILY, \
            f"Expected {MAX_SAME_ENTITY_CLUSTERS_DAILY}, got {len(selected)}"
        assert selected[0].id == "c0"

    def test_domain_quota_limited(self):
        """同域名最多 MAX_CLUSTERS_PER_DOMAIN_FINAL 个。"""
        from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.config import (
            MAX_CLUSTERS_PER_DOMAIN_FINAL,
        )
        now = datetime.now()
        clusters = []
        for i in range(5):
            a = make_article(f"a{i}", f"News {i}", domain="same-domain.com",
                             entity_keys=[f"entity_{i}"],
                             topic_keys=["model_release"],
                             published_at=now - timedelta(hours=i))
            c = make_cluster(f"c{i}", articles=[a], entities=[f"entity_{i}"],
                             latest_seen=now - timedelta(hours=i))
            c.final_score = 1.0 - i * 0.01
            clusters.append(c)

        selected = select_diverse_clusters(clusters, now)
        assert len(selected) == MAX_CLUSTERS_PER_DOMAIN_FINAL, \
            f"Expected {MAX_CLUSTERS_PER_DOMAIN_FINAL}, got {len(selected)}"

    def test_too_old_filtered(self):
        """超过 freshness hour 的 cluster 被排除。"""
        now = datetime.now()
        old_a = make_article("old", "Old News", published_at=now - timedelta(hours=50))
        old = make_cluster("c1", articles=[old_a],
                           latest_seen=now - timedelta(hours=50))
        old.final_score = 0.9
        recent_a = make_article("recent", "Recent News", published_at=now - timedelta(hours=2))
        recent = make_cluster("c2", articles=[recent_a],
                              entities=["openai"],
                              latest_seen=now - timedelta(hours=2))
        recent.final_score = 0.8

        selected = select_diverse_clusters([old, recent], now)
        assert len(selected) == 1
        assert selected[0].id == "c2"

    def test_null_latest_seen_excluded(self):
        now = datetime.now()
        a = make_article("a1", "News", published_at=now - timedelta(hours=2))
        c = make_cluster("c1", articles=[a], latest_seen=None)
        c.final_score = 0.9
        selected = select_diverse_clusters([c], now)
        assert len(selected) == 0

    def test_max_clusters_limit(self):
        from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.config import (
            MAX_FINAL_CLUSTERS,
        )
        now = datetime.now()
        clusters = []
        for i in range(20):
            a = make_article(f"a{i}", f"News {i}", domain=f"domain{i}.com",
                             entity_keys=[f"entity_{i}"],
                             published_at=now - timedelta(hours=i))
            c = make_cluster(f"c{i}", articles=[a], entities=[f"entity_{i}"],
                             latest_seen=now - timedelta(hours=i))
            c.final_score = 1.0 - i * 0.01
            clusters.append(c)
        selected = select_diverse_clusters(clusters, now)
        assert len(selected) <= MAX_FINAL_CLUSTERS


class TestPickTopStory:
    def test_picks_highest_score_eligible(self):
        now = datetime.now()
        a1 = make_article("a1", "GPT-5", domain="openai.com",
                          is_official=True, published_at=now - timedelta(hours=2))
        a2 = make_article("a2", "Other", domain="other.com",
                          published_at=now - timedelta(hours=2))
        c1 = make_cluster("c1", articles=[a1, a2], entities=["openai"],
                          is_single_source=False,
                          latest_seen=now - timedelta(hours=2))
        c1.final_score = 0.95
        c2 = make_cluster("c2", articles=[a2], entities=["other"],
                          latest_seen=now - timedelta(hours=2))
        c2.final_score = 0.5
        top = pick_top_story([c1, c2], now)
        assert top is not None
        assert top.id == "c1"

    def test_returns_none_when_none_eligible(self):
        now = datetime.now()
        a = make_article("a1", "News", published_at=now - timedelta(hours=40))
        c = make_cluster("c1", articles=[a], latest_seen=now - timedelta(hours=40))
        c.final_score = 0.9
        top = pick_top_story([c], now)
        assert top is None


class TestBuildDailyReport:
    def test_report_structure(self):
        now = datetime.now()
        a1 = make_article("a1", "GPT-5 Released", domain="openai.com",
                          entity_keys=["openai"], topic_keys=["model_release"],
                          published_at=now - timedelta(hours=2),
                          is_official=True, source_quality=1.0)
        a2 = make_article("a2", "GPT-5 Benchmark", domain="techcrunch.com",
                          entity_keys=["openai"], topic_keys=["benchmark"],
                          published_at=now - timedelta(hours=3),
                          source_quality=0.9)
        c1 = make_cluster("c1", articles=[a1, a2], entities=["openai"],
                          latest_seen=now - timedelta(hours=2),
                          is_single_source=False)
        score_clusters([c1], now)

        report = build_daily_report([c1], now)
        assert report.mode == "daily"
        assert report.top_story is not None
        assert report.top_story.id == "c1"
        assert len(report.highlights) == 0  # only one cluster, used as top

    def test_no_top_story_if_none_eligible(self):
        now = datetime.now()
        a = make_article("a1", "News", published_at=now - timedelta(hours=2))
        c = make_cluster("c1", articles=[a], latest_seen=now - timedelta(hours=2))
        score_clusters([c], now)
        report = build_daily_report([c], now)
        assert report.top_story is None
        assert len(report.highlights) == 1


# ============================================================
# tool.py 测试
# ============================================================

class TestRouteMode:
    def test_default_is_quality(self):
        assert _route_mode("今日 AI 日报") == "quality"

    def test_auto_resolves_to_quality(self):
        assert _route_mode("test", "auto") == "quality"

    def test_explicit_fast(self):
        assert _route_mode("test", "fast") == "fast"

    def test_explicit_quality(self):
        assert _route_mode("test", "quality") == "quality"

    def test_invalid_mode_defaults(self):
        assert _route_mode("test", "invalid") == "quality"


class TestApplyQuotas:
    def _make_item(self, source_group, id_prefix=""):
        class FakeItem:
            pass
        item = FakeItem()
        item.source_group = source_group
        item.id = f"{id_prefix}_{source_group}"
        return item

    def test_limit_enforced(self):
        items = [self._make_item("ai_media", str(i)) for i in range(20)]
        result = _apply_quotas(items, limit=3)
        assert len(result) <= 3

    def test_groups_are_ordered(self):
        """core_provider 在 ai_media 之前。"""
        items = [
            self._make_item("ai_media", "1"),
            self._make_item("core_provider", "2"),
            self._make_item("curated", "3"),
        ]
        result = _apply_quotas(items, limit=10)
        groups = [it.source_group for it in result]
        cp_idx = groups.index("core_provider")
        am_idx = groups.index("ai_media")
        assert cp_idx < am_idx, f"Expected core_provider before ai_media, got {groups}"


class TestReportToDigest:
    """_report_to_digest —— source_ids 映射正确性 + render guard。"""

    def _make_article(self, aid, title, domain, source_name="Test", pub=None):
        return Article(
            id=aid, title=title,
            url=f"https://{domain}/{aid}", domain=domain,
            source=source_name, source_group="ai_media",
            published_at=pub or datetime.now(),
            title_norm=normalize_title(title),
            source_quality_score=0.75,
        )

    def _make_cluster(self, cid, title, articles=None, entities=None):
        return EventCluster(
            id=cid, title=title,
            articles=articles or [],
            entities=entities or [],
            representative=articles[0] if articles else None,
            source_domains={a.domain for a in (articles or [])},
            final_score=0.8,
        )

    def test_source_ids_mapping(self):
        """source_ids 指向正确的 article 而非 cluster 索引。"""
        a1 = self._make_article("a1", "GPT-5 Released", "openai.com", "OpenAI")
        a2 = self._make_article("a2", "GPT-5 Benchmark", "techcrunch.com", "TechCrunch")
        a3 = self._make_article("a3", "GPT-5 Review", "theverge.com", "The Verge")
        c1 = self._make_cluster("c1", "GPT-5 Released", articles=[a1, a2, a3],
                                entities=["openai"])
        c1.known = ["GPT-5 achieves 90% on benchmarks"]

        c2_a1 = self._make_article("b1", "Claude 4 Announced", "anthropic.com", "Anthropic")
        c2 = self._make_cluster("c2", "Claude 4 Announced", articles=[c2_a1],
                                entities=["anthropic"])
        c2.known = ["Claude 4 outperforms GPT-5 on coding"]

        report = NewsReport(
            mode="daily", title="AI 日报", generated_at=datetime.now(),
            top_story=c1, highlights=[c2],
        )
        articles = [a1, a2, a3, c2_a1]

        digest = _report_to_digest(report, articles)

        assert digest["top_story"] is not None
        top_src_ids = digest["top_story"]["source_ids"]
        assert 1 <= len(top_src_ids) <= 3

        assert len(digest["highlights"]) == 1
        hl_src_ids = digest["highlights"][0]["source_ids"]
        assert len(hl_src_ids) >= 1

        # article_id → source_id 顺序: a1=1, a2=2, a3=3, b1=4
        # GPT-5 cluster 的 source_ids 应该是 1,2,3; Claude 4 = 4
        for sid in top_src_ids:
            assert 1 <= sid <= 4, f"source_id {sid} out of range"
        assert 4 in hl_src_ids

    def test_details_have_source_ids(self):
        """details 中每条带 source_ids + source_labels。"""
        a1 = self._make_article("a1", "News", "example.com")
        c1 = self._make_cluster("c1", "News", articles=[a1])
        c1.known = ["Known fact"]

        report = NewsReport(
            mode="daily", title="AI 日报", generated_at=datetime.now(),
            top_story=c1, highlights=[],
        )
        digest = _report_to_digest(report, [a1])
        assert len(digest["details"]) == 1
        detail = digest["details"][0]
        assert "source_ids" in detail
        assert len(detail["source_ids"]) >= 1
        assert "source_labels" in detail

    def test_entity_guard_limits_same_entity(self):
        """render guard 防止同实体 cluster 重复出现。"""
        a1 = self._make_article("a1", "Kimi K2", "moonshot.cn", "Moonshot")
        a2 = self._make_article("a2", "Kimi 评测", "jiqizhixin.com", "机器之心")
        c1 = self._make_cluster("c1", "Kimi K2", articles=[a1], entities=["kimi"])
        c2 = self._make_cluster("c2", "Kimi K2 Benchmark", articles=[a2],
                                entities=["kimi"])
        c3_a = self._make_article("a3", "Claude", "anthropic.com")
        c3 = self._make_cluster("c3", "Claude 4", articles=[c3_a],
                                entities=["anthropic"])

        report = NewsReport(
            mode="daily", title="AI 日报", generated_at=datetime.now(),
            top_story=c1, highlights=[c2, c3],
        )
        digest = _report_to_digest(report, [a1, a2, c3_a])
        # top 是 kimi，highlights 中第二个 kimi c2 应被 guard 过滤
        # 所以 highlights 只剩 c3 (anthropic)
        assert digest["top_story"] is not None
        source_entities = {1: "kimi", 2: "kimi", 3: "anthropic"}
        highlight_entities = [
            source_entities[sid]
            for h in digest["highlights"]
            for sid in h["source_ids"]
        ]
        assert highlight_entities == ["anthropic"]

    def test_digest_structure_complete(self):
        """digest 字典包含所有必需字段。"""
        a1 = self._make_article("a1", "News", "example.com")
        c1 = self._make_cluster("c1", "News", articles=[a1])
        report = NewsReport(
            mode="daily", title="AI 日报", generated_at=datetime.now(),
            highlights=[c1],
        )
        digest = _report_to_digest(report, [a1])

        required = ["title", "subtitle", "verdict", "generated_at", "mode",
                    "top_story", "highlights", "details", "sources",
                    "watchlist", "missing_info", "closing"]
        for key in required:
            assert key in digest, f"Missing key: {key}"
        assert digest["mode"] == "daily"

    def test_sources_indexed_correctly(self):
        """sources 列表从 1 开始编号。"""
        articles = [
            self._make_article("a1", "News One", "domain1.com", "Source 1"),
            self._make_article("a2", "News Two", "domain2.com", "Source 2"),
        ]
        c1 = self._make_cluster("c1", "News", articles=[articles[0]])
        report = NewsReport(
            mode="daily", title="AI 日报", generated_at=datetime.now(),
            highlights=[c1],
        )
        digest = _report_to_digest(report, articles)

        assert len(digest["sources"]) == 2
        assert digest["sources"][0]["source_id"] == 1
        assert digest["sources"][0]["title"] == "News One"
        assert digest["sources"][0]["domain"] == "domain1.com"
        assert digest["sources"][1]["source_id"] == 2
