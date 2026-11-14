"""Explainable persona-aware ranking for platform hot lists."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Mapping

_PLATFORM_ALIASES: Final = {
    "weibo": ("微博",),
    "douyin": ("抖音",),
    "zhihu": ("知乎",),
    "bilibili": ("B站", "B 站", "哔哩哔哩"),
    "baidu": ("百度",),
    "toutiao": ("头条", "今日头条"),
    "xiaohongshu": ("小红书",),
}
_TOPIC_SECTIONS: Final = ("内容方向", "兴趣标签", "差异化", "痛点", "要做的")
_RED_LINE_SECTIONS: Final = ("不做的", "去模板味")
_TOPIC_STOPWORDS: Final = {
    "一个", "一种", "一次", "内容", "读者", "用户", "平台", "账号", "真实",
    "可以", "需要", "不是", "不要", "如何", "以及", "进行", "保持", "使用",
}
_LATIN_TERM = re.compile(r"[a-zA-Z][a-zA-Z0-9.+#-]{1,23}")
_BULLET_SPLIT = re.compile(r"[、，,；;：:（）()《》<>“”\"'·/]+")
_CLAUSE_BREAK = re.compile(
    r"(?:为什么|反而|如何|怎样|怎么|哪个|是否|以及|而不是|这类|每篇|缺一个|"
    r"当天能|真实工作里|用了|刷到|却|想回到|想用)+"
)
_HOT_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(万|亿)?")
_FOCUS_REJECT = (
    "而不是", "想回到", "想用", "这类", "每篇", "为什么", "怎么", "如何",
    "却", "用了", "刷到", "当天能", "真实工作里",
)
_UNSAFE_FRAGMENT = re.compile(r"[\s「」“”《》()（）0-9]")
_EXCLUSION_GENERIC: Final = {
    "ai", "内容", "项目", "作品", "账号", "用户", "人群", "平台", "工作",
    "工具", "效果", "方式", "来源", "数据", "信息", "传播", "发布",
}
_SHORT_EXCLUSIONS: Final = {
    "速成", "夸张", "虚构", "绝对化", "引流", "刷量", "搬运",
}


@dataclass(frozen=True, slots=True)
class _InterestConcept:
    """A profile-grounded topic family with explainable title aliases."""

    label: str
    triggers: tuple[str, ...]
    aliases: tuple[str, ...]
    weight: float


_INTEREST_CONCEPTS: Final = (
    _InterestConcept(
        "AI 应用",
        ("ai", "人工智能", "大模型", "agent", "rag", "prompt", "模型调用"),
        ("ai", "人工智能", "大模型", "模型", "生成式", "chatgpt", "deepseek",
         "agent", "智能体", "算力", "prompt", "rag", "机器学习"),
        0.72,
    ),
    _InterestConcept(
        "AI 工具与提效",
        ("ai 工具", "提效", "效率", "效率工具", "职业"),
        ("ai 工具", "智能工具", "效率工具", "提效", "效率", "生产力",
         "自动化", "职场", "工作", "加班", "打工人", "时间管理"),
        0.70,
    ),
    _InterestConcept(
        "独立开发与产品",
        ("独立开发", "一人公司", "opc", "开发者", "程序员", "产品"),
        ("独立开发", "一人公司", "opc", "开发者", "程序员", "编程",
         "创业", "副业", "产品", "代码", "上线"),
        0.68,
    ),
    _InterestConcept(
        "本地与社区",
        ("嘉兴", "禾城", "本地", "社区", "共创"),
        ("嘉兴", "禾城", "南湖", "本地", "同城", "社区", "共创", "线下"),
        0.72,
    ),
    _InterestConcept(
        "技术工程",
        ("前端", "编程", "开发", "runtime", "harness", "javascript", "typescript"),
        ("前端", "编程", "代码", "开发", "框架", "javascript", "typescript",
         "react", "vue", "runtime", "harness", "接口", "工程"),
        0.62,
    ),
    _InterestConcept(
        "宠物内容",
        ("宠物", "猫咪", "猫", "养宠"),
        ("宠物", "猫", "猫咪", "布偶", "狸花", "养宠", "撸猫", "狗狗",
         "汪星人", "萌宠"),
        0.78,
    ),
    _InterestConcept(
        "内容创作",
        ("内容", "写作", "选题", "创作", "运营"),
        ("内容", "写作", "选题", "创作", "账号", "涨粉", "运营", "爆款"),
        0.56,
    ),
)


@dataclass(frozen=True, slots=True)
class PersonaSignals:
    """Compact local signals derived only from the selected persona profile."""

    terms: tuple[tuple[str, float], ...]
    exclusions: tuple[str, ...]
    platforms: frozenset[str]
    focus: tuple[str, ...]

    @property
    def focus_terms(self) -> list[str]:
        return list(self.focus)


def _section_lines(text: str, section_names: tuple[str, ...]) -> list[str]:
    wanted = False
    level = 0
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"^(#+)\s+(.+)$", line)
        if heading:
            current_level = len(heading.group(1))
            title = heading.group(2)
            if wanted and current_level <= level:
                wanted = False
            if any(name in title for name in section_names):
                wanted = True
                level = current_level
            continue
        if wanted and line.startswith(("-", "*")):
            lines.append(line.lstrip("-* ").strip())
    return lines


def _terms_from_lines(lines: list[str], weight: float) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in lines:
        clean = re.sub(r"https?://\S+", " ", line)
        clean = re.sub(r"[*_`#>\[\]]", " ", clean)
        for piece in _BULLET_SPLIT.split(clean):
            for clause in _CLAUSE_BREAK.split(piece):
                phrase = re.sub(
                    r"^(?:不写|不做|不分享|不把|不要|没有|想找|想判断|需要)\s*",
                    "",
                    clause.strip(" 。.!！?？"),
                )
                if not phrase or phrase in _TOPIC_STOPWORDS:
                    continue
                candidates = [phrase] if 2 <= len(phrase) <= 16 else []
                candidates.extend(match.group(0) for match in _LATIN_TERM.finditer(phrase))
                for candidate in candidates:
                    normalized = candidate.lower().strip()
                    if len(normalized) < 2 or normalized in _TOPIC_STOPWORDS:
                        continue
                    found[normalized] = max(found.get(normalized, 0.0), weight)
    return found


def _valid_exclusion(term: str) -> bool:
    if term in _EXCLUSION_GENERIC:
        return False
    if term.isascii():
        return len(term) >= 4
    return len(term) >= 3


def _platform_section(text: str) -> str:
    match = re.search(r"(?m)^#+\s+平台运营\s*$", text)
    if not match:
        return text
    next_heading = re.search(r"(?m)^#\s+", text[match.end():])
    end = match.end() + next_heading.start() if next_heading else len(text)
    return text[match.end():end]


def _preferred_platforms(text: str) -> frozenset[str]:
    section = _platform_section(text)
    if "主力" in section:
        blocks = re.split(r"(?m)^##+\s+", section)
        section = "\n".join(
            block for block in blocks
            if block and "主力" in block.splitlines()[0]
        )
    else:
        section = re.split(r"(?m)^##+\s+.*(?:待确认|待补充)", section)[0]
    low = section.lower()
    return frozenset(
        platform
        for platform, names in _PLATFORM_ALIASES.items()
        if any(name.lower() in low for name in names)
    )


def persona_signals(profile_text: str) -> PersonaSignals:
    """Extract focus terms, hard-line exclusions, and preferred platforms."""
    topic_terms = _terms_from_lines(_section_lines(profile_text, _TOPIC_SECTIONS), 1.0)
    identity_terms = _terms_from_lines(_section_lines(profile_text, ("我是谁", "当前简介", "兴趣标签")), 0.8)
    for term, weight in identity_terms.items():
        topic_terms[term] = max(topic_terms.get(term, 0.0), weight)
    focus: list[str] = []
    low_profile = profile_text.lower()
    for concept in _INTEREST_CONCEPTS:
        if not any(trigger in low_profile for trigger in concept.triggers):
            continue
        focus.append(concept.label)
        for alias in concept.aliases:
            topic_terms[alias] = max(topic_terms.get(alias, 0.0), concept.weight)
    exclusions = tuple(
        dict.fromkeys(
            term
            for term, _weight in _terms_from_lines(_section_lines(profile_text, _RED_LINE_SECTIONS), 1.0).items()
            if _valid_exclusion(term)
        )
    ) + tuple(term for term in _SHORT_EXCLUSIONS if term in profile_text)
    for term, _weight in sorted(topic_terms.items(), key=lambda item: (-item[1], -len(item[0]), item[0])):
        if (
            2 <= len(term) <= 12
            and not _UNSAFE_FRAGMENT.search(term)
            and not any(marker in term for marker in _FOCUS_REJECT)
            and term not in focus
        ):
            focus.append(term)
    ordered = sorted(
        (
            (term, weight)
            for term, weight in topic_terms.items()
            if 2 <= len(term) <= 12 and not _UNSAFE_FRAGMENT.search(term)
        ),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    return PersonaSignals(
        tuple(ordered[:120]),
        tuple(dict.fromkeys(exclusions))[:60],
        _preferred_platforms(profile_text),
        tuple(focus[:12]),
    )


def _contains_term(title: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9.+#-]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title) is not None
    return term in title


def _heat_score(hot: str) -> float:
    match = _HOT_NUMBER.search(hot.replace(",", ""))
    if not match:
        return 0.0
    value = float(match.group(1))
    if match.group(2) == "亿":
        value *= 100_000_000
    elif match.group(2) == "万":
        value *= 10_000
    if value <= 0:
        return 0.0
    return min(8.0, max(1.0, int(value).bit_length() / 4))


def rank_items(items: list[dict], signals: PersonaSignals, platform: str) -> list[dict]:
    """Return a stable relevance order with human-readable matching reasons."""
    ranked: list[dict] = []
    for original_rank, raw in enumerate(items):
        title = str(raw.get("title", "")).strip()
        if not title:
            continue
        normalized = title.lower()
        matches = [
            (term, weight)
            for term, weight in signals.terms
            if _contains_term(normalized, term)
        ]
        matches.sort(key=lambda item: (-item[1], -len(item[0]), item[0]))
        relevance = sum(min(18.0, 4.0 + len(term) * 0.8) * weight for term, weight in matches[:4])
        score = relevance
        reasons = [f"匹配「{term}」" for term, _weight in matches[:2]]
        if platform in signals.platforms:
            score += 4.0
            reasons.append("画像主力平台")
        score += _heat_score(str(raw.get("hot", "")))
        score += max(0.0, 3.0 - original_rank * 0.12)
        blocked = [term for term in signals.exclusions if _contains_term(normalized, term)]
        if blocked:
            score -= 24.0
            reasons = [f"可能触达画像红线「{blocked[0]}」"]
        if not reasons:
            reasons = ["平台热度"]
        ranked.append({
            **raw,
            "originalRank": original_rank + 1,
            "score": round(max(0.0, min(100.0, score)), 1),
            "relevance": round(max(0.0, relevance), 1),
            "matched": bool(matches) and not blocked,
            "reasons": reasons,
        })
    ranked.sort(key=lambda item: (-float(item["score"]), int(item["originalRank"])))
    return ranked


def public_personalization(signals: PersonaSignals | None) -> Mapping[str, object]:
    if signals is None:
        return {"enabled": False, "focusTerms": [], "platforms": []}
    return {
        "enabled": True,
        "focusTerms": signals.focus_terms,
        "platforms": sorted(signals.platforms),
    }
