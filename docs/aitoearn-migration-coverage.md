# AiToEarn 迁移覆盖清单

核对日期为 2026-11-14，来源固定提交为 `4b59b10ff960a269ce8ad4b77075c0522b11e5a6`，完整只读源码根目录为 `/tmp/easel-aitoearn-source-4b59b10/project/aitoearn-backend`。服务端源码位于 `project/aitoearn-backend/apps/aitoearn-server/src/core/`，文件级平台审计另见 [官方平台适配器迁移核对](channel-adapter-migration.md)。

用户已确认先完成代码和离线契约验证。平台开发者应用、审批权限和测试账号尚未提供，真实授权、上传和发布保留为待验证。

## 当前复核状态与历史结论更正

此前“既定排除项之外全部完成”的结论已被六项缺口审计明确取代，不能继续作为当前版本的完整性证明。原 **51 个操作**只覆盖 `aitoearn-ai/src/core` 控制器；没有覆盖共享模块的挂载入口，也不能证明原生供应商分支、普通聊天与任务 UI 的连接、模型能力、队列调度或运维指标完整。审计见 `.omo/evidence/aitoearn-migration-gap-audit-20261114.md`。

原来源清单中的 314 个 server 文件、173 个 AI 文件和 23 个共享契约，以及历史 2006 通过/1 跳过、121 项前端测试和旧门审 APPROVE，均只属于当时的快照与验证范围。`.omo/evidence/aitoearn-ai-source-audit.json`、`aitoearn-source-blob-audit.json`、`aitoearn-ai-complete-gate.md` 及旧运行记录予以保留，但不延伸为此次六项补齐的验收结论。

当前六项已完成修复和验收。包含配置权限与进程指标修复的全量后端为 **2039 通过、1 跳过**（`.omo/evidence/aitoearn-six-gaps-final-process-pytest.txt`）；前端分别为 **121 项 Node 测试**和 **42 项 AI 工作区 Bun 测试**（`aitoearn-six-gaps-frontend-tests.txt`、`aitoearn-six-gaps-final-ai-frontend.txt`）。主代理已在真实页面验证出图、视频、草稿、任务、配置和权限，并实际延迟真实 HTTP 响应，验证记忆类型与草稿分页切换不会被旧响应覆盖。首轮独立复核指出的日志默认筛选422、旧响应覆盖、原始分享错误码与触控区域问题已修复，生成记录补齐了无跳级的标题结构。**两项独立视觉复核与最终门审均通过，阻塞项为空。** 当前产品指纹为 `b1aa0b2f4868eae9d88297d4bb7c56800f44fc6f99d65c77a31bd940c533b922`；详细矩阵、35 个页面状态和 248 组响应式/主题验证见 [.omo/evidence/aitoearn-six-gaps-acceptance.md](../.omo/evidence/aitoearn-six-gaps-acceptance.md)。完成范围为固定来源及约定排除项下的代码、离线契约和本机真实页面验证，仍不宣称真实第三方凭据可用性。

### 六项缺口的代码与离线契约

