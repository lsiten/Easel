---
name: video-reference-analyst
description: "参考视频分析。当用户给 YouTube、Shorts、IG、TikTok URL 或本地视频，说做个类似的、仿这个、照着做、参考这种风格时，分析 5-aspect 分镜与逐镜 motion，复用已选概念，在 OpenFilm 中验证代表段并制作完整预览。stock-footage、ai-image-gen 和 ai-video-gen 按镜头需要供料。用户指定专用流程时沿用其选择。用户只要求分析、理解、梳理源视频时，交付分镜、关键帧、转写和内容简报。已有素材的明确剪辑请求转对应剪辑 skill，仅转写使用 auto-subtitle。"
layer: produce
---

# 参考视频分析（Video Reference Analyst）

> 用户给一条**参考视频**说"做个类似的"。本 SKILL 是**参考制作的入口与编排层**：
> 分析参考 → 结构化 5-aspect 简报 → 判定逐镜 motion 类型 → 提差异化概念 →
> 在 OpenFilm 中验证代表段 → 完成全片并交付预览。用户明确指定专用流程时沿用该选择。
>
> 思路沉淀自 OpenMontage `method/skills/meta/video-reference-analyst.md`
> （5-aspect 结构源自 CMU/Harvard CHAI 研究；sample-first + 逐镜 motion 判定是其核心纪律）。
> Easel 由 `video-openfilm` 合成预览，按镜头需要使用素材搜索、AI 生成与剪辑工具。

## 什么时候用

**用**：用户给参考视频（URL 或本地文件）+ 表达"做个类似的/仿这个/照着做/这种风格"。
**不用**（路由到别处）：
- 用户说"剪这条 / 切这条 / 包装这条 / 给这条做字幕"（已有素材，非参考）→ `video-production` / `clipify` / `video-highlights`
- 用户只给主题/文案要无中生有 → `video-openfilm`
- 用户只要转写 → `auto-subtitle`
- 用户要短剧（剧情/角色/对白/反转）→ `short-drama`

### 检测信号
触发：粘贴 YouTube/Shorts/IG/TikTok URL；说"做个类似的""仿这个""照着做""这种风格""像 XX 那样"；
上传视频并说"我想做一条这样的"。

## 理解源视频（理解内容 / 素材库，不只是「参考制作」）

> 本 SKILL 的核心能力是**理解一条视频**：分镜 + 关键帧 + 转写 → 结构化 brief + 你的多模态解读。
> 「参考制作」是它的下游用法之一；**「理解源视频」本身可独立用于素材库 / 内容理解 / 复用判断**，
> 不一定要做「类似的」。用户说「分析/理解这条视频」「这条视频讲了什么」「看看这个视频内容」
> 「这条素材是什么」「帮我梳理这个视频」→ 走本节，不必进入第 4 步提概念 / 第 5 步出样片。

**两种用法共享同一个入口 `ref_analysis.py analyze`：**

```bash
# A. 参考制作（第 1 步）：分析参考 → 5-aspect 简报 → 提概念 → 出样片 → 路由引擎
python skills/shared/scripts/ref_analysis.py analyze \
  --source "<url 或本地路径>" --depth standard --max-keyframes 16 -o outputs/ref/<名字>

# B. 理解源视频（素材库/内容理解）：只需「看懂这条视频」，不必做类似
python skills/shared/scripts/ref_analysis.py analyze \
  --source "<url 或本地路径>" --depth standard --max-keyframes 16 -o outputs/ref/<名字>
# 只要转写（不做分镜/抽帧）：
python skills/shared/scripts/ref_analysis.py analyze --source "<url 或本地路径>" --transcript-only
```

**产出 `outputs/ref/<名字>/`：**
- `video_analysis_brief.json`：`metadata`（时长/分辨率/音轨）/ `scenes`（分镜，scenedetect 检测、缺失则按间隔）/
  `keyframes`（关键帧路径 + 时间戳）/ `audio` / `transcript`（转写，复用 `asr.py`）。
- `keyframes/*.jpg`：你（多模态）读这些图，补 描述/主体/构图/配色/文字/节奏/风格。

