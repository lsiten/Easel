# 官方平台适配器迁移核对

依据 AiToEarn 固定源码 `4b59b10`，核对 `project/aitoearn-backend/apps/aitoearn-server/src/core/channels/platforms/` 内各平台 module、constants、schema、provider、service 与注册 controller。Python 实现依据 MIT 服务端行为重写。浏览器原有平台 ID 与以下开放协议 ID 分开注册。

## 注册能力矩阵

表格中的实现均有代码入口。平台开发者应用、权限审核、测试账号和真实网络调用需要后续凭据联调。

| 平台 ID | 上游注册能力 | 已实现行为与入口 | 上游能力边界 |
|---|---|---|---|
| youtube | auth、publish、work、analytics、engagement、publish-options、webhook | OAuth/PKCE/刷新/撤销/频道选择，resumable 上传与 owned readback，future publishAt 轮询，分类 assignable 检查，完整可编辑 metadata 更新与 If-Match，服务端视频封面上传，作品/链接/归属，日期数据与快照，评论/回复/删除，播放列表与字幕列表，签名事件持久化 | 字幕服务按源列表方法提供。新字幕文件上传不属于注册入口 |
| tiktok | auth、publish、work、analytics、webhook | OAuth/PKCE/刷新/撤销，creator_info 时长限制，图片/视频发布，PULL_FROM_URL 与 FILE_UPLOAD，状态/取消，作品与归属，照片真实 ID 与认证创作者 canonical 链接，账号/作品数据，签名通知 | SELF_ONLY 官方完成但没有最终作品 ID 时保留实际完成证据与 work_link_status=pending。公开作品须真实 ID 与 owned readback |
| instagram | auth、publish、work、analytics、engagement、webhook | 短转长期令牌/刷新/撤销，图片/轮播/Reels/Stories 容器，child 失败补偿，封面/来源选项，作品/归属，数据与快照，评论/回复/删除，取消，签名通知 | 媒体本地元数据检查覆盖 格式、大小、时长、适用宽高比。缺失平台指标保留 null |
| facebook | auth、publish、work、analytics、engagement、webhook | 用户长期令牌，完整 Page 分页与 Page token 刷新，Feed 文字/多图/分片视频，Stories/Reels，真实上传区间与 processing readback，附加选项，作品/数据/评论/回复/赞及撤销/取消，签名通知 | 无真实成功响应和处理完成证据时保留 SUBMITTED |
| threads | auth、publish、work、analytics、engagement、publish-options | 长期令牌/刷新，图片/轮播/视频/文字自动发布，topic/位置/回复/引用选项，前后游标，作品/数据，回复/删除/隐藏/取消隐藏，取消 | 上游未提供远程 revoke。本地断开可用 |
| twitter | auth、publish、work、analytics、engagement | OAuth/PKCE/刷新/撤销，媒体分段/完成/轮询与 alt_text，Unicode 加权正文与 URL 长度，发帖/引用/投票，作品/数据，回复/赞/收藏/转发/关注及撤销/取消 | 未注册 webhook |
| linkedin | auth、publish、webhook | OAuth/刷新/撤销，身份选择，图片/视频上传与状态，文字/多图/视频发布，author 作用域/visibility/distribution/commentary，取消，签名事件 | module 未注册 work、analytics、engagement。存在未注册 engagement 源文件，相关入口405 |
| pinterest | auth、publish、work、analytics、publish-options | OAuth/刷新/撤销，完整 Board 分页与创建，图片 Pin，signed-form 视频上传/轮询/发布，完整可编辑选项更新，作品/归属，安全短链与日期数据，取消 | 无已证实条件更新协议。传入 expected_etag 返回405 |
| google-business | auth | OAuth/PKCE/刷新/撤销，Google 资料，企业账号与 locations 全量分页和 location 身份选择 | module 仅注册 auth。发布/作品/数据/互动405 |
| bilibili-open | auth、publish、work、analytics、publish-options、webhook | 原生 OAuth/刷新，HMAC-SHA256 签名 API，真实账号，100MiB 分片/完成，原生封面，分区/版权/转载来源/话题/活动选项，投稿与审核轮询，真实 BV/av 才产生 canonical，作品与数据，分类源，原生 webhook | 源 update stub 不代表可用更新。update405。作品 provider 注册链接解析，归属读回复用同模块认证 archive 服务 |
| kwai-open | auth、publish、work、analytics | 原生 OAuth/刷新，认证用户，4MiB fragment 上传，独立 ephemeral upload token，必需封面，多媒体选项，photo_id 提交/待审核/完成，认证作品列表/详情/归属/数据与安全短链 | 无远程 revoke、update、webhook 注册。源 HTTP 上传主机经公共地址校验且禁止跳转 |
| douyin-open | auth、publish、work、webhook、miniapp、offline-qr controller | OAuth 或小程序二维码，code2session 与双 ticket UID/scope 一致性，令牌加密，client token/open ticket 缓存，share-id，签名 snssdk1128 scheme，下载/私密/封面时间/短标题选项，真实 share 查询，原生签名通知，认证小程序数据服务 | publish 是应用用户操作 handoff。未获得实际账号证据时保持 requires_user_action/SUBMITTED。work provider 仅链接解析，miniapp 视频查询可在实际授权范围读回。离线分享模板/访问授权由 Easel native_handoff 提供 |
| wechat-official-open | auth、publish、webhook | SNS OAuth/刷新与实际官方 app token 校验，发布身份固定 appid:<appId>，SNS 访客记为 parent_uid，内嵌图片/素材封面/草稿/提交/状态分阶段，author/digest/评论/封面显示/来源选项，XML 签名与 challenge | 无 work、analytics、engagement 注册。公众号 app 凭据变化要求重新绑定。草稿创建和正式提交各自持久检查点 |
| wechat-channels-open | publish、work | 服务端 Cookie auth_data 核验真实 finder 账号并加密，官方 app token，链接信息/作品详情与 finder 归属，提交外部 workId/workLink 的待确认状态 | 源授权类型 plugin。专有插件资产和代码排除，MIT 后端 Cookie 核验与作品行为已重写。外部链接须共享服务验证审核内容与发布时间后才能完成 |
| rednote-link | publish、work、offline-qr controller | 无凭据 canonical/短链解析，保留 xsec_token 与 link_error 语义，外部 workLink 状态，小红书 SDK share-config/离线模板由 Easel native_handoff 和 rednote_share 提供 | 源无官方账号/作品归属 API。应用分享与外部链接保持人工待确认。专有插件资产和代码排除 |