| 审计缺口 | 当前实现与可核验边界 | 证据 |
| --- | --- | --- |
| 1. 原有 UI 与任务/历史不连通 | AI 工作区提供任务继续、消息、问题回复、收藏/评分/分享/终止、生成历史、产物、批量草稿、记忆和运行时配置；普通聊天通过 observer 关联同一原生会话、Agent 任务和统一日志，保留通用模式与画像隔离 | `test_generation_chat_observer.py`、工作区 `aiWorkspace.test.ts`；`.omo/evidence/aitoearn-gap-product-flow.md` 和最终验收矩阵，真实页面与独立复核已通过 |
| 2. 缺少原生 Gemini 图片与草稿分支 | 明确 Gemini 渠道调用 `generateContent`，处理参考图、imageSize/aspectRatio、原生 usage 和 owned assets；普通草稿复用同一服务。仅模型名含 Gemini 不会强制改变 OpenAI relay 协议；未知付费结果不自动重发 | `test_generation_native_config.py`；`.omo/evidence/aitoearn-gap-native-config.md`。使用离线/回环协议夹具，非真实账号可用性声明 |
| 3. 缺少共享结构化配置流程 | 两个挂载别名各有读、验证、保存、应用，共八个操作；类型校验、密钥隐藏、显式清除、环境覆盖锁、revision CAS、原子私有保存；restart 诚实返回 per-request 应用，不重启主进程 | `web/generation_config_router.py`、`test_generation_native_config.py`、`test_generation_config_authorization.py` |
| 4. 缺少优先级和独立并发池 | SQLite 持久优先级与原子全局准入，图片 3、视频 3、草稿普通 60/低优先级 2、阈值 1000；后台实际并发，HTTP 与跨进程竞争共享限额；新认领读取实时配置，活跃任务保留 owner/class | `test_generation_queue_priority.py`、`test_generation_queue_native_once.py`；`.omo/evidence/aitoearn-gap-queue-metrics.md` |
| 5. 缺少 HTTP/队列指标 | `/metrics` 输出 Prometheus 文本；完整流响应的请求数、状态、耗时、in-flight 和取消；十类存储的真实状态、开始/完成/失败、等待/处理时间与 attempts，草稿池及当前限额；补齐进程 CPU、内存、启动/运行时间与 Python GC；按来源排除指标与健康检查自身请求，不把私密 URL、用户、提示词放入标签 | `test_operational_metrics.py`、`test_process_metrics.py`；队列观测持久化，HTTP 指标按进程保存；旧数据只回填状态，不伪造历史耗时；不支持的 OS 指标省略 |
| 6. 模型能力元数据不完整 | 多模型 chat/image/video 配置与目录保留渠道、runtime alias、比例、分辨率、模态、说明等能力；草稿定价包含 supportedAspectRatios/resolutions；图片与草稿输入按声明能力校验 | `test_generation_native_config.py`、原生草稿与视频契约；`.omo/evidence/aitoearn-gap-native-config.md` |

### 共享配置模块的八个挂载适配

来源为 `libs/config-editor/src/config-editor.controller.ts`，分别挂载在 server 的 `config` 与 AI 的 `ai/config`。对应清单现在区分 **51 个核心操作 + 8 个共享配置挂载操作 = 59 个已列出的来源挂载操作**；这不是所有行为或未来共享模块的自动完整性证明。八个额外业务适配入口仍在独立条目中记录，原生 Gemini 分支和队列/指标另按能力核验，不用路由数量替代行为证据。

| 方法 | Easel server 别名 | Easel AI 别名 | 语义 |
| --- | --- | --- | --- |
| GET | `/api/config` | `/api/ai/config` | 全局结构化配置的脱敏读取 |
| POST | `/api/config/validate` | `/api/ai/config/validate` | 完整配置类型与约束校验 |
| PUT | `/api/config` | `/api/ai/config` | revision CAS 与私有原子保存 |
| POST | `/api/config/restart` | `/api/ai/config/restart` | 后续请求使用最新配置，返回 per-request 应用结果 |

**全局配置不接受画像作用域 API key 作为管理权限。** 两个别名的全部八项操作先拒绝 caller_key，再执行既有全局管理访问校验；read/write 画像 key 即使来自 loopback、携带本人 persona，也返回 403。正常同画像 AI 日志仍可读，跨画像请求仍被拒绝。该修复由四组 read/write × loopback/LAN 场景逐项验证，不能用“有 write key”推断拥有全局应用配置权限。

代码与离线契约验证不等于真实外部凭据验证。开发者应用、审批权限、测试账号及真实供应商服务可用性仍按用户已接受的条件另行联调；以下既有排除项继续有效。

