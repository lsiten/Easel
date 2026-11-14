"""Hot-list order is explainably relevant to the selected persona."""
from __future__ import annotations

import asyncio

import web.app as web
from web.trend_personalization import persona_signals, rank_items

PROFILE = """# 身份定位
## 内容方向
- 嘉兴本地 AI、职业提效、独立开发

## 兴趣标签
- Agent、RAG 数据工程、技术评测

## 平台运营
小红书、知乎

## 不做的
- 泛泛 AI 新闻、效果翻倍
"""


def test_rank_prefers_persona_topics_and_explains_why():
    signals = persona_signals(PROFILE)
    items = [
        {"title": "世界杯决赛今晚开赛", "hot": "900万", "url": ""},
        {"title": "嘉兴开发者用 Agent 做独立产品", "hot": "80万", "url": ""},
        {"title": "通用 AI 新闻合集", "hot": "700万", "url": ""},
    ]

    ranked = rank_items(items, signals, "zhihu")

    assert ranked[0]["title"] == "嘉兴开发者用 Agent 做独立产品"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert "嘉兴本地" in ranked[0]["reasons"][0] or "agent" in ranked[0]["reasons"][0]
    assert "zhihu" in signals.platforms


def test_persona_red_lines_penalize_otherwise_popular_items():
    signals = persona_signals(PROFILE)
    items = [
        {"title": "效果翻倍的十个 AI 工具", "hot": "999万", "url": ""},
        {"title": "RAG 检索评测记录", "hot": "3万", "url": ""},
    ]

    ranked = rank_items(items, signals, "weibo")

    assert ranked[0]["title"] == "RAG 检索评测记录"
    assert "红线" in ranked[1]["reasons"][0]


def test_profile_concepts_match_real_hot_titles_without_treating_ai_as_a_red_line():
    signals = persona_signals(PROFILE)
    ranked = rank_items([
        {"title": "DeepSeek harness 发布新能力", "hot": "40万", "url": ""},
        {"title": "足球联赛焦点战", "hot": "900万", "url": ""},
    ], signals, "zhihu")

    assert "ai" not in signals.exclusions
    assert ranked[0]["matched"] is True
    assert ranked[0]["relevance"] > 0
    assert ranked[1]["matched"] is False


def test_one_confirmed_main_platform_ignores_unconfirmed_platform_mentions():
    signals = persona_signals(PROFILE + """

## 平台运营

### 小红书（主力）

- 图文为主。

### 公众号 / 抖音 / 视频号

- 待确认是否创建。
""")

    assert "xiaohongshu" in signals.platforms
    assert "douyin" not in signals.platforms


def test_trend_api_returns_persona_order_without_changing_anonymous_order(monkeypatch):
    web._TREND_CACHE.clear()
    monkeypatch.setattr(web, "profile_exists", lambda persona: persona == "测试画像")
    monkeypatch.setattr(web, "load_profile_text", lambda persona: PROFILE)
    monkeypatch.setattr(web, "_fetch_platform", lambda platform: [
        {"title": "足球联赛焦点战", "hot": "900万", "url": ""},
        {"title": "Agent 工程实践", "hot": "20万", "url": ""},
    ])

    personal = asyncio.run(web.api_trends("zhihu", 2, "测试画像"))
    anonymous = asyncio.run(web.api_trends("zhihu", 2))

    assert personal["personalization"]["enabled"] is True
    assert personal["trends"][0]["items"][0]["title"] == "Agent 工程实践"
    assert personal["trends"][0]["items"][0]["reasons"]
    assert anonymous["personalization"]["enabled"] is False
    assert anonymous["trends"][0]["items"][0]["title"] == "足球联赛焦点战"
