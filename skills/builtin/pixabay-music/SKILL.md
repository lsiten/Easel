---
name: pixabay-music
description: "从 Pixabay 音乐库按关键词查找现成 BGM、配乐与音效，通过 Ego 浏览器和 CDN 下载，供视频制作取用，无需登录或 API key。当用户说找 BGM、配背景音乐、取 Pixabay 音乐、找钢琴或 lo-fi 曲、给视频配乐、抓音效时使用。原创音乐生成用 ai-music；OpenFilm 内置精选库用 music-search。本技能搜索 Pixabay，取用前核实本次素材的许可。"
layer: produce
---

# Pixabay 音乐 / BGM / 配乐抓取（Pixabay Music）

从 **Pixabay 免费音乐库**抓取现成的 **BGM / 配乐 / 纯音乐 / 音效**，供视频制作取用。
全程通过共享脚本 `skills/shared/scripts/pixabay_music.py`：

- 用 **Ego 浏览器**（真实指纹）打开 Pixabay 搜索页 —— 因为 pixabay.com 主站对裸 HTTP 请求返回 403，需真实浏览器才能拿到页面；
- 点击下载按钮触发下载事件，拿到 **CDN 直链**（`cdn.pixabay.com/download/audio/…mp3`）；
- 再用 **urllib 直连下载** MP3 —— CDN 直链可直接拉取（已实测 200），浏览器只负责「拿到直链」。

产物落到 `outputs/<主题>/audio/`，或经 `--to-asset` 登记进画像**素材库**（`assets.py`），供 OpenFilm / 视频制作取用。

**免登录、免 API key、免版税（no-copyright）** —— 无需任何账号或密钥配置。

## 与相邻 SKILL 的边界

| SKILL | 用途 | 区别 |
|-------|------|------|
| **pixabay-music**（本 SKILL） | 从 Pixabay 免费库**抓取现成音乐** | 按关键词海量搜索，免 key |
| `ai-music` | 用 AI **生成**原创音乐 / BGM | 需要 provider / API key，输出原创 |
| `openfilm get music-search` | 搜索并取用 OpenFilm **内置 CC0 媒体库** | 按需求查询，`--take` 取用 |
| `tts-voiceover` | 人声口播 / 旁白 / 朗读 | 人声，不是背景音乐 |

本 SKILL 与 `openfilm get music-search` **互补**：内置库是精选，Pixabay 是按关键词海量搜索。`get music` 是已连接服务的原创音乐生成入口，可能收费，不能当成免费媒体库命令。

## 输入

| 字段 | 必填 | 说明 |
|------|------|------|
| keyword | 是 | 搜索关键词 / 情绪 / 乐器（如 `uplifting` / `温馨` / `钢琴` / `lo-fi` / `激昂` / `钢琴+鼓点`） |
| pick | 否 | 取第几首（`get` 用，默认 1） |
| out | 否 | 输出目录（`get` 用，默认 `outputs/<主题>/audio/`） |
| to-asset | 否 | 下载后登记进该画像素材库（`assets.py place`） |
| category | 否 | 素材库分类，默认 `音乐 / BGM` |
| tags | 否 | 素材库标签，逗号分隔 |

## 输出

- 下载的 MP3 文件（放入 `outputs/<主题>/audio/` 或 `--out` 指定目录）
- `list` 打印搜索结果（编号 + 标题 + 作者），供挑选
- `get` 打印下载路径；`--to-asset` 时登记进素材库并打印落位信息

## 依赖

- **Ego / ego-browser**：`check` 会探测 `easel.browser.select_browser()` 与 `ego-browser` CLI、playwright。未就绪则安装 Ego 后重试。
- 无需任何 API key / 账号登录（Pixabay 音乐免登录、免版税）。

## 执行步骤

> **配置检查路径铁律**：先 `cd` 到运行时说明给出的 Easel 项目根，确认当前目录有 `.env` 和 `skills/shared/scripts/`，再运行 `check` / `list` / `get`。不得改用 workspace 的共享脚本副本。

1. **能力探测**（首次或怀疑环境变化时）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_music.py check
   ```
   确认 Ego / ego-browser CLI / playwright 就绪。

2. **浏览搜索结果**（挑选）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_music.py list "uplifting" --limit 12
   # 机读
   .venv/bin/python skills/shared/scripts/pixabay_music.py list "钢琴" --limit 12 --json
   ```
   打印「编号 + 标题 + 作者」序列，供挑选 `--pick N`。

3. **下载**（取第 N 首，默认第 1 首）
   ```bash
   .venv/bin/python skills/shared/scripts/pixabay_music.py get "uplifting" --pick 1
   # 指定输出目录
   .venv/bin/python skills/shared/scripts/pixabay_music.py get "温馨" --pick 3 -o outputs/主题名/audio
   # 下载并登记进画像素材库
   .venv/bin/python skills/shared/scripts/pixabay_music.py get "钢琴" --pick 1 --to-asset <画像> --category "音乐 / BGM" --tags 钢琴,温馨
   ```
   产物落到 `outputs/<主题>/audio/`；`--to-asset` 时登记进 `profiles/<画像>/assets/音乐 / BGM/`。

4. **取用**：下载 / 登记好的音乐，交给 `video-openfilm` / `video-production` 作为 BGM 轨，或交给 `audio_mix.py` / `audio_ops.py` 做混音、归一化。

## 说明与边界

- **免 key / 免登录**：Pixabay 音乐是免费、免版税（no-copyright），下载不需要任何账号或密钥。
- **为什么用 Ego 不用直连**：pixabay.com 主站对裸 HTTP 请求返回 403（反爬），需真实浏览器拿到页面；但下载的是 `cdn.pixabay.com` 直链，可直接 urllib 拉取（已实测 200）。故浏览器只负责「拿到直链」。
- **下载抓取依赖 Ego 的 download 事件**：脚本点击搜索页的下载按钮以取得 CDN 直链。若 Ego 会话的下载事件未触发（个别环境 / Pixabay 改版），`get` 会返回未拿到直链并打印日志；此时可先 `list` 挑出曲目，或重试。`check` 用于确认 Ego 环境就绪。
- **与 OpenFilm 的关系**：本 SKILL 是视频制作取音乐的**补充来源**（按关键词海量搜索）；OpenFilm 自带 CC0 媒体库通过 `openfilm get music-search` 查询和取用。两者并存。
