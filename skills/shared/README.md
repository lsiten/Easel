# skills/shared

跨 SKILL 复用的工具、脚本与配置。项目根使用 `skills/shared/`；技能的 SKILL.md 引用本目录时向上两层，技能的 references 文档则向上三层。具体写法见 [热点数据参考](hotlist-apis.md)，相对链接以当前文档所在目录为准，执行命令统一先切换到项目根。

## 知识文件

- `hotlist-apis.md` — 中文社媒热搜 API（60s / xxapi 聚合源，走代理；禁止直接抓平台官网）
- `pillar-and-cadence.md` — 内容支柱与发布节奏（策划三角共享知识）
- `scoring-dimensions.md` — 统一评分维度（七维 + 标尺）

## 脚本（scripts/）

- `social_stats.py` — 公共统计计算（互动率/环比/移动平均/加权/聚合），归因层复用，把 LLM 心算固化为确定性代码
- `wordcount.py` — 字数统计与目标字数校验（count/check/selftest，含 social_count 社媒计数口径）。文本类 SKILL（text-condenser/hook-generator/post-formatter/video-script/social-content）统一用它校验字数行数
- `render_card.py` — HTML → 图片确定性渲染（playwright + chromium，走代理加载 CDN/字体、有界超时不卡死）。卡片/海报类 SKILL（card-quote/card-xiaohongshu/poster-hero/comparison-card）统一用它出图
- `image_ops.py` — 通用图像处理确定性封装（纯 Pillow）：resize/crop/pad/convert/compress/watermark/round/collage/thumbnail/info 子命令。处理「已有图片」的加工类 SKILL（image-editing）统一用它，区别于 render_card 的「HTML 设计→渲染」
- `audio_ops.py` — 通用音频处理封装（ffmpeg）：trim/convert/normalize/extract/concat/fade/speed/denoise/info。audio-editing / audio-denoise 用它
- `video_ops.py` — 通用视频处理封装（ffmpeg）：cut/concat/speed/silence-cut/text/aspect(横竖转)/frame/gif/compress/bgm/watermark/info。video-editing 用它
- `asr.py` — 语音转字幕（faster-whisper）：transcribe → SRT/ASS/TXT/JSON，中文友好断句。auto-subtitle 用它
- `tts.py` — 文字转语音配音（edge-tts）：speak（多音色/语速/字幕）/voices。tts-voiceover 用它
- `ai_image.py` — AI 文生图/图生图/变体（OpenAI 兼容 + apimart 异步，用户自备 key）。ai-image-gen 用它
- `ai_video.py` — AI 文/图生视频（provider: 通义万相/火山Seedance/可灵/OpenAI兼容，异步轮询，用户自备 key）。ai-video-gen 用它
- `ai_music.py` — AI 音乐/BGM 生成（provider: DashScope/Suno兼容，用户自备 key）。ai-music 用它

## 依赖

- `render_card.py` 需 `pip install playwright && playwright install chromium`
- `image_ops.py` 需 `pip install Pillow`（图像处理）
- `audio_ops.py` / `video_ops.py` 需系统 `ffmpeg` + `ffprobe`
- `asr.py` 需 `pip install faster-whisper` + ffmpeg；默认模型下载与缓存由 faster-whisper 管理，也可用 `--model-dir` 指定已下载的本地目录。网络不可用时使用本地模型，不预设所有机器需要代理。
- `tts.py` 的 edge 引擎需 `pip install edge-tts` 和到语音服务的网络连接；闭源引擎使用项目 `.env` 中的语音配置。代理按实际网络配置选择。
- `ai_image.py` / `ai_video.py` / `ai_music.py` 纯标准库（urllib/hmac），但需**用户自备各服务的 API key**（配 .env），见各自 SKILL 的配置章节
- `data-report`（pandas+matplotlib）、`infographic/gif_chart.py`（matplotlib+Pillow）、`auto-short-video/assemble.py`（ffmpeg）在各自 SKILL 的 scripts/ 下
- 浏览器账号、发布、评论与数据抓取脚本使用 Playwright，并通过 `easel/browser.py` 统一选择已安装的 Ego，其次 Chrome。已安装 Ego 但启动失败时会报错，不静默降级。`python -m easel.browser` 可只读查看当前选择；非标准安装位置可通过环境变量 `EASEL_EGO_EXECUTABLE` / `EASEL_CHROME_EXECUTABLE` 指定浏览器程序路径。
- Ego 使用原平台目录下独立的 `Ego/` 子目录，通过本机随机端口 CDP 连接隔离会话；首次切换需重新登录。Chrome 沿用原平台目录，不自动迁移 Cookie。Ego 关闭前原子保存 Cookie、localStorage 和 IndexedDB 到目录内的私有快照（POSIX 权限 `0600`），下次启动恢复；保存后释放会话、进程和目录锁。快照包含登录凭证，不得上传或公开。此持久化需要 Playwright 1.51+。
- HTML/PDF/卡片等离线渲染继续使用 Playwright Chromium。图片、音视频、统计等脚本还会使用 Pillow、FFmpeg、pandas 等依赖。项目 Python 依赖由 `pip install -e .` 安装；安装脚本补齐渲染内核并检测平台浏览器，缺少 Ego / Chrome 时提示安装。各脚本 `check` / `--help` 和 `easel doctor` 用于确认当前环境。
