# 模型配置与执行路由

Easel 将“执行底座”和“模型通道”分开，同时保证每个模型请求都从这两层之一获得配置。

## 唯一配置入口

- **Agent 底座**：Codex、OpenCode、ACP、Pi 或仅聊天模式。保存在
  `outputs/_harness/config.json`，认证沿用原生 CLI；仅聊天模式使用 `.env` 中的 chat 通道。
- **六个模型通道**：对话与脚本、语音转写、配音、生图、视频、音乐。
  供应商、Base URL、API Key、模型名称、默认 provider 和通道高级参数统一在
  “设置 → 模型配置 → 六个通道”维护，并写入项目根 `.env`。
- 对话、生图/改图、视频的模型能力也分别在该通道内维护；转写和音视频运行参数
  放在各自通道的高级项中。旧版 `app-config.json` 中的模型能力仅作为兼容数据读取，
  “更多设置”不再提供第二套模型目录 UI。

旧版本写在 `outputs/_generation/app-config.json` 的 provider 凭据会在首次读取时迁移到
`.env`。迁移前会保留 `app-config.pre-channel-migration.json`，迁移后旧字段被清空；
通过配置 API 再写入 provider 会返回 `provider_config_managed_by_channels`。

## 路由规则

- 对话、工具、文件、浏览器和 Skill 执行：使用当前 Agent 底座。
- 仅聊天模式、批量规划、内容记忆和结构化文本任务：使用 chat 通道。
- 图片生成/编辑：使用 image 通道。
- 视频生成及视频处理：使用 video 通道。
- 字幕和语音理解：使用 transcribe 通道；本地 Whisper 是其中一种 provider。
- 配音：使用 speech 通道。
- 音乐：使用 music 通道。

每个通道内先过滤掉必填配置不完整的 provider，再把可用 provider 按其列表顺序
作为候选。显式设为「主」且本身可用时排第一；主 provider 缺少配置时不会阻止后续
可用 provider，实际生成会落到第一个可用项。未配置 provider 仍会显示，便于补齐
配置，但不会参与路由。

模型能力不是第二套 provider 配置。模型的 `channel` 只决定它复用哪一条 provider
记录；请求实际发送时使用该 provider 的 Base URL、密钥、模型覆盖和通道参数。
不同 provider 可以声明同名模型，路由按上述可用性顺序选择；同名能力不会再被视为
配置冲突。图片生成、改图、视频模式和分辨率等能力声明与该 provider 在同一份能力
目录中读取。

设置界面也使用这一边界：每个通道只有一张统一的 provider 表。展开任一 provider
的「配置」后，在同一位置编辑它的模型与能力；生图和视频的多项能力使用同一展开区
内的标签切换，不再在页面下方维护另一套模型配置。顶部「保存配置」一次提交当前
通道的 provider 变更和模型能力变更。转写、配音和音乐采用相同结构，其单模型能力
直接复用 provider 行里的模型字段。

### 视频通道高级项

普通文生视频、图生视频只使用 provider 行里的地址、Key 和模型，不依赖火山 VOD
账号。视频通道下方的高级项按用途分组：

- `VOLCENGINE_ACCESS_KEY_ID`、`VOLCENGINE_SECRET_ACCESS_KEY` 和
  `VOLCENGINE_SPACE_NAME`：智能视频/VOD 的请求签名与素材空间，只用于翻译、擦除、
  高光、解说、风格转换和轨道编辑，不是 Seedance/ARK 生视频密钥。
- `VOLCENGINE_VOD_BASE_URL` 和 `VOLCENGINE_REGION`：VOD OpenAPI 地址与签名区域。
  默认分别是 `https://vod.volcengineapi.com` 和 `cn-north-1`，通常无需填写。
- `VOLCENGINE_PLAYBACK_BASE_URL` 和 `VOLCENGINE_URL_AUTH_PRIMARY_KEY`：当结果只返回
  VID、没有直接下载 URL 时，用于生成带签名的播放地址并归档成品。
- `EASEL_CHANNEL_MEDIA_BASE_URL`：让生成模型或发布平台读取本机参考素材的公网 HTTPS
  根地址。纯文字、且没有本机素材引用的任务不需要。
- `EASEL_DRAFT_MODEL_RATES`：按模型名配置批量创作的成本预估，只影响界面预估，
  不参与模型请求，也不改变实际计费。

批量规划留空时从 chat 通道解析默认模型。需要参考图或视频帧时会校验模型声明的
图片输入能力。批量图文带参考图时使用 image 通道的编辑模型目录。

## 环境变量兼容

Skill 和第三方脚本仍可读取既有环境变量。`.env` 是这些变量的持久配置源，进程环境
变量在部署时具有更高优先级。Web、API、CLI 和 Agent 子进程读取同一份有效值，因此
不会再出现“Web 创作已配置、Skill 仍缺配置”的分裂。