| AI 服务来源 | Easel 落点 | 已实现行为与证据 |
| --- | --- | --- |
| `ai/image` | `generation_images.py`、`generation_router.py` 和既有图片技能 | 同步/异步生成、编辑、多图、适用供应商遮罩、任务、模型别名、URL/data URI、响应元数据。无支持的参数在付费前拒绝。验证见 `test_generation_images.py`、`test_generation_inputs.py` |
| `ai/chat` | `generation_chat_*.py` 与现有模型配置 | OpenAI 普通/流式、Anthropic 原生流、工具与多模态 JSON、usage、模型目录、持久 SSE 回放。画像本地图片转 data URI，本地音视频使用冻结限时媒体地址；已完成重放先于配置与素材检查。验证见 `test_generation_chat_contract.py`、`test_generation_chat_inputs.py` |
| `ai/video/openai`、`ai/video/volcengine` | `generation_videos.py`、原生路由、SQLite 队列 | OpenAI multipart、remix；火山原生场景、参考音视频、行内选项、尾帧与用量。持久原生 ID、崩溃恢复、真实 MP4/PNG 解码归档、画像隔离。验证见 `test_generation_videos.py`、`test_generation_volcengine.py` |
| `ai/video` | `generation_video_generic*.py` | OpenAI、Volcengine、Grok、Dashscope、Relay 五渠道，源字段/场景/模型目录、分页/状态、冻结媒体、公共签名取件、分组投影。未知提交不自动再次付费。验证见 `test_generation_video_generic.py`、`test_generation_video_generic_media.py` |
| `ai/aideo` | `generation_aideo*.py` | 五种技能、原生上传/签名、任务/分页、30 秒后台回收、跨进程 poll fencing、重启与历史重放、真实归档。验证见 `test_generation_aideo.py`、`test_generation_aideo_dispatch.py` |
| `agent` | `generation_agent*.py`、`web/generation_agent_*.py`、既有 Harness | 12 项任务操作、原生对话/附件/模型选择、消息/恢复、终止、评分/收藏、到期分享、删除和画像内问题回复。Harness 自有 SDK wire 格式按 Easel 已有事件适配。验证见 `test_generation_agent_tasks.py`、`test_generation_runtime_integration.py` |
| `draft-generation` 与 `internal` | `generation_draft*.py` | 批量视频/图文、URL 分析、统计/查询、定价、偏好记忆及小时调度、三项内部别名、真实模型/媒体执行与素材/草稿投影。来源平台限制与默认值、音轨本机缓存 ASR 已覆盖。验证见 `test_generation_draft_{tasks,native,business,audio}.py` |
| `ai/logs`、`ai/assets` | `generation_logs.py`、`generation_store.py` | 图片、视频、Chat、Agent、Aideo、草稿、角色与剪辑共用画像日志；实际生成文件进入统一产物索引。SQL 元数据筛选分页避免加载大输入。验证见模块契约与 `test_generation_runtime_integration.py` |

此外迁移了未单独注册源 HTTP 路由的 MIT 业务服务，新增 8 个 Easel 适配入口。风格迁移与剧集解说各有提交/查询；DirectEdit 原生剪辑有提交/查询；Sora 角色扩展有创建/查询。对应 `test_generation_aideo_workflows.py`、`test_generation_direct_edit.py`、`test_generation_characters.py`。角色扩展要求配置的供应商实现固定来源协议，未宣称所有标准 OpenAI 服务均支持它。

源 `ai-availability` 的 execute 直接执行一次回调，record 方法均为空实现，并不存在可移植的熔断器。`models-config` 控制器没有注册 HTTP 操作，当前有效行为由已有本机模型配置和新目录承担。

原有环境字段接口 `GET/POST /api/generation-settings` 继续兼容；完整结构化应用/provider/model/queue 配置使用上述 `/api/config` 与 `/api/ai/config` 管理流程。密钥回读隐藏、进程环境覆盖项锁定。草稿费率可设置真实金额，未设置时金额为未知；JSON 在环境文件中以可安全 source 的 base64 值保存，API 和运行时解码，仍兼容外部注入的 JSON。ASR 只复用已有本机缓存或显式本机模型路径，不自动下载。

