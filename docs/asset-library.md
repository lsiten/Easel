# 每账号素材库（Asset Library）

Easel 支持**每个账号（画像）独立的素材库**：用户只需把制作视频用的素材目录（图片 / 视频 / 文本）登记进去，
Easel 会**按内容理解自动整理**——文本直接读、图片看、视频转写——再按理解到的主题归类到相应目录，
供后续**视频创作**和**文章创作**取用。

设计目标：**用户只添加目录，其余交给理解与整理**；整理结果随画像隔离，创作时按主题检索取用。

## 与既有能力的关系

| 能力 | 作用 | 数据来源 |
|---|---|---|
| **素材库（本文）** | 每账号「可创作的素材」，按内容理解整理，供视频/文章取用 | 用户登记的素材源目录 |
| 本地知识库（kb / Otter Wiki） | 检索/沉淀**已发布或既有**的内容，按画像聚焦 | `kb_scopes.json` |
| OpenFilm 子项目 | AI **视频制作**引擎 + Studio 可视化 | `subprojects/openfilm/` |
| 外部内容源 | 只读浏览已发布产物树 | `_content_sources()` |

素材库与知识库互补：知识库是「已有什么」，素材库是「可用来创作什么」。两者都随画像，但互不干扰。

## 数据模型

- **登记清单**：`profiles/asset_scopes.json`，与 `kb_scopes.json` 同构：`{ "<画像>": [绝对源目录, ...] }`。
  每个画像可登记若干素材源目录；登记清单只存目录路径，不复制内容。
- **整理树**：`profiles/<画像>/assets/`，按**主题分类目录**组织，如：
  ```
  profiles/禾城AI造物局/assets/
  ├── 嘉兴本地/            # 分类目录（按理解到的主题）
  │   ├── 嘉兴本地.md
  │   └── manifest.json    # 本分类的条目元数据
  ├── AI与产品/
  │   ├── plugin.md
  │   └── manifest.json
  ├── _index.json          # 全量条目索引（检索用）
  └── _meta.json           # 源目录 + 整理时间
  ```
- **落位方式**：文件以**软链**落位（`_link_into` 先 `symlink_to`，失败回退 `copy2`），
  避免重复大文件；源目录变动后软链自动跟随。
- **条目元数据**（`manifest.json` / `_index.json` 每项）：
  `name / kind / category / tags / summary / source / mtime`。

## 整理（按内容理解）

整理分两条路径，二者共用同一套落位与索引逻辑：

1. **Agent 驱动（推荐）**：Agent 逐件**理解内容**——
   文本直接读、图片走视觉理解、视频走转写（`asr`）——
   然后逐件 `place <画像> --file F --category C --tags ... --summary ...` 归类。
   这是「按理解的内容整理」的完整形态，由 `skill-asset-library` 技能引导。
2. **基线整理（可独立运行 / 自检）**：无 Agent 计划时，`organize` 用 `scan` 扫源目录，
   按「类型 + 文件名/内容关键词」做基线归类（见 `_TOPIC_RULES` 主题桶，如 `嘉兴本地 / AI与产品 / 人物` 等），
   兜底按类型（`图片 / 视频 / 音频 / 文本`）。保证未接 Agent 时也能跑通与自检。

`organize --dry-run` 只返回整理计划不落位。

## 检索（供创作取用）

视频 / 文章创作 skill 随**当前画像**调用 `query`，按主题/类型检索相关素材：
对 `category / tags / summary / name / kind` 做词法命中，按命中数排序返回。
仅检索本画像素材库，不跨画像。

## 接口

### CLI（`skills/shared/scripts/assets.py`）

```bash
python skills/shared/scripts/assets.py sources <画像>                 # 已登记素材源目录
python skills/shared/scripts/assets.py add <画像> <目录> [更多目录…]   # 登记素材源目录
python skills/shared/scripts/assets.py remove <画像> <目录>           # 注销
python skills/shared/scripts/assets.py scan <画像> [--json]          # 扫描待整理文件（不改动）
python skills/shared/scripts/assets.py organize <画像> [--dry-run]   # 基线整理；Agent 可逐件 place 精修
python skills/shared/scripts/assets.py place <画像> --file F --category C [--tags a,b] [--summary ...] [--kind image|video|text]
python skills/shared/scripts/assets.py list <画像> [--category C] [--json]
python skills/shared/scripts/assets.py query <画像> <主题/关键词> [--kind image|video|text] [--limit N] [--json]
python skills/shared/scripts/assets.py check [--json]              # 能力/状态
```

### Web 接口（`web/app.py`，均随 `?persona=`）

| 接口 | 作用 |
|---|---|
| `GET /api/assets/check` | 能力/状态：是否已登记、已整理多少、多少分类 |
| `GET/POST/DELETE /api/assets/sources` | 查看 / 登记 / 注销素材源目录 |
| `GET /api/assets/tree` | 素材库顶层树（分类目录 + 文件计数），并入内容库 |
| `GET /api/assets/children/{path}` | 某层子节点（懒加载，只读；`@assets/<子路径>` 防穿越） |
| `GET /api/assets/media/{path}` | 文件原样内容（媒体/文本预览） |
| `GET /api/assets/query` | 按主题/类型检索（供创作 skill 调用） |
| `POST /api/assets/organize` | 启动后台 AI 内容理解与归类，返回 202 和任务进度；`dry_run` 仅分析不落位 |
| `GET /api/assets/organize` | 当前画像最近任务（进行中/已完成/失败），刷新页面可恢复 |
| `GET /api/assets/organize/{id}` | 按任务 ID 查看当前画像的进度；其他画像不能读取 |

- **路径约定**：素材库内路径以 `@assets/<分类>/<文件>` 表示；`_assets_resolve` 在**未 resolve** 的
  路径上做 containment 校验（保留软链，媒体/目录预览经 `FileResponse`/`iterdir` 在系统层自然跟随链接），
  并显式拒绝含 `..` 或绝对路径段的子路径，杜绝越界。
- **内容库集成**：`get_output_tree` 把素材库节点（`assets: True`）并入内容库；Web 前端识别 `assets: True`
  节点，懒加载/媒体走 `/api/assets/*`，只读且隐藏删除，显示「素材库」徽标；内容库工具栏提供「整理素材」。

### 技能（`skills/builtin/skill-asset-library/SKILL.md`）

引导 Agent 完成「登记 → 理解 → 归类 → 检索」：先 `add` 登记源目录，逐件理解内容
（文本直读 / 图片视觉 / 视频转写，兜底文件名），`place` 逐件归类或 `organize` 基线整理，
创作时 `query` 取用相关素材。

Web 登记新目录后会自动启动 AI 整理，「素材库」与「内容库」的整理入口共用后台任务。
图片实际看图，视频经 `video-reference-analyst` 抽帧理解并结合可用转写，文本读取正文。
服务端验证 AI 返回的完整方案只包含本批扫描文件，再落盘；原文件保留，同名素材使用独立链接。
模型或权限失败会显示错误，不会悄悄退回关键词分类并标成成功。此功能需要工具型 Agent，API 仅聊天模式会明确提示切换。
CLI 的 `assets.py organize` 保留快速基线整理语义；Agent 的 `place` 与 Web AI 方案共享同一素材存储层。

## 自检

```bash
python -m easel doctor    # 含「素材库（可选：每账号素材整理）」检查项
python skills/shared/scripts/assets.py check <画像>
```
