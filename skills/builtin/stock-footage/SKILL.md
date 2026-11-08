---
name: stock-footage
description: "从 Pixabay / Pexels 免费视频库抓取**现成实拍 / 空镜 / B-roll（真实动态画面）**，供视频制作取用——当某段分镜需要'真实运动'而非把静图用 Ken Burns 冒充动态时用它。Ego 浏览器 + CDN 直链下载，免登录、免 API key、免版税（可商用、无需署名）。当用户说 找空镜、找实拍、找 B-roll、找一段画面、取现成视频、真实动态、这段要动起来的画面、stock footage、素材片段 时使用。与 ai-video-gen 的区别：那是 AI 生成视频（要 provider/key），本 SKILL 从免费库抓现成实拍（免 key）；与 pixabay-music 同结构（同 Ego+CDN 机制），区别是取视频画面不是音乐；与 ai-image-gen+Ken Burns 的区别：那是静图缓动，本 SKILL 给真动态画面。"
layer: produce
---

# 现成实拍 / 空镜 / B-roll 抓取（Stock Footage）

视频里「这段该有真实运动」（海浪、城市航拍、机械运转、人流……）时，**优先用真实动态画面，而不是把静图用 Ken Burns 缓动冒充动态**——这是成品质感的关键分界。本 SKILL 从 **Pixabay / Pexels 免费视频库**抓取现成的实拍 / 空镜 / B-roll，供一键短视频、OpenFilm、视频产线取用。

全程通过共享脚本 `skills/shared/scripts/pixabay_footage.py`：

- 用 **Ego 浏览器**（真实指纹）打开 Pixabay 视频搜索页 —— 因为 pixabay.com 主站对裸 HTTP 请求返回 403，需真实浏览器；
- 点击下载按钮触发下载事件，拿到 **CDN 直链**（`cdn.pixabay.com/download/video/…mp4`）；
- 再用 **urllib 直连下载** MP4 —— CDN 直链可直接拉取，浏览器只负责「拿到直链」。

产物落到 `outputs/<主题>/video/`，或经 `--to-asset` 登记进画像**素材库**（`assets.py`），供 OpenFilm / 一键短视频取用。

**免登录、免 API key、免版税（可商用、无需署名）** —— 无需任何账号或密钥配置。

> 思路沉淀自 OpenMontage `method/skills/creative/stock-sourcing-usage.md`（真实 motion 片段检索 + POV 查询构造 + 评分 + 失败升级；非「animate-stills 静图冒充动态」）。Easel 用 Pixabay（Ego+CDN）实现，免 API key。

## 与相邻 SKILL 的边界

| SKILL | 用途 | 区别 |
|-------|------|------|
| **stock-footage**（本 SKILL） | 从免费库**抓现成实拍 / 空镜**（真动态） | 按关键词搜索，免 key，真动态画面 |
| `ai-video-gen` | 用 AI **生成**视频片段 | 需 provider / API key，输出原创 |
| `pixabay-music` | 从 Pixabay 免费库**抓音乐**（同 Ego+CDN 机制） | 取音乐，本 SKILL 取画面 |
| `ai-image-gen` + Ken Burns | 静图缓动（`animated_still`） | 不是真动态；`motion_clip` 段不要用 |

## 输入

| 字段 | 必填 | 说明 |
|------|------|------|
| keyword | 是 | 搜索关键词 / 场景 / 动作（如 `city skyline drone` / `海浪` / `timelapse light trails`） |
| pick | 否 | 取第几段（`get` 用，默认 1） |
| out | 否 | 输出目录（`get` 用，默认 `outputs/<主题>/video/`） |
| to-asset | 否 | 下载后登记进该画像素材库（`assets.py place`） |
| category | 否 | 素材库分类，默认 `视频 / 空镜` |
| tags | 否 | 素材库标签，逗号分隔 |

## 依赖

- **Ego / ego-browser**：`check` 会探测 `easel.browser.select_browser()` 与 `ego-browser` CLI、playwright。未就绪则安装 Ego 后重试。
- 无需任何 API key / 账号登录（Pixabay 视频免登录、免版税）。