## 保留的排除范围

| 范围 | 本项目采用的实现 |
| --- | --- |
| NestJS 应用及 MongoDB、Redis、BullMQ、RustFS/S3 整套部署迁移 | 复用 Easel 的 Python、FastAPI、本地文件和 SQLite，补齐事务、租约、任务派发、回写及备份能力 |
| Monetize 交易市场、支付和商家结算 | 保留排除项 |
| Claude Router 整体替换 | 复用 Easel 当前模型与 Agent 接入 |
| 完整 MCP 产品和专有技能、提示词及品牌资源 | 保留排除项，服务端能力按可用来源独立实现 |

接入范围包括九项国际平台，以及哔哩哔哩、抖音、快手、微信公众号、微信视频号和小红书的六项原生协议标识。每项具体注册能力以固定源码的 module、provider、schema 和 service 为依据。

## 内容与素材

以下来源路径均相对于上述 `core/`。

| 来源 | 操作 | Easel 可调用入口与实现 | 核验状态 |
| --- | --- | --- | --- |
| `content/media-group.controller.ts` | 创建、详情、修改、分页筛选、删除媒体分组 | `/api/content/media/groups`，`content_library.py` | 离线 HTTP 已验证，默认分组受保护，组视图返回最多三张相对路径缩略图 |
| `content/material-group.controller.ts` | 创建、详情、修改、分页筛选、删除草稿分组 | `/api/content/material/groups` | 离线 HTTP 已验证，独立命名空间，删除普通分组时记录事务迁入默认分组 |
| `content/media.controller.ts` | 创建、详情、分页筛选、单条删除、ID 批量删除、按条件删除、增加使用次数 | `/api/content/media/records`、`records/bulk`、`records/filter`、`usage` | 已验证画像、`.easel.json`、媒体路径归属、混合批次回滚和并发使用次数幂等 |
| `content/media.controller.ts` | 移动或复制到目标分组 | `/api/content/media/transfer` | 已验证复制生成新 ID 并清零使用次数，移动保留次数，失败事务回滚 |
| `content/media.service.ts` | 外部 URL 导入素材存储 | `/api/content/media/import-url`，`content_library_import.py` | 已验证固定请求 ID 重放、50 MiB/120 秒边界、公网连接约束、私网重定向拒绝、正常项目 owner 和来源哈希 |
| `content/material.controller.ts` | 创建、详情、修改、分页筛选、单条删除、ID 批量删除、条件删除、使用次数阈值删除 | `/api/content/material/records` 与对应批量及筛选入口 | 离线已验证。单条删除的 `auto_delete_media` 清理同画像关联媒体记录，源文件保留 |
| `content/material.controller.ts` | 移动、复制、按平台挑选最优草稿 | `/api/content/material/transfer`、`optimal` | 已验证平台限制、视频平台类型、最大使用次数与最低使用次数选择 |

验证位于 `tests/test_content_library.py`、`tests/test_content_library_import.py`。删除默认分组、跨画像读取和批量混入他人记录均有失败用例。

## 上传、媒体和参考分析

