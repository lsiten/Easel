---
name: video-hypit
description: "当用户明确要求用 Hypit 制作、复刻、修改或导出视频，调用 /hypit，提供 SVML/SVS/SVRun 工程，或要求继续 Hypit 工作台中的失败任务时使用。由对话 Agent 读取画像与素材、编写工程、检查计划、执行原生 CLI、跟踪 Build 并验收成片。涉及 Easel 会话隔离、Hypit Runtime 与服务配置、已有产物复用及导入。"
layer: produce
---

# Hypit 视频制作

由当前对话 Agent 完成创作与工具调用。用户给出目标、参考或修改意见后，Agent 负责准备可执行工程、运行 Hypit、诊断失败并交付经过检查的结果。用户要求解释或分析时，按其要求交付对应内容。

## 找到当前任务

- 读取当前画像的相关文件，复用对话中已定的主题、风格、画幅、素材、服务与授权。需要素材时读取 [素材库技能](../skill-asset-library/SKILL.md)，按本画像查询。
- 用户给参考视频时，读取 [参考视频分析技能](../video-reference-analyst/SKILL.md) 中的分析部分，形成参考证据、镜头与声音判断。继续使用本次选定的 Hypit 制作。
- 用户给出失败 Run 时，先读取它的记录和日志，取得 `profile_id`、`session_id`、Easel Run id 与工程位置。工程中的 Brief、Treatment、Analysis、Timeline 和已完成 Outputs 是续作依据。
- 新任务按已知目标准备工程。只有影响创作结果的关键信息确实缺失时才询问用户。`.svml`、`.svs` 和 `.svrun` 由 Agent 编写。

运行本项目脚本前，切换到运行时注入的项目根，使用项目 `.venv` 中的 Python。会话目录、工具定位、命令边界及 Web 状态的处理见 [Easel 运行约定](references/easel-runtime.md)。

## 准备工具与服务

优先复用当前项目的 `outputs/_hypit/runtime/node_modules/.bin/hypit`。读取其 `--version` 和当前操作的帮助，再选择所需的 Runtime、媒体工具与 Endpoint。

- 环境检查通过后，继续确认本次工程、Run Source 和实际需要的能力。
- 配置从当前项目及用户已选服务中取得。Hypit 的 Runtime Profile、Credential Store 与 Easel 对话模型配置各有用途，按当前调用链检查。
- 缺少能力时，先检查本机已有安装、模型与缓存，再处理具体缺项。新增服务、下载或升级按当前任务授权执行。
- 本地处理与 Provider 费用依据本次 Run 的 `plan`、`pricing` 和已授权范围判断。Easel 工作台的资源次数用于描述资源估计。

涉及工具安装或版本选择时，按需读取 [官方分发说明](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/environment/distribution.md)。配置服务与凭据时，读取 [官方服务接入](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/environment/model-and-provider.md) 和 [Runtime Profile](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/environment/profile.md)。

## 写出可执行工程

先明确用户要看的内容、保留的参考关系和本次改动，逐步写入 Brief 与 Treatment。存在参考时，Analysis 记录实际观察及判断，Timeline 记录目标片的时间与镜头安排。按任务规模维护这些信息。

- Author Source 使用 `.svml`，样式与可复用配置使用 `.svs`，Run Source 使用 `.svrun` 指定 Author、targets 及复用选择。
- 根据当前表达组织人物、画面、字幕、声音、组件和时间关系。需要口播语义驱动时使用语义锚点，确定性的动效可使用作者定义的时钟。
- 复用已有素材与成功 Outputs。修改字幕、布局或动效时，把可保留的媒体结果明确选入 Run，重新检查剩余需求。
- 项目组件、包依赖和输出名以已安装版本的 vocabulary、包 README 及 `check` 结果为准。先检查代表段，再扩大到完整作品。

首次编写或修订 Hypit 工程时，按需读取 [官方 Authoring](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/production/authoring.md)、[Run 语法](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/production/runs.md) 和 [组件表达](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/production/component-design.md)。

## 检查计划并构建

在同一工程和 Runtime 上执行 `check`、`plan`，需要 Provider 价格时执行 `pricing`。把实际新增的请求、预计用量、支付账号及不确定性说明给用户。沿用已覆盖这些操作的授权，新增付费范围取得相应授权后执行。

用原生 `hypit build <run-source> --follow` 提交工程，记录返回的 Hypit Build id、Run Source、Runtime Profile、工程路径和日志位置。构建参数与导出示例见 [Easel 运行约定](references/easel-runtime.md)。

- 观察进程结束、超时或连接断开时，用原 Build id 查询 `activity`、`status`、`builds`、`inspect`，据真实执行状态继续处理。
- 失败时先看 Build logs；Worker 启动失败时看 Runtime logs。定位和修复具体原因，再依据保留的结果准备后续 Run。
- 持续跟踪至本次交付完成，或出现需要用户提供素材、账号操作或新增授权的具体依赖。耗时期间报告实质进展。

有关重试、receipt 和复用语法，读取 [官方 Build 与 Result 流程](https://github.com/hypit-ai/hypit/blob/25454bed40bbb09eaf675e217ed9901fa8ef4f2a/skills/hypit/references/production/builds.md)。

## 验收与交付

用 `inspect` 找到实际输出名，再用 `get` 导出需要的 Outputs。检查 MP4 的时长、画幅、帧率和音轨，查看片头、主要镜头、转场和片尾，播放核对声音。按 Brief 与参考关系判断表达、字幕、构图、运动和节奏，修复影响交付的问题。

交付实际视频路径、工程位置和必要的使用说明。可用的 Hypit Studio 通过原工程启动并验证后提供链接。操作浏览器时遵守当前项目的 ego-browser 约定。

Easel 原生工程与派生产物按 [Easel 运行约定](references/easel-runtime.md) 保存。报告已完成的原生 Hypit Build、当前 Web Run 状态和仍待接入的界面能力，使用户能准确继续工作。

本技能参考官方 Hypit v0.3.2 的 Agent 工作流，来源与当前集成状态见 [EASEL-META.md](EASEL-META.md)。