## 执行步骤

> **配置检查路径铁律**：先 `cd` 到运行时说明给出的 Easel 项目根，确认当前目录有 `.env` 和 `skills/shared/scripts/`，再运行 `check` / `list` / `get`。不得改用 workspace 的共享脚本副本。

1. **能力探测**（首次或怀疑环境变化时）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_footage.py check
   ```
   确认 Ego / ego-browser CLI / playwright 就绪。

2. **浏览搜索结果**（挑选，看标题 / 作者 / 时长）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_footage.py list "city skyline drone" --limit 12
   # 机读
   .venv/bin/python skills/shared/scripts/pixabay_footage.py list "海浪" --limit 12 --json
   ```
   打印「编号 + 标题 + 作者 + 时长」序列，供挑选 `--pick N`。

3. **下载**（取第 N 段，默认第 1 段）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_footage.py get "city skyline drone" --pick 1
   # 指定输出目录
   .venv/bin/python skills/shared/scripts/pixabay_footage.py get "海浪" --pick 3 -o outputs/主题名/video
   # 下载并登记进画像素材库
   .venv/bin/python skills/shared/scripts/pixabay_footage.py get "海浪" --pick 1 --to-asset <画像> --category "视频 / 空镜" --tags 海浪,空镜
   ```
   产物落到 `outputs/<主题>/video/`；`--to-asset` 时登记进 `profiles/<画像>/assets/视频 / 空镜/`。

## 查询构造（比关键词更重要）

免费视频库是**关键词检索**，不是语义检索——构造好关键词决定能不能找到：

- **2–4 个关键词，别太长**：「aerial city skyline sunset」可以；「aerial shot of downtown at golden hour」搜不到。
- **主体打头**：「ocean waves」，不是「beautiful calm serene ocean waves at dawn」。
- **必带 POV 关键词**（库按 POV 索引，加对了常比改主体更能找到对的角度）：`drone / aerial`（航拍）、`macro / close-up`（微距/特写）、`timelapse`（延时）、`slow motion`（慢镜）、`handheld / locked-off / OTS / dashcam / FPV`。
- **失败换词**：「programmer coding」差就试「developer laptop」「software engineer workspace」。

| 场景类型 | 关键词模板 | 示例（含 POV） |
|---------|-----------|---------|
| 建立镜头 | `[地点] [时段] [POV]` | `tokyo skyline night drone` |
| 活动 | `[人] [动作] [POV]` | `scientist microscope OTS` |
| 物体 | `[物体] [风格] [POV]` | `circuit board macro` |
| 自然 | `[元素] [质感] [POV]` | `ocean waves aerial` |
| 抽象运动 | `[运动] [风格] [POV]` | `light trails timelapse` |
| 职场 | `[场景] [活动] [POV]` | `modern office meeting handheld` |

## 选片评分（拿到结果后先评再用）

| 分 | 含义 |
|----|------|
| 5 | 完美匹配，直接用 |
| 4 | 好匹配，轻微裁/剪 |
| 3 | 可用，建议调色贴合风格 |
| 2 | 勉强，先换关键词 |
| 1 | 不对，换 |

**阈值 3 分**：低于 3 分先换关键词 / 换 POV，再不行才降级到 `ai-video-gen`（AI 生成）。

## 失败升级（搜不到 / 全 <3 分）

1. **换关键词 / 换 POV** 重试；
2. 仍不行 → 降级 `ai-video-gen`（AI 生成该段）；
3. 都差 → 向用户报「找不到合适的 [场景] 空镜，这是最好的几个 [列结果]，或改用 AI 生成，你选哪个」。

## 许可

Pixabay / Pexels 视频**免费、可商用、无需署名**。建议在素材库登记时保留来源（`--to-asset` 会自动带 `kind=video` 与标签），便于追溯。

## 画幅

下载的画面按已确认画幅使用（未确认横/竖版或分辨率，制作/付费前必须先问并等确认）；需要补边/裁剪到画幅用 `image-editing` 的 `pad` 或 ffmpeg。