## 通用入口

- `router.py` 提供平台清单、OAuth GET/POST 回调、失败 session、资料、账号选择/刷新/本地删除、作品/链接/归属、数据快照和动态发布选项。`native_router.py` 提供抖音二维码回调与视频号服务端 Cookie 绑定。
- `store.py` 提供一次性 state、persona/platform/account 作用域、Fernet 密文、并发 key 初始化、跨实例刷新锁与事务。小程序 homepage 辅助令牌单独保存于密文，API 序列化排除。
- `groups.py` 与 `group_router.py` 提供 default 保护、列表/创建/更新/批量删除/迁回 default/赋组/事务排序。
- `mutation_router.py` 提供已审核更新、删除、互动与 Board 创建。回复前读取并核验 parent/thread 关系，平台限制在 provider 内检查。
- 发布服务通过 Easel 共享审批、owned media manifest、冻结文件、fencing lease、ledger、服务器 receipt 与恢复流程进入适配器。客户端上传 URL 和 receipt 不构成发布依据。
- 已认证 webhook 先进入加密去重 inbox。`completion_events.py` 提供最早到期批次、确认、退避和损坏隔离。`native_webhooks.py` 持久保存仅含应用与 share/resource ID 的原生通知，`process_pending_app_events` 经服务器 ledger resolver 精确匹配后事务转入 persona/account inbox。实际完成仍由服务器官方读回验证。
- `manifest.py` 公开真实注册能力、auth_mode、绑定方式、配置就绪、标题/正文/数量限制、媒体规则、封面支持/必需、动态字段以及列表/详情差异。

## 离线验证

`test_channels_oauth.py`、`test_channels_contracts.py`、`test_channels_routes.py`、`test_channels_parity.py`、`test_channels_durability.py` 覆盖九个平台主协议、错误入口、刷新/状态并发、加密、作用域、签名通知、分页、回复关系、更新与媒体阶段。`test_channels_native.py`、`test_channels_wechat_native.py`、`test_channels_native_webhooks.py`、`test_channels_native_routes.py`、`test_channels_native_boundaries.py`、`test_channels_root_native_webhook.py`、`test_channels_native_review.py` 覆盖原生 OAuth、HMAC 字节、分片、必需封面、真实身份、草稿提交边界、二维码双授权、签名 scheme、未绑定回调的持久重试与隔离。共享发布服务另有入口/ledger/恢复/队列测试。

全部验证使用离线 MockTransport 与本地接口。开发者应用配置、平台审批、真实账号、真实媒体探测与平台端到端联调仍需凭据后执行。已声明的手工 handoff 和上游未注册操作按表内语义保留。
