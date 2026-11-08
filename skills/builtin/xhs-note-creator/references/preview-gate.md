# 发布前预览 + 自检门禁（Step 9）

小红书读者看到的是**图文笔记**（竖图轮播 + 标题 + caption + 话题 + 作者 + 互动条），不是 `.md` 原稿。
所以发布前必须**先把真实发布效果做出来、先自检**——没问题才交付用户确认，确认无误才真发。

## 引擎

`skills/shared/scripts/xhs_preview.py`（与 `render_card.py` / `card_audit.py` 同层的确定性脚本）。

| 子命令 | 作用 | 依赖 |
|--------|------|------|
| `preview <note_dir\|meta.json>` | 读 `meta.json` + 已渲染 `card_*.png` → 生成 `xhs_preview.html` | 纯 stdlib（读图） |
| `check <note_dir\|meta.json>` | 刷新预览 + 渲染预览截图 + 内容/视觉/合规/去AI味 自检 → 报告 + verdict | playwright + PIL + numpy |
| `render <preview.html>` | 仅把预览渲染成截图 PNG（供视觉复核） | playwright |
| `selftest` | 合成 meta 验证 preview/check 判定逻辑 | PIL |

## 预览还原的真实小红书图文交互

`xhs_preview.html` 还原移动端图文笔记展示，用户点开即见「发布后读者看到的样子」：

- **竖图轮播**：左右滑动 / 点点 / `1/N` 计数 / 箭头，对应 `card_*.png` 多图
- **标题**（加粗，单行）、**caption 正文**（多行）
- **话题标签**（蓝色 `#话题`，来自 `meta.hashtags`）
- **日期 + 风格**、**作者行**（头像/首字 + 名 + 相对时间）
- **互动条**：评论 / 收藏 / 点赞 / 转发（真实发布前计数 0，展示真实结构）
- 顶部「发布效果预览 · 非真实发布」提示条，避免与真发混淆

> 预览是**单个 `.html` 文件**：App「产物/输出」页已用 `sandbox=allow-scripts` 的 iframe 渲染任意 HTML 产物
> → 预览自动可交互、所见即所得，**零前端改动**。相对引用 `card_*.png`（App 经 `/api/media` 取图）。

## 自检（`check` 的 verdict）

| verdict | 含义 | 处理 |
|---------|------|------|
| `PASS` | 可交付用户确认 → 确认无误才真发 | 放行 |
| `WARN` | 长度 / 去AI味 瑕疵（列出但不阻断） | 逐条评估，能改就改；确可接受才放行 |
| `FAIL` | 硬伤：缺渲染图 / 单卡溢出 / 正文引流 / 绝对化用语 / 卡片非 3:4 | 必须返工（见下），重跑至 `PASS` |
| `BLOCK` | 内容安全闸门命中（密钥 / 内部信息），退出码 7 | 一律拦下，脱敏后重发 |

**检查项**（确定性）：
- 内容：标题 / caption / hashtag / 卡片数量与单卡字数区间（对齐 `meta-schema.md`、xhs-note-creator Step 6/8）
- 视觉：`card_audit.py` 逐卡死空白/密度 + 卡片 3:4 比例 + 预览截图非空
- 合规：正文引流（微信/二维码/购买/公众号/私信…）、绝对化用语（最/第一/100%/绝对…）
- 去AI味：`text-polisher` 权威短语（不只是/赋能/颠覆/天花板/效率翻倍…）
- 安全：复用 `content_guard.py` 真·敏感信息扫描（BLOCK 级）

## 返工回路

`FAIL` 按类型回炉 → **重渲卡片 → 重生成预览 → 重跑 `check`**，直到 `PASS`：

- 视觉类（死空白/密度/非 3:4）→ [card-xiaohongshu](../../card-xiaohongshu/SKILL.md) + `card_audit.py`
- 文案类（caption/hashtag 字数、结构）→ 本 SKILL Step 6
- 去AI味 → [text-polisher](../../text-polisher/SKILL.md)
- 引流 / 绝对化 → 删除正文引流与绝对化用语

> 真正发布（`skill-xhs-publisher`）只发生在 **Step 8 校验 + Step 9 自检全 PASS、且用户确认预览无误**之后。