**理解源视频的输出（供素材库/内容理解，不进入「参考制作」的概念/样片流程）：**
```
这条视频：[2 句讲清在讲什么]
内容/主题：[核心信息、人物/场景、关键数据]
结构：[X 个镜头，Y 秒，节奏]
风格/调性：[节奏/画面/能量/配色]
关键看点/钩子：[2-3 个]
转写/口播：[要点]
复用建议：[适合做什么类型的素材/内容；是否受保护/侵权/需规避]
```

> 与「参考制作」的区别：**理解源视频 = 看懂这条视频本身**（供素材库归档/复用判断/内容理解）；
> **参考制作 = 分析后做一条「类似的」**（提概念、出样片、路由引擎）。
> 用户只要「理解/梳理/看看」→ 停在 brief + 上面解读；用户说「做个类似的」→ 走完整第 1–7 步。

**错误处理（理解源视频）：**
- 无 yt-dlp / 无法下 URL → 让用户存本地文件再报路径。
- scenedetect 缺失 → 按间隔分镜（结果可用，镜头边界不精确），如实告知。
- 视频无音轨 / 转写失败 → 跳过转写，仍给分镜 + 关键帧 + 视觉解读。
- 含受保护/侵权/真人/品牌 → 理解可继续，复用建议里标「需规避/不可照搬」。

## 执行步骤

### 1. 分析参考（取数据 + 你来看懂）

先取结构化数据（分镜/抽帧/时长/音轨/转写），再由**你**（多模态）读关键帧看懂：

```bash
python skills/shared/scripts/ref_analysis.py analyze \
  --source "<url 或本地路径>" --depth standard --max-keyframes 16 \
  -o outputs/ref/<名字>
# 只想要转写：加 --transcript-only；要逐镜 motion 判定：--depth deep
```

产出 `outputs/ref/<名字>/video_analysis_brief.json`（含 metadata / scenes / keyframes /
audio / transcript，`motion_summary` 与 `five_aspect` 待你回填）+ `keyframes/*.jpg`。
**读 `keyframes/` 里的图**——你是多模态模型，工具给结构，你给理解：
逐帧补 描述/主体/构图/配色/文字；跨帧看 视觉连续性与风格一致性；判 类型/制作质量/主色/
版式/转场。把观察写回 brief 的 `content_analysis`/`style_profile`/`replication_guidance`。

> 无 yt-dlp 时 URL 源会报错 → 让用户把视频存成本地文件再报路径。
> scenedetect 缺失时按间隔分镜（结果仍可用，只是镜头边界不精确）。

### 2. 呈现给用户（不是数据堆砌，是口语化解读 + 强制 5-aspect）

先给一段口语化解读，再给**强制 5-aspect**（下游 stage 直接读，不要压回散文）：

```
"我看完了这条视频。我看到的是：
内容：[2 句讲清楚这条视频在讲什么]
风格：[1 句——节奏/画面处理/能量]
结构：[X 个镜头，Y 秒，节奏风格]
Motion：[N/M 个镜头是 动态片段 / 静图动效 / 静图。这条视频用的是 AI 生成视频片段 /
        静图+Ken Burns 缓动 / 混合]

5-aspect（逐镜头或逐镜组）：
- Subject（主体）：类型/数量/属性；跨镜如何切换（reveal/disappear/switch/alternating，或 N/A）
- Subject Motion（主体运动）：按时间序的动作；交互；或 N/A
- Scene（场景）：叠加层（文字/图形，单列）；POV（航拍/OTS/微距…）；环境；时段；动态
- Spatial Framing（空间构图）：景别；主体位置；景深；相对高度；如何变化
- Camera（镜头）：播放速度；镜头；高度；角度；对焦/景深；稳定性；运动

它为什么好看：[2-3 个具体点——钩子/节奏/转场/旁白风格]
现在看看用你当前的配置能做什么…"
```

**5-aspect 是强制结构**，逐镜走一遍五个方面再写，不要只写叙事。

### 3. 逐镜 motion 判定与素材选择

**绝不猜**参考用的是静图还是视频。读 keyframes 判每段 `motion_type`：

| motion_type | 含义 | 素材与合成 |
|-------------|------|------------|
| `motion_clip` | 主体或环境真实运动 | 从用户素材、`stock-footage` 或 `ai-video-gen` 获取视频，交给 OpenFilm 编排 |
| `animated_still` | 静图加缩放或平移 | 用户素材或 `ai-image-gen` 出图，OpenFilm 制作静图动效 |
| `static_image` | 静态图像 | 用户素材、`ai-image-gen` 或 `card-*` 出图，OpenFilm 编排静帧时长与转场 |

