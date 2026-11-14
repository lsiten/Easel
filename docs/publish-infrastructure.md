# 发布基础设施与迁移入口

Easel 采用 Python/FastAPI、SQLite WAL 和本地文件存储实现平台接入、任务编排、素材管理及归因。AiToEarn 固定来源为 `4b59b10ff960a269ce8ad4b77075c0522b11e5a6`。归档校验值、文件清单和许可证分类见 [来源清单](aitoearn-provenance.json)，逐项覆盖与验证状态见 [迁移覆盖表](aitoearn-migration-coverage.md) 和 [官方平台核对表](channel-adapter-migration.md)。

## 服务入口

| 能力 | HTTP 入口 |
| --- | --- |
| 综合平台能力 | `GET /api/platform-capabilities` |
| 官方平台配置状态 | `GET /api/channels/platforms` |
| OAuth 开始、回调、session、刷新 | `/api/channels/{platform}/auth/*` |
| 账号、二级账号选择、授权状态、分组排序 | `/api/channels/{platform}/accounts*`、`/api/channels/account-groups*` |
| 作品、数据快照、发布选项、互动 | `/api/channels/{platform}/{works,analytics,publish-options,comments,...}` |
| 官方发布预检、提交、处理完成、作品回读 | `/api/channel-publish/{platform}/{precheck,publish}`、`/{run_id}/{finalize,reconcile}` |
| 排队与待处理作品更换凭据后的重新审核 | `/api/channel-publish/{platform}/{run_id}/reapprove/precheck`、`/reapprove` |
| 现有浏览器发布与短信接管 | `/api/publish/{platform}`、`/status`、`/sms`、`/reconcile` |
| 原生应用限时分享模板、公开媒体、二维码与结果查询 | `/api/native-handoff/*`，访客页面使用根路径 `handoff` 与 `token` 参数 |
| 人工发布包 | `POST /api/manual-publish/{platform}/export` |
| 多平台流程、定时任务、取消、改期、失败重试 | `/api/publish-flows`、`/{flow_id}`、`/tasks/{task_id}/{action}` |
| 历史筛选、公开状态、作品链接、日聚合 | `/api/publish-records`、`/queued`、`/published`、`/summary`、`/{run_id}` |
| 素材与文案及各自分组、移动复制、使用次数 | `/api/content/{media,material}/*` |
| 公网 URL 安全导入 | `POST /api/content/media/import-url` |
| 两阶段上传、签名公共上传与确认 | `/api/asset-uploads/*` |
| 当前画像的用户资料和 AI 偏好 | `/api/local-user`、`/ai`、`/ai/{kind}` |
| 调用方 API Key 的创建、列出、撤销 | `/api/access-keys` |
| 短链创建、查询、删除及公开跳转 | `/api/short-links`、`/s/{code}` |

命名画像必须已经存在。各模块均使用明确的 persona/account 作用域。Google Business 的上游模块提供授权和账号选择。其发布、作品、分析接口返回不支持。LinkedIn 的作品与分析能力按上游注册情况返回不支持。

## 官方平台配置

在 `.env` 或服务进程环境中配置每个平台的应用。平台前缀使用大写和下划线，例如 `GOOGLE_BUSINESS`。

```dotenv
EASEL_YOUTUBE_CLIENT_ID=...
EASEL_YOUTUBE_CLIENT_SECRET=...
EASEL_YOUTUBE_REDIRECT_URI=http://localhost:7860/api/channels/youtube/auth/callback
# 可选，以空格分隔，默认采用对应适配器声明的权限
EASEL_YOUTUBE_SCOPES=...
```

支持 youtube、tiktok、instagram、facebook、threads、twitter、linkedin、pinterest、google-business，以及 bilibili-open、kwai-open、douyin-open、wechat-official-open、wechat-channels-open、rednote-link。原生别名的环境前缀可使用完整 ID 或去掉 `-open`、`-link` 后的名称，使用 APP_ID/APP_KEY 和 APP_SECRET 别名也可。提供商要求的回调域名、应用审批和权限需在后续真实账号联调中验证。回调既支持 GET，也支持带一次性 state 的 POST。

