# 原生平台独立审查

审查日期为 2026-11-14。覆盖 `native_handoff.py`、`native_handoff_router.py`、`native_handoff_validation.py`、`rednote_share.py`、`channel_monitor.py`、`publish_dispatch.py`，以及 `web/app.py` 的公开回调守卫、后台启动、原生通知 resolver 与 finalize 分发。源契约对照 AiToEarn `4b59b10` 注册 provider、controller、schema 和 service。

## 发现与修复闭环

| 级别 | 发现 | 修复与证据 |
|---|---|---|
| P1 | 原生 action 过期后，monitor 停止查询已经提交的 share job。平台延迟完成或过期后回调会一直停在 SUBMITTED | 移除 monitor 对 action.expires_at 的停止条件。`test_submitted_native_job_is_verified_after_action_expiry` 使用真实 NativeHandoff、真实 monitor_one 与 MockTransport，推进两小时后官方查询完成，断言只生成一次 share，同时公开模板仍拒绝过期访问 |
| P1 | 未校验的 provider video_id 布尔值可被 root poll_owned 识别为整数并错误记录最终作品 | DouyinOpen 的真实查询响应边界拒绝 bool、object、list 身份。`test_native_share_query_rejects_non_identity_completion` 三组畸形响应返回 typed ChannelError |
| P2 | 公开 template token 使用字符串 compare_digest，非 ASCII 查询参数触发 TypeError | 改为字节比较。`test_public_native_grant_rejects_non_ascii_token_as_typed_error` 断言 typed template_not_found。原生通知与原九平台签名比较同样使用字节，非 ASCII 签名回归已通过 |
| 源契约 | 原生标题、媒体、话题与 Unicode UTF-16 插入位置有不完整边界 | 模板验证复用媒体规则，执行数量、标题、正文、总长度与五个话题限制。原生 adapter 校验 Bili 分区和去重话题、Kwai 四话题及 UTF-16 长度。实际 signed scheme 测试包含 emoji、重复中文话题与正确插入偏移 |

## 作用域与实际结果证据

- 公开 start 必须持 owner 签发、绑定模板和审核摘要的授权，提供 reviewed=true。模板保存 owned manifest 并冻结文件。公开资料排除 persona、context、request_key 和内部 manifest。撤销与过期后媒体访问被拒绝。
- 原生通知先验证平台签名，再保存加密、去重、应用级 inbox。resolver 仅匹配服务器 ledger 的 receipt、最终作品和 share/resource ID，并要求 app_client_id 等于当前配置。正常账号还需 callback owner 一致。延迟检查点、错应用、错作品、错账号、伪装 app-handoff actor 和重复通知均有真实 root ASGI 回归。
- resolver 使用实际 ChannelProgress.model_dump 的 receipt 层级。匹配事件只建立官方读回任务，通知 status 不直接标记 PUBLISHED。测试确认精确一条 UNKNOWN run 被排入真实 video ID 读回，其他画像保持原状态。
- Douyin handoff 从服务器生成 share ID 与签名 schema。客户端只取得持久化 action。重试返回同一 action，官方查询完成后保存平台真实作品，发布者 UID 按官方返回保存，未返回时留空。应用配置变化时拒绝旧任务跨应用读回。
- Rednote bridge 的 appKey、nonce、timeStamp 排序 SHA256，与源码请求和 verifyConfig 字段一致。桥接请求和响应有超时及大小限制，SDK token 保留服务端。应用分享维持手工状态，source 仅日志处理的微信公众号 XML 通知保持签名验证和确认语义。

## 执行证据与边界

执行以下聚焦回归，共 167 项通过，用时 9.13 秒。

```sh
.venv/bin/python -m pytest -q -x tests/test_channels_*.py tests/test_channel_publishing.py tests/test_channel_reconcile.py tests/test_channel_monitor.py tests/test_native_channel_pipeline.py tests/test_native_handoff.py
```

本次审查范围内已复现的问题均完成修复和回归。开发者应用审批、真实账号、正式平台上传、SDK 唤起与真实网络发布等待凭据联调。源码未注册的账号、作品、互动或分析能力，以及无官方证明的外部作品链接，继续按能力矩阵返回不支持或人工待确认状态。