| 来源能力 | Easel 落点 | 核验状态与边界 |
| --- | --- | --- |
| `assets/assets.module.ts` 引入的上传签名、公开上传、确认和回调 | `upload_sessions*.py`，`/api/asset-uploads/sessions`、`content`、`confirm`、`public/.../callback` | 本机签名会话绑定画像、会话、文件名、大小、哈希与到期时间。公开能力由已授权调用者签发 |
| `libs/assets/src/http/assets-http.controller-base.ts` 的两阶段上传 | 已有附件存储回调与新增会话状态 | 流式写入、大小和类型限制、校验失败重试、确认幂等、已死接收进程回收、活跃接收者保护和旧 attempt fencing 已有离线用例 |
| 视频缩略图 | 既有 `/api/media/poster/{path}` 与媒体服务 | 复用当前能力。云对象存储回调由本地会话确认承担对应完成语义 |
| 素材 URL 下载 | `remote_media.py`、`_public_network.py` | 实际 socket 连接固定到本次验证的公网地址。重定向重新验证，环境代理不参与该字节下载边界 |
| 视频参考下载与分析 | `ref_analysis.py`、`remote_media_proxy.py`、`reference_output.py` | yt-dlp 原生有限协议下载经过受限公网代理，外部下载器回退被关闭。真实离线 HLS 私网分片探针未触达受害端点 |
| Hypit 派生产物归档 | `hypit_import.py`、`video_artifact_boundary.py` | 每次运行生成正常 owner 项目，核对来源运行目录、文件声明和哈希，同次重放保持相同产物引用 |

验证位于 `tests/test_upload_sessions.py`、`tests/test_remote_media.py`、`tests/test_remote_media_proxy.py`、`tests/test_ref_analysis_security.py`、`tests/test_ref_analysis_semantic.py` 与 Hypit 集成测试。

## 用户、调用方凭证和短链接

| 来源 | 操作 | 当前落点 | 核验状态 |
| --- | --- | --- | --- |
| `user/user.controller.ts` | 当前用户、名字/头像、位置、语言、创作者/商家身份 | `/api/local-user`，`local_user*.py` | 离线 HTTP 已验证画像隔离、字段验证和写守卫 |
| `user/user.controller.ts` | image/edit/video/agent 全量及单项 AI 模型和选项设置 | `/api/local-user/ai`、`ai/{kind}` | 全量字段与单项模型校验、单项更新保留其他配置已验证 |
| `api-key/api-key.controller.ts` | 创建、列表、撤销 | `/api/access-keys`，`local_access*.py` | 秘钥只返回一次、哈希存储、画像、读写权限、最近使用和撤销已有离线用例 |
| `short-link/short-link.service.ts`、`controller.ts` | 创建和到期存储、代码跳转 | `/api/short-links`、`/s/{code}` | 本地持久存储和授权写入已验证，另提供同画像列表及撤销 |

验证位于 `tests/test_local_user.py` 与 `tests/test_local_access.py`。

## 账号与互动

| 来源操作 | 当前落点 | 状态 |
| --- | --- | --- |
| 账号列表、详情、单条删除、批量删除、授权状态 | `channels/router.py`、`store.py`，`/api/channels/{platform}/accounts` | 已有离线 HTTP 和加密作用域用例 |
| 平台授权开始、GET/POST 回调、刷新、撤销、二级账号选择、授权 session 查询 | `channels/oauth.py`、`router.py`、`native_oauth.py`、`reads.py` | 单次 state、拒绝或失败 session、POST 表单回调、同画像查询、并发刷新和平台适用的撤销已通过离线验证。原生扫码支持双 ticket 账号与权限一致性核验 |
| 平台动态发布选项查询与创建 | `channels/auxiliary_router.py`、`options.py`、`native_options.py` 和 Board 等操作 | 源码注册选项、分类合法性、画板完整分页与创建、地点查询、字幕与播放列表入口已实现。字段和媒体限制通过 manifest 提供，逐平台边界见平台核对文档 |
| 账号分组列表、创建、批量删除、修改及组内排序 | `channels/groups.py`、`group_router.py` | default 保护、归组、删除迁回、全组验证和原子排序已验证 |
| 账号与作品分析 | `channels/analytics.py`、`router.py`、`analytics_snapshots.py` | 当前统计与 Graph/Pinterest 返回的每个历史时间点分别持久化，保留账号、作品、时区、来源哈希与缺失值。新增实际 HTTP 回归验证多日序列、作品身份、跨画像隔离、日期参数转发和错误日期在外部读取前拒绝。累计值接口按源能力提供当前快照 |
| 评论列表、发表评论/回复、平台互动函数 | `channels/actions.py`、`engagement.py`、`mutation_router.py` | 平台适用的 cursor、limit、前后翻页参数已实现；回复前逐页读取目标作品评论并核验 parent/thread。离线回归验证前后游标及异作品父评论被拒绝，写操作需要当前画像账号和人工审核 |
| 已有浏览器平台当前账号身份 | `browser_identity.py`，各 whoami/stats 命令 | 读取当前认证身份。CSDN 和掘金的配置公开主页不作为登录身份依据。离线身份与既有登录回归已通过，真实页面仍待验证 |

