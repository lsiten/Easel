---
name: video-openfilm
description: "Easel 默认视频制作与合成引擎。做视频、参考视频创作、静图动效、口播、实拍混剪、动态图形、HTML 视频和镜头编排，默认由 OpenFilm 组织时间线、声音、字幕和画面，交付可编辑项目及 Studio 预览。素材可来自用户素材库、stock-footage、ai-image-gen、ai-video-gen 或 OpenFilm 媒体库。用户明确要求导出、发布、出片、下载或 MP4 时渲染成片。用户已指定专用制作流程时保留其选择。"
layer: produce
---

# OpenFilm 视频制作

OpenFilm 是 Easel 默认的视频制作与合成引擎。电影项目位于 `outputs/<主题>/`，包含 `film.html`、镜头页面和素材。默认交付可编辑项目与 Studio 预览，用户要求导出、发布、出片、下载或 MP4 时执行 `render`。

OpenFilm 的制作规范来自其内置 skills。先读取任务需要的 `edit`、`media`、`canvas`，再按规范编排。

## 意图与来源

- 复用用户已经确定的概念、时长、画幅、平台和素材。完整 brief 可直接开工，用户授权自行决定时说明采用的选择并继续。
- 参考视频由 `video-reference-analyst` 先分析结构、镜头和运动，再交给 OpenFilm 合成。
- 真实运动镜头可由 `stock-footage` 或 `ai-video-gen` 提供，静图可由 `ai-image-gen`、`card-*` 或用户素材库提供。OpenFilm 负责混合素材的时间线、字幕、配乐、转场和预览。
- 用户明确选择一键口播流水线、多角色短剧、已有整片剪辑等专用工作流时，使用对应的 `auto-short-video`、`short-drama`、`video-production` 或 `clipify`。
- 仅请求生成一个 AI 视频片段、剪一个片段、转写或配音时，直接使用对应零件 skill。

## 素材与音乐

优先复用用户指定和已整理的资产，再按画面需要取素材。各来源的可用性由本机能力和服务配置决定。

| 来源 | 入口 | 用途 |
|------|------|------|
| 用户素材库 | `assets.py`，`@assets/<子路径>` | 品牌、人物、自备视频、音乐和图片 |
| OpenFilm 媒体库 | `openfilm.py get -p <主题> <kind>` | 音乐、音效、转场、效果、文字、贴纸与模板，按 `media` skill 的查询和 `--take` 用法取用 |
| 实拍素材 | `stock-footage`、`pixabay_footage.py` | 环境、人物、产品与空镜，遵守来源许可和现有浏览器规范 |
| 音乐搜索 | `pixabay-music` | 媒体库缺少合适 BGM 时按关键词搜索 |
| AI 素材 | `ai-image-gen`、`ai-video-gen`，或 Studio 已配置的 Services | 原创画面、动作与指定风格，按已授权预算和服务配置执行 |

用户询问素材来源时，说明适合当前项目的来源、可用条件与选用理由。

选配乐时读取 [选曲与声音验收](references/music-selection.md)：按本片情绪与节奏比较候选，检查最近 10 个其他项目，记录来源和选择理由。品牌调性相同不代表每条使用同一首；用户指定的主题曲、固定片头或系列动机可以说明原因后复用。

## 执行步骤

### 1. 检查能力并读取制作规范

```bash
python skills/shared/scripts/openfilm.py check
python skills/shared/scripts/openfilm.py skills
python skills/shared/scripts/openfilm.py skill edit
python skills/shared/scripts/openfilm.py skill media
python skills/shared/scripts/openfilm.py skill canvas
```

能力缺失时先检查可复用的本机工具，再按缺失项处理。`install` 会安装 CLI 并准备渲染浏览器，按用户授权执行。

### 2. 创建或打开本会话项目

```bash
python skills/shared/scripts/openfilm.py open <主题>
```

Easel 共用一台 Studio，项目选择按当前会话绑定。`open` 记录主题和项目地址。Web 视频面板优先嵌入本项目的固定编辑器地址（`pin=1`），支持时间线与编辑操作。独立窗口打开本会话编辑器，播放视图使用本项目 film 预览地址。