Graph 图片/视频、TikTok 图片、Pinterest 图片使用平台拉取媒体。为这些路径配置可从平台访问的 HTTPS 地址。

```dotenv
EASEL_CHANNEL_MEDIA_BASE_URL=https://your-easel-host.example
```

服务提供限时签名媒体 URL，签名绑定 run、画像、媒体序号和已审核的 SHA-256。平台拉取冻结后的审核版本。设置公开域名时，同时使用已有的受信 Host 配置和反向代理入口。

凭据保存在 `outputs/_channels`，使用本地私钥加密。API 响应隐藏令牌、上传会话 URL 和服务器 receipt。Facebook 发布使用已选择的 Page token。

## 原生分享与签名通知

抖音原生 OAuth 和小程序二维码使用同一平台标识，小程序还需 MINIAPP_CLIENT_ID、MINIAPP_CLIENT_SECRET。公众号发布身份绑定实际应用 App ID。视频号通过 `/api/channels/wechat-channels-open/accounts/bind-cookie` 验证当前 finder 身份后加密保存 Cookie。小红书链接解析保留 xsec_token，分享 SDK 使用应用级配置。

抖音、小红书模板在创建时冻结当前画像媒体，生成有限时且可撤销的分享授权。访客页面只访问该授权内的内容及媒体，明确审核后生成应用分享参数。抖音 scheme、share ID、open ticket 签名和短链接由服务端生成，实际发布完成经 share 结果查询确认。小红书 SDK verifyConfig 由配置的 ACCESS_TOKEN_URL 桥接服务签名，公开结果只包含 SDK 校验参数。SDK 由访客点击加载，文案提供复制入口。

原生平台的 CLIENT_ID、CLIENT_SECRET 和 WEBHOOK_VERIFY_TOKEN 按实际平台配置。回调先校验签名再保存加密 inbox，只有应用 ID 与服务器已保存的 share/resource/publish ID 精确匹配才进入画像账号事件。事件唤醒真实平台回读。

## 审核、调度与恢复

预检绑定内容、画像、实际账号、媒体 SHA-256、发布策略及平台选项，返回审核摘要。提交必须携带 `reviewed=true` 和摘要。官方接口还要求预检返回的到期时间。媒体、账号或内容变化会使审核失效。浏览器执行前会重新验证当前账号身份。

官方定时预检的 `scheduled_for` 使用带时区的 ISO 时间，支持未来30天。创建流程时给出相同时间的 `publish_at`、共享 content、逐平台 items、各自的审核摘要和 context。调度器从持久化任务中领取到期项，并调用正常发布入口。修改计划或重试需要新的人工审核。已确定失败的任务创建新的尝试身份，旧账本记录保留。未知结果和已提交任务继续核验。

账本位于 `outputs/_publish/ledger.sqlite3`，流程任务位于同目录的 `tasks.sqlite3`。平台 worker 使用续期租约和 fencing token。每个外部阶段先持久化 inflight 标记，再保存服务器 receipt。处理中结果保存为 submitted。服务会继续轮询这些已知阶段，并消费已验证 webhook 作为唤醒信号。正式发布完成依据服务器 receipt 或实际账号与作品回读。

中断的外部写入进入 unknown。相同内容即使换幂等键也会被待核验记录拦截。恢复接口查询真实平台证据。客户端提供的 verified 字样不会建立成功结果。微信后台创建草稿保存为 submitted。人工导出保存为 ready_for_manual_submission。

凭据轮换后的排队与待处理官方任务使用 reapprove 入口重新审核。审核页面提供最初内容与冻结媒体及封面预览。排队任务保持原 run 与计划时间，调度到期后继续执行。已提交任务继续原有服务器阶段。因授权变化产生的历史失败任务可在同一冻结 run 上恢复，跨库中断后重放可补齐任务状态。

```dotenv
EASEL_PUBLISH_MODE=manual
# 或只暂停部分平台
EASEL_PUBLISH_DISABLED_PLATFORMS=twitter,douyin
```

官方 worker 在各阶段重读策略。内置浏览器流程在最终提交和发布短信验证时重读策略。外部 CLI 工具调用使用启动前的策略检查。

## 回写、素材和归因