对应源码为 `channels/accounts/accounts.controller.ts`、`account-groups.controller.ts` 与 `channels/engagement/engagement.controller.ts`。验证位于 `test_channels_oauth.py`、`test_channels_routes.py`、`test_channels_contracts.py`、`test_channels_parity.py`、`test_channels_durability.py`、`test_channel_analytics_history.py`、`test_browser_identity.py` 和已有登录/数据脚本回归测试。

## 发布流程、任务、记录和回写

| 来源操作 | 当前落点 | 状态 |
| --- | --- | --- |
| 创建多平台 flow、公共内容、平台覆盖、定时、外部上下文和聚合详情 | `/api/publish-flows`，`publish_tasks.py`、`publish_task_router.py` | 持久化、flow ID 重放、任务认领、上下文传递和崩溃恢复已有离线用例 |
| 立即发布、重试、取消、修改计划时间 | `/api/publish-flows/tasks/{id}/{action}`、`publish_dispatch.py` | 状态约束、重新审核和未知结果保护已验证。冻结计划支持删除源文件后在同 run 上复审轮换凭证，保留原 due，恢复同一失败任务后仅发布一次。正常任务页展示账号、原时间及冻结媒体审核入口 |
| 更新已发布作品 | `/api/publish-flows/tasks/{id}/update` 的预检、提交及结果查询；平台作品管理入口 | 来源任务与服务器保存的作品绑定，当前远端版本、审核摘要、凭证、封面与请求 key 受校验。实际适配器离线用例验证原作品更新、并发版本拒绝、回写、断点与封面失败保留正文更新 |
| 历史、排队、已发布、详情、账号/平台/状态/时间筛选 | `/api/publish-records`、`queued`、`published`，`publish_records*.py` | 实际 FastAPI 应用验证画像隔离、已发布列表与终态记录视图，筛选参数由服务端校验 |
| 公开记录、删除终态本地记录 | `/api/publish-records/public/{id}`、`DELETE /{id}` | 实际 HTTP 验证公开字段收缩、待回写时拒绝删除、完成回写后隐藏和保留幂等 tombstone |
| 作品链接 pending/error/ready 和派生作品 ID | `/api/publish-records/{id}/work-link` | ready 经过服务器回读，独立 HTTP 回归验证错误标记在详情和历史列表中持久可见，跨画像修改被拒绝 |
| 用户操作信息、App scheme 和短链接 | `/api/publish-records/{id}/user-action`、`_channel_handoff.py`、`native_handoff.py` | 官方 receipt 派生用户操作信息和到期时间，客户端只展示验证过的应用地址。抖音 client token/open ticket/share-id/scheme 与实际结果查询有离线契约，确认后才回写已发布；小红书 SDK share-config 保持应用接续状态。模板支持签名访问、冻结媒体、撤销与真实二维码 |
| 每日发布统计和总览 | `/api/publish-records/summary` | 独立 HTTP 回归验证按实际 published_at 过滤，按中国时区归入发布日，创建日和统计日可不同 |
| 已确认发布后增加来源草稿/媒体使用次数 | `publish_context.py`、`publish_projection.py`、`ContentLibrary.increment_usage` | 已通过实际适配器 HTTP、跨画像拒绝、未发布零计数、回写确认失败重放只增一次和 flow 上下文传递测试 |
| 发布账本、租约、fencing、未知结果、恢复及副作用去重 | `channel_publishing.py`、`publish_ledger.py`、`publish_recovery.py`、`publish_projection.py` | 已有真实子进程退出、实际适配器 MockTransport、并发、陈旧 worker、回读和备份恢复证据 |