`outputs/_sessions/<会话>.openfilm-topic.txt` 保存项目绑定，`.openfilm-studio.txt` 保存固定编辑器地址，`.openfilm-editor.txt` 保存编辑器入口，`.openfilm-film.txt` 保存播放预览地址。播放预览使用 `?film-preview=1`，由服务端注入时间线并等比适配视口。

当前会话未绑定项目、绑定失效或显式路径无效时，先定位或创建正确项目。CLI 会报错。有会话的命令始终遵循其绑定，仅独立 CLI 使用且没有 `EASEL_SESSION_KEY` 时允许发现最近的有效项目。

### 3. 制作与迭代

按 OpenFilm 制作规范编辑 `outputs/<主题>/film.html`、页面及素材，复用前序 brief 和已选视觉方向。`get`、`canvas`、`look`、`frame`、`render` 通过 `-p <主题>` 显式指定项目，也可使用有效的当前会话绑定。

```bash
python skills/shared/scripts/openfilm.py get -p <主题> music-search --prompt "本片情绪、乐器与节奏" --sec 35 --limit 5
python skills/shared/scripts/openfilm.py get -p <主题> music-search --take <候选ID>
python skills/shared/scripts/openfilm.py canvas -p <主题>
```

素材的 kind（例如 `music`）和 skill 名（例如 `edit`）保留原命令含义。
`music-search` 搜索免费媒体库；`get music` 是原创音乐生成，取决于已连接服务及费用授权，不能作为免费取曲的默认命令。封装会在搜索时显示近期用曲，取曲时拦截已用 ID，导出时检查实际引用文件与来源记录；绕过封装直接在 Studio 编辑或导出时仍需执行下面的预检。

### 4. 取帧并查看实际画面

```bash
python skills/shared/scripts/openfilm.py frame <主题> 2
python skills/shared/scripts/openfilm.py frame -p <主题> 0-6
python skills/shared/scripts/openfilm.py frame -p <主题> assets/clip.mp4 2
python skills/shared/scripts/openfilm.py look -p <主题> 0-6
```

`frame` 仅在本次 `look` 成功后列出该命令明确返回且存在于本项目缓存的 PNG 绝对路径。失败时先修复并重跑。使用 `view_image` 逐张检查返回的帧，包含片头、关键镜头、转场附近与片尾。

逐项核对画幅、文字可读性、字体、元素层级、主体裁切、镜头连续性与运动。声音通过音轨、时长和实际播放另行核对。需要验证运动时比较多个时间点，缺声、黑帧、白闪、裁切或意外静止必须修复后再交付。

### 5. 交付预览或按要求导出

默认交付本会话 Studio 预览与 `outputs/<主题>/` 项目。用户已要求成片时直接执行已授权导出。

```bash
python skills/shared/scripts/openfilm.py get -p <主题> music-check
python skills/shared/scripts/openfilm.py render -p <主题>
python skills/shared/scripts/openfilm.py frame -p <主题> --full
```

完整导出位于 `outputs/<主题>/<主题>.mp4`。区间渲染和自定义 `--out` 的位置遵循 OpenFilm 的命令输出。导出后使用 `ffprobe` 检查分辨率、时长和音轨，结合 `--full` 与实际帧检查确认成片。

## 交付检查

- 项目与当前会话绑定相符，引用的页面和素材齐全。
- 片头、关键镜头与片尾已有本次成功取帧结果，并实际查看过画面。
- 画幅、内容、运动、声画与用户意图相符。
- 配乐去重通过或记录了用户指定复用的原因；音乐覆盖目标时段，收尾自然，口播清晰。去重通过不替代播放验收。
- 各项检查记录为 `pass`、`revise` 或 `fail`，修复未通过项后重新验证。
- 用户要求导出时交付验证后的 MP4，其他创作交付可编辑项目与预览。

## 依赖与来源

封装入口为 `skills/shared/scripts/openfilm.py`。运行器优先级为 `EASEL_OPENFILM`、子项目、全局命令、npx。OpenFilm 子项目位于 `subprojects/openfilm/`，遵循其 MIT 许可与商标说明。[OpenFilm](https://github.com/openfilm/openfilm)
