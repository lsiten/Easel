# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | video-openfilm |
| **所属层** | produce |
| **来源类型** | 集成（OpenFilm） |
| **原始来源** | [openfilm/openfilm](https://github.com/openfilm/openfilm)（The open source AI video agent） |
| **参考项目** | OpenFilm（`openfilm` CLI + Studio 可视化编辑器 + `frame(t)` 页面电影格式；技能 `edit`/`media`/`canvas` 随 OpenFilm 分发） |
| **依赖** | Node.js 22.2+、ffmpeg、git；渲染浏览器（Chromium，首次 open 下载约 100MB）；素材/音乐多来源——OpenFilm 自带 CC0 媒体库（免 key）、Pixabay 免费库（免 key 免版税，经 `pixabay-music` / `pixabay_footage.py`）、生成 AI 媒体经 Studio Settings→Services 接服务 |
| **许可** | OpenFilm：MIT（格式 / CLI / Studio / 桌面应用）；OpenFilm 名称与 Logo 为商标，见其 TRADEMARK.md |

> 整理时间: 2026-11-07
> 用途: 来源溯源与致谢

## 说明

本 SKILL 将 OpenFilm 作为 Easel 默认的视频制作与合成引擎，统一组织用户素材、实拍素材和 AI 生成素材。
封装 `shared/scripts/openfilm.py` 按会话或显式项目驱动 CLI。流程为能力检查、读取内置制作规范、
打开项目、编排镜头与声音、取帧自检、交付可编辑项目和 Studio 预览。用户要求导出、发布、出片或下载时渲染 MP4。
Web 视频面板优先显示固定到当前项目的 Studio 编辑器，并提供对应项目的播放预览。
`stock-footage`、`ai-image-gen` 和 `ai-video-gen` 提供素材，用户明确选择专用制作流程时沿用该选择。