来源为 `channels/publish/flows/`、`tasks/`、`records/` 各 controller，以及 `core/publish-record/publish-record.service.ts`。

验证位于 `tests/test_publish_tasks.py`、`test_channel_publishing.py`、`test_channel_reconcile.py`、`test_publish_usage_context.py`、`test_publish_actor.py`、`test_publish_ledger.py`、`test_publish_recovery.py`、`test_migration_app_integration.py`、`test_migration_record_views.py` 和投影测试。独立 review 的三项发现及闭环证据保存在 `.omo/evidence/aitoearn-channel-ledger-integration-review.md`。

## 前端入口

侧栏的「平台工作台」复用当前项目的样式与画像选择，授权账号切换会重建工作区并取消旧请求。

| 工作区 | 可操作内容 | 核验状态 |
| --- | --- | --- |
| 平台账号 | 真实应用配置状态、OAuth 连接、抖音小程序二维码授权与会话查询、微信视频号 Cookie 验证绑定、刷新、频道或企业身份选择、断开连接 | TypeScript 构建与 lint 通过，真实平台授权待开发者配置 |
| 发布 | 当前画像的媒体、草稿关联、封面、平台条件、签名预检、人工审核、即时或定时发布、状态刷新、完成确认、原始媒体重新审核和作品核验 | 高级选项以 JSON 编辑，字段及媒体限制来自服务端能力声明。页面交互由实际 Ego QA 记录 |
| 作品数据与互动 | 资料、作品读取、详情、支持平台的更新或删除、指标、评论或回复、删除评论与平台互动 | 按服务端注册能力显示入口，平台资源包括播放列表、字幕、画板、地点 |
| 内容 | 独立媒体与草稿分组、创建与编辑、分页搜索、批量删除、移动或复制、最优素材、幂等使用登记、签名上传和 URL 导入 | 服务端离线契约通过，页面操作由实际 Ego QA 记录 |
| 任务记录 | 画像作用域流程分页、计划详情、重新预检、立即执行、重试、改期、取消、发布记录、链接核验与每日统计 | 任务重新审核读取同画像的原始意图，签名和幂等字段由服务器验证 |
| 设置 | 本地用户与 AI 偏好、调用方密钥、短链接、平台账号分组及排序 | 输入使用结构化表单，密钥明文仅在创建结果中显示 |
| 原生应用分享 | 抖音与小红书限时模板、画像归属、审核媒体、链接和二维码、详情与撤销 | 创建入口使用归属媒体和稳定请求 key，服务端离线验证及实际页面结果随原生协议审计闭环 |
| 公开分享页面 | 根路径 handoff/token 查询入口、图片或视频预览、复制文案、逐项下载、明确审核、抖音 App 接续及平台结果轮询、小红书官方 SDK 加载与调用 | 访客入口渲染独立组件，SDK 参数和连接范围有运行时单元回归，真实 App 与应用权限待联调 |

前端代码位于 `web/frontend/src/components/ChannelsPage.tsx`、`ChannelHandoffGuest.tsx`、`components/channels/`、`ChannelSettings.tsx`、`ChannelProfileSettings.tsx`、`ChannelAccountGroups.tsx` 和对应 API 封装。离线工作树验证通过构建、定向 oxlint 与 34 个文件的 draft guard。前端十项运行时单元回归覆盖平台数据封装、分页、画像/账号、分享授权、SDK 参数和加载失败。Ego 已验证正常发布、作品、记录、原生分享与撤销路径。六个工作区在 390/1440 px、明暗两种主题下重采 24 组截图，边界检测均无横向越界；手机画像切换验证旧账号数据移除。证据位于 `.omo/evidence/aitoearn-final-manual-qa.md`、`workbench-layout-matrix.json` 与对应截图。构建保留主包体积超过 500 kB 的提示。

