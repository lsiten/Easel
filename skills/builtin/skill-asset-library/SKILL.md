---
name: skill-asset-library
description: "每账号（画像）素材库：把用户提供的素材源目录（图片 / 视频 / 文本）按内容理解 AI 整理成分类素材库，供后续视频创作、文章创作取用。当用户说 我的素材 / 素材库 / 把这几个目录整理一下 / 这些图片视频怎么用 / 给账号加素材 / 按主题归类素材 / 创作时帮我找素材 时使用。区别于 kb 知识库（只读文本检索）与 outputs 内容库（已产出成品）：素材库是「可复用输入素材」，按内容理解归类、随画像。"
layer: produce
---

# 素材库（每账号素材，按内容理解整理，供创作取用）

> 一个账号（画像）可登记若干**素材源目录**（图片 / 视频 / 文本 / 音频），用户只需添加目录即可。
> Agent **按内容理解**把素材整理进分类目录（`profiles/<画像>/assets/`），供**视频创作 / 文章创作**取用。
> 整理由 Agent 理解内容驱动；脚本 `skills/shared/scripts/assets.py` 负责登记、落位、索引、检索。
> 统一入口：`python skills/shared/scripts/assets.py <命令> <画像> ...`
>
> Web 登记目录会自动启动当前 Agent 的后台内容理解任务；「整理素材」可重跑，进度按画像保存。
> 图片看图、视频抽帧理解并结合可用转写；AI 返回的方案由服务端校验后落位。失败如实显示原因。
> CLI `organize` 仍表示快速基线分类，不要把它的结果当成已完成视觉/视频理解。

## 边界（与相邻 SKILL 划清）

| SKILL | 场景 |
|-------|------|
| **skill-asset-library（本 SKILL）** | 可复用的**输入素材**（图片/视频/文本），按内容理解归类，随画像，供创作取用 |
| kb 知识库 / ai-社区 | 只读文本检索与沉淀（BM25 证据包），不是媒体素材 |
| outputs 内容库 | 已产出的成品（文章/视频/卡片），不是待整理素材 |
| video-openfilm / ai-video-gen | 视频**制作**（消费素材库里的素材产出成片），不是整理素材 |

## 数据与路径

- 登记清单：`profiles/asset_scopes.json`（{画像: [绝对源目录]}，与 `kb_scopes.json` 同构）。
- 整理树：`profiles/<画像>/assets/`（分类目录 + 软链落位 + `manifest.json` + `_index.json`）。
- 随画像：素材库只属于当前画像；创作取用只读本画像素材库。

## 执行步骤

1. **登记源目录**（用户只给目录，不移动文件）：
   ```bash
   python skills/shared/scripts/assets.py add <画像> <目录1> [<目录2> …]
   python skills/shared/scripts/assets.py sources <画像>      # 确认已登记
   ```
2. **待整理清单**（先看清楚有哪些、什么类型）：
   ```bash
   python skills/shared/scripts/assets.py scan <画像>
   ```
3. **按内容理解整理**（核心）——逐件理解后归类落位：
   - 文本：直接读内容，取标题/主题/要点。
   - 图片：用 `ai-image`（视觉理解）或看图判断主题；无视觉能力时按文件名/EXIF 归类并提示用户可补。
   - 视频/音频：用 `asr`/转写取主题；无转写时按文件名归类并提示。
   - 逐件落位（推荐，最准确）：
     ```bash
     python skills/shared/scripts/assets.py place <画像> --file <素材绝对路径> \
        --category <主题分类> --tags "标签1,标签2" --summary "一句话摘要" --kind image|video|text|audio
     ```
   - 批量基线整理（类型+关键词，快速兜底，可再 `place` 精修）：
     ```bash
     python skills/shared/scripts/assets.py organize <画像> --dry-run   # 先看分类方案
     python skills/shared/scripts/assets.py organize <画像>            # 落位
     ```
4. **校验**：
   ```bash
   python skills/shared/scripts/assets.py list <画像> [--category 嘉兴本地]
   ```

## 供创作取用（视频 / 文章）

创作 skill 需要素材时，按主题/类型检索本画像素材库，**不要凭记忆裸做**：

```bash
python skills/shared/scripts/assets.py query <画像> <主题或关键词> [--kind image|video|text] [--limit 10]
# 例：
python skills/shared/scripts/assets.py query 禾城AI造物局 嘉兴本地 活动 --kind image
python skills/shared/scripts/assets.py query 水獭比特 AI 编程 --kind video
```

- 视频创作（`video-openfilm` / `ai-video-gen` / `short-drama` 等）：取 `--kind video`/`image` 的画面与镜头素材。
- 文章/图文创作：取 `--kind text`/`image` 的事实、图片与要点。
- 命中的素材 `entry.source` 是绝对路径，直接用于制作；`summary`/`tags` 作为理解线索。

## 边界与注意

- 用户只添加**源目录**，不要求移动/复制原文件；整理树用软链（失败回退拷贝），不重复大文件。
- 无视觉/转写能力时按文件名/类型基线归类并**明确提示**用户可补充理解，不臆造内容。
- 整理可重复运行（幂等落位）；新增源目录后重跑 `organize`/`place` 增量整理。
- 素材库随画像：切换画像后素材库随之切换，不跨画像取用。