逐镜保留参考的运动特征，真实动作镜头使用合适的视频素材。混合类型在 `per_scene` 中标明，写回 brief 的 `motion_summary`。
所有类型默认使用 OpenFilm 合成，用户明确选择专用流程时沿用其选择。

### 4. 提 2-3 个差异化概念

用户尚未选定方向时，基于 5-aspect 与 motion 判定提出 2–3 个差异化概念。
每个给 `name / hook / 叙事结构 / 视觉路径 / 目标受众 / 为什么能成`。
差异化清单：至少 1 个技术/信息向、1 个直觉/故事向、1 个有冲击力/反常识；
两个概念使用不同叙事结构与视觉路径。用户已选方向时直接沿用，授权自主制作时选定最贴合要求的概念并说明。

### 5. Sample-first（先出 10-15s 样片，再谈全片）

先在同一 OpenFilm 项目做 10–15s 代表段，覆盖钩子与关键镜头，验证运动、风格、节奏与声画。
默认通过 Studio 预览展示样片，素材保存在 `outputs/<主题>/` 项目内，按需说明样片和全片的生成成本。
用户要求先审样片时等待反馈，用户已授权完成全片时自检通过后继续。只有要求导出样片时才渲染 MP4。

### 6. 进入全片制作

| 需求 | 制作路径 |
|------|----------|
| 视频创作，包括真实运动、静图动效、口播和动态图形 | 默认 `video-openfilm`，交付可编辑项目与 Studio 预览 |
| 用户明确选择一键口播流水线 | `auto-short-video` |
| 用户明确要求对已有整片剪辑或重包装 | `video-production`、`clipify` 或对应剪辑工具 |
| 用户明确选择多角色短剧制作流程 | `short-drama` |
| 只写脚本或分镜 | `video-script` |

用户素材库、`stock-footage`、`ai-image-gen`、`ai-video-gen` 与 OpenFilm 媒体库按镜头需要提供素材。
将 5-aspect、逐镜 motion 判定、已选概念与样片结论传给制作流程，直接复用已有分析。
用户明确要求导出、发布、出片或下载时渲染 MP4。

### 7. 自评（CHAI 三轴，动手前就过一遍）

借鉴 OpenMontage `meta/reviewer.md` 的 critique 三轴，逐条过：
- **准确**：每个判断指向具体镜头/帧/字段（如"第 3 镜主体在画面右侧"），不瞎评
- **完整**：发现一处问题就找同类其他处（"还有哪个镜头也是静图当 motion？"）
- **建设性**：每条 critical 问题带具体修法（"caption 写'右侧的人'，人在左侧，改成'左侧的人'"）

过一遍再进入引擎制作。

## 制作约定

- 逐镜 motion 判定指导素材和动画处理，默认由 OpenFilm 完成合成与预览。
- 参考驱动先验证代表段，沿用用户已经确定的概念、风格、画幅和授权范围。
- 按对应 skill 使用工具，结合关键帧实际观察完成视觉判断。
- 用户已指定专用制作流程时保留该选择。仅在阻碍执行的歧义仍未解决时提问。

## 错误处理

| 情况 | 处理 |
|------|------|
| 无 yt-dlp / 无法下 URL | 让用户存本地文件再报路径，不用占位素材 |
| scenedetect 缺失 | 按间隔分镜（结果可用，边界不精确），如实告知 |
| 参考太短/无信息 | 按主题走 `video-openfilm`，说明无法参考 |
| 参考含无法复制元素（真人/品牌/版权） | 提差异化概念绕开，不照搬受保护内容 |
| motion 判定模糊 | 取"多数镜头"判定，混合就在 per_scene 标清 |

## Profile 感知

- 有 Profile：`style.md` 调性/视觉贯穿 5-aspect 的 style_profile 与概念；`platforms.md` 给画幅/
  时长建议（复用已确定的画幅）；`preferences.md` 红线过滤（受保护/侵权/违规内容）。
- 无 Profile 时按用户要求与参考提取风格，用户授权自行决定时说明采用的画幅与平台假设后继续。