小红书公开分享页按官方参数调用 `xhs.share`，媒体通过限时地址传入，标题和正文提供复制入口。官方当前说明分享 SDK 的文字内容由用户在应用中填写。真实应用权限、已审核域名和发布结果仍需账号联调。[小红书官方 JS SDK 说明](https://agora.xiaohongshu.com/doc/js)

## 最终核验与后续联调

平台适配器的源选项、媒体限制、分页、字幕列表、alt-text、失败补偿、照片读回、OAuth POST 回调、失败 session、历史序列、回复关系和 webhook 派发均已有实现及离线验证。各平台未注册操作、人工 handoff 与源 stub 的能力边界记录在平台核对文档中。

浏览器执行前重新读取当前账号。内置发布脚本在最终提交、二次确认与短信等待后重新核验认证账号及当前发布策略。通用步骤在人工停顿后执行同一守卫，异常会中止提交。无主服务注入账号时保留独立 CLI 的调用方式。外部 biliup 进程使用启动前校验。对应回归位于 `tests/test_publish_live_policy.py`、`test_publish_actor.py`、`test_browser_identity.py`，最终门审已独立复跑。

后续联调事项如下。

1. 用户提供开发者应用、平台审批、测试账号和域名后执行真实 OAuth、二维码/应用接续、媒体上传与平台端发布联调。
2. 长时间运行、真实平台配额与限流、真实媒体编码和各浏览器/应用版本的行为，通过后续持续运行验证。

本清单记录实际代码、离线回归及页面证据。真实平台验证按用户确认的凭据条件执行。

## 早期验证记录（仅适用于各自历史快照）

### 双接入增量

2026-11-14 增加浏览器账号复用与应用授权一键配置。平台工作台将原有 9 个浏览器平台和 15 个官方适配器合并为 18 个平台选项，同平台共用选择入口。已有扫码登录默认进入浏览器工作区，可独立发布和查看浏览器支持的数据，应用授权入口继续保留。应用授权通过已有工具型 Agent 对话调用 Ego，用户完成登录或审批后继续同一任务，也支持手动填写已有配置。

新增配置管理接口采用管理访问保护、字段白名单、密钥隐藏回读和串行原子保存。任务进度按画像隔离，完成状态回读实际配置与凭据。浏览器草稿按画像及平台隔离，旧发布页草稿兼容。

增量全量回归为 1809 项通过、1 项可选本机 Ego 集成测试跳过，前端全部 121 项通过。构建及 lint 通过。页面和配置链路使用真实 Ego、本地控制台及替代外部模型的 QA harness，OAuth 使用离线 HTTP 契约。真实开发者平台的登录、审批和发布仍待联调。证据位于 `.omo/evidence/platform-connections-*`。

### 迁移基线验证

主代理完成迁移基线全量回归，1795项通过，1项显式本机Ego会话集成测试按默认配置跳过。前端运行时10项通过，生产构建和全项目oxlint通过。构建主包约725kB，保留Vite体积提示。

最终门审结论为APPROVE。24组工作台截图通过明暗主题及390/1440宽度检查，手机画像切换隔离已验证。服务已用最终代码重启，首页、15平台能力清单和当前前端资源均返回HTTP200。

日志与证据位于 `.omo/evidence/aitoearn-final-pytest.txt`、`aitoearn-final-build.txt`、`aitoearn-final-lint.txt`、`aitoearn-final-frontend-tests.txt`、`aitoearn-final-gate.md`、`aitoearn-final-manual-qa.md`、`workbench-layout-matrix.json` 和 `final-service-probe.json`。
