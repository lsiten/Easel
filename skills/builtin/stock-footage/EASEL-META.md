# Easel SKILL 元数据

| 字段 | 值 |
|------|-----|
| **SKILL 名称** | stock-footage |
| **所属层** | produce |
| **来源类型** | 自研 |
| **原始来源** | Easel 自研（`skills/shared/scripts/pixabay_footage.py`，Ego 浏览器 + CDN 直链） |
| **参考项目** | 思路沉淀自 OpenMontage `method/skills/creative/stock-sourcing-usage.md`（真实 motion 片段检索 + POV 查询构造 + 评分 + 失败升级；非「animate-stills 静图冒充动态」）；抓取机制复用 Easel `pixabay-music`（Ego + CDN） |
| **依赖** | Ego / ego-browser CLI、playwright（与 pixabay-music 同机制）；可选 `assets.py`（登记进画像素材库） |
| **许可** | Pixabay / Pexels 视频：免费、可商用、无需署名；本实现为 Easel 自研抓取脚本 |

> 整理时间: 2026-11-08
> 用途: 来源溯源与致谢

## 说明

补齐 Easel 一键短视频 / 视频制作里**缺失的真实动态画面检索**：此前 `auto-short-video` 只有 AI 生图 + Ken Burns 静图缓动（`animated_still`），`motion_clip`（该段需真实运动）只能靠 `ai-video-gen`（要 provider/key）。本 SKILL 用 Pixabay 免费视频库抓**现成实拍 / 空镜 / B-roll**（免 key、免版税），让 `motion_clip` 段先走真实动态、失败才降级 AI 生成——直接落实 OpenMontage「真实 motion 片段，不用静图冒充动态」的核心纪律。