确认发布与 outbox 入队在同一 SQLite 事务中完成。日历、publish-log 以 run_id 幂等更新，保留已有指标、备注和画像评分。`context.materialId`、`materialGroupId`、`mediaIds` 引用当前画像的素材记录，确认发布后以 run_id 去重增加使用次数。中断后的重放继续完成待回写项。

新分析数据追加到 `outputs/_analytics/snapshots/<persona>/<platform>.jsonl`，带来源、抓取时间、稳定账号 UID 和原始响应哈希。缺失指标保持 null。旧 JSONL 作为兼容读取来源。

素材和文案采用独立分组。默认组受到保护，删除自建组会把记录移回默认组。删除文案的关联素材选项遵循上游单条删除行为。素材文件的所有权由正常项目中的 `.easel.json` 证明。

两阶段上传需要先签发绑定大小、哈希、画像、会话和有效期的 grant。默认目标为聊天附件，`target=media` 创建正常的画像所有项目，供素材登记和发布使用。公开接收、确认和回调逐次验证签名。接收进程中断后通过尝试身份和期限进行安全回收。

远程媒体导入和参考分析校验公网地址、重定向、大小和超时。字节下载使用固定已验证地址的连接，采用直接连接。yt-dlp 经本机鉴权校验代理处理各级媒体请求，并限制外部网络下载器的回退。

## 调用方凭据与写入保护

写操作使用回环来源或配置的 `X-Easel-Write-Token`，并保留浏览器 Origin 检查。签名上传、平台签名 webhook 和一次性 OAuth state 回调各自验证其授权材料。

调用方 API Key 的明文仅创建时返回，存储保留哈希、名称、权限和最后使用时间。调用使用 `X-Easel-API-Key` 或 `Authorization: Bearer ...`，必须匹配画像及读写权限。Key 管理接口要求管理访问。二进制上传按流读取。

终态发布记录可以从历史视图移除，底层幂等身份保留。公开记录仅返回平台、状态、时间和作品链接。

## 验证与维护

### 平台工作台接入

平台工作台将同一平台的浏览器接入和应用授权放在同一个入口。账号页已有扫码登录时，默认使用浏览器接入，复用原有登录态、身份校验、发布、媒体选择、预检和发布记录。浏览器接入直接读取原有账号接口，应用未配置时也能使用。

应用授权保留原有官方平台适配器、OAuth、二维码或应用接续流程。只有官方适配器的平台显示应用授权入口，只有原有浏览器流程的平台显示浏览器入口。两种接入按各自已实现的平台能力开放操作。应用配置属于项目，授权账号和工作区仍按画像隔离。

点击「一键配置应用授权」会创建配置对话，由当前已安装的工具型 Agent 读取 Ego Skill 并操作开发者控制台。登录、验证码、实名、协议、付费和平台审批交由用户处理，同一对话继续配置任务。缺少真实域名或资质时保留待处理进度。也可手动填写已有应用配置。

配置管理接口位于 `/api/channel-applications`，沿用管理写入保护。密钥在 Ego 变量与配置保存请求之间传递，读取接口仅返回字段是否存在。保存经过字段白名单和原有环境值校验，使用文件锁和原子替换保留其他配置，文件权限为 `0600`。配置任务记录不保存密钥。服务端分别核验「应用已配置」和「账号授权已连接」。

配置任务使用已有聊天和 Agent 会话机制。当前选择 API 型运行时时，一键浏览器配置提示切换到已安装的工具型 Agent，原有浏览器账号仍能使用。浏览器发布草稿独立保存当前画像和平台，兼容原有发布页草稿。

本轮通过本地开发者控制台和替代外部模型的 QA harness 验证入口、等待用户状态、保存和回读，OAuth 使用 MockTransport 验证真实内部连接路径。真实开发者控制台、登录及审批后续按用户账号条件联调。

```bash
.venv/bin/python -m pytest -q
cd web/frontend
npm run build
```

SQLite 账本提供 backup、checkpoint 和 integrity_check。备份原子写入且权限为0600。现有回归包含旧 schema 升级、备份恢复、进程中断、过期租约、两画像隔离、回写重放和离线平台协议。当前平台联调采用 MockTransport 和本地 HTTP 契约，真实开发者应用、平台权限、实际上传和账号发布另行验证。
