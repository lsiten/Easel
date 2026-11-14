# Easel 中的 Hypit 运行与产物

## 项目根与工具

从 Easel 运行时注入的项目根执行下列命令。`easel_root`、`hypit_project`、`run_source` 等 shell 变量取本次任务已解析的绝对路径。路径存在空格或中文时保留引号。

```bash
easel_root="$PWD"
hypit_cli="$easel_root/outputs/_hypit/runtime/node_modules/.bin/hypit"
"$hypit_cli" --version
"$hypit_cli" help build
"$hypit_cli" help get
```

目录中缺少 CLI 时，检查本项目的配置、显式可执行文件和本机已有工具。当前 Easel 适配版本固定为 `@hypit/hypit` 0.3.2。新安装或升级需同时核对包、Skill 参考和适配器的兼容性。

运行时目录承载共享依赖。视频工程按当前画像和会话定位，项目素材和工程文件写入自己的工作目录。

## 画像、会话与 Run

对话环境提供 `EASEL_ROOT`、`EASEL_PERSONA`、`EASEL_SESSION_KEY`。Web 会话的 effective key 常为 `画像安全名::原始会话ID`，而 Hypit API 的 `session_id` 使用原始会话 ID。优先取用户已选 Run 记录中的 scope；从环境恢复时，画像来自 `EASEL_PERSONA`，会话取 effective key 的末段，并核对当前对话。独立 CLI 缺少这些值时，依据当前用户请求建立并记录自己的工程身份。

已有 Easel Run 的持久记录位于 `outputs/_hypit/runs/<Easel Run id>/lifecycle.json`。以下标识分别由各自系统产生，记录清楚它们的对应关系。

| 标识 | 来源和用途 |
| --- | --- |
| Easel Run id | 生命周期记录的 `id`，用于 Web 查询及会话工程目录 |
| Easel 记录的 `run_id` | Easel 生命周期内部标识 |
| Hypit Build id | 原生 `build` 返回，用于 Hypit `status`、`logs`、`inspect` 和 `get` |

复用 `easel.video_run_manifest.resolve_project_root(workspace, profile_id, session_id, easel_run_id)` 解析隔离工程。其目录形如 `profiles/<画像>/sessions/<原始会话ID>/video/<Easel Run id>/`。新建 Agent 工程时可生成自己的 run id，使用相同 resolver，并在交付中记录。

工具与源码定位参考本次任务记录。读取其它项目素材时，依据用户提供的材料或素材库归属判断复用关系。

## 工程与原生命令

项目根用 `package.json` 声明 Hypit 工程；已有文件时合并所需字段。

```json
{
  "name": "easel-hypit-production",
  "private": true,
  "type": "module",
  "hypit": { "project": true }
}
```

Agent 在工程内编写 `main.svml`、所需的 `.svs` 和 `build.svrun`。Run 的一个典型结构如下，`final.video` 应替换为当前 Author 实际公开的 Logical Output。

```xml
<?svml using="@hypit/markup/run@1"?>
<svrun version="1">
  <author source="./main.svml"/>
  <target output="final.video"/>
</svrun>
```

固定 `hypit_project` 和 `run_source` 为本次工程及 Run 的绝对路径，再执行以下流程。共享素材需要额外授权路径时，按 `help` 用法向相关命令传入 `--asset-root`。其它位置安装的项目包通过 `--package-root` 指定。命令行相对路径从 shell 当前目录解析。

```bash
"$hypit_cli" paths --project "$hypit_project"
"$hypit_cli" check "$run_source" --project "$hypit_project" --json
```

先检查工程已有的 Runtime Profile 和 `paths` 输出。工程尚未选定 Runtime 时，`runtime init` 会创建该工程的初始 Profile 并选择它；继续根据实际需求配置 Endpoint、工具与凭据。初始化后的 Profile 使用 `hypit.runtime.json`，已有 Profile 沿用其真实路径。

```bash
"$hypit_cli" runtime init --project "$hypit_project" --json
```

准备好 `runtime_profile` 后，对同一个 Run 计划和构建。

```bash
"$hypit_cli" plan "$run_source" --project "$hypit_project" --runtime "$runtime_profile" --json
"$hypit_cli" pricing "$run_source" --project "$hypit_project" --runtime "$runtime_profile" --json
"$hypit_cli" build "$run_source" --project "$hypit_project" --runtime "$runtime_profile" --follow --json
```

`pricing` 适用于需要查询所选 Provider 当前价格的情况。`build` 的 stdout 携带结果 JSON，stderr 可包含进度与诊断。命令退出码、JSON 中的工作状态和实际 Results 一起用于判断执行结果。

## 跟踪、恢复与导出

使用本次返回的 `hypit_build_id`，在原工程及 Runtime 查询。

```bash
"$hypit_cli" status "$hypit_build_id" --project "$hypit_project" --runtime "$runtime_profile" --json
"$hypit_cli" logs "$hypit_build_id" --project "$hypit_project" --runtime "$runtime_profile" --lines 80
"$hypit_cli" inspect "$hypit_build_id" --project "$hypit_project" --json
```

Result 的 `outcome`、`outputs`、`outputCount` 与遗漏数量决定后续检查范围。导出时使用实际输出名，输出目录应先建立。

```bash
"$hypit_cli" get "$hypit_build_id" --project "$hypit_project" --output "$output_name" --to "$video_path" --json
ffprobe -v error -show_entries format=duration:stream=codec_type,width,height,r_frame_rate -of json "$video_path"
```

需要停止实际执行时使用 `cancel`，具体参数以当前 CLI help 为准。恢复失败任务时读取其 Result、日志与 receipt，把可用输出通过新 Run 的复用选择带入下一次 Build。已发生的付费请求及其远端结果要纳入重试判断。

## Easel 页面与产物归档

当前 `skills/shared/scripts/hypit.py readiness` 可以检查环境。现有 `run` 封装仍向原生 CLI 传入顶层 `--project` 及 `--output-root`、`--report-root`，会触发 `Unknown command "--project"`。本技能通过上述原生命令执行，让 Agent 负责准备 Run Source、选择 Runtime 和导出 Result。复查本段时应核对适配器当前实现。

原生 `build` 的参数从 `build <run-source>` 开始。导出由 `get --to` 完成，报告可来自保存的 `inspect --json` 结果。

原生 Result 保存真实执行记录。当前 Web Run 还依赖 `HypitLifecycle` 的任务接入，Agent 独立完成的原生 Build 应在交付中说明其状态。生命周期通过现有服务接口更新；保留原有失败记录和错误证据。

工程的原生 `.svml`、`.svs`、`.svrun`、`.hypit`、依赖与凭据存储保留在该工程边界内。对已声明在成功 Easel Run 中的派生文件，使用 `/api/hypit/runs/{run_id}/artifacts/import`，携带原来的 `profile_id`、`session_id` 和明确的 `artifact` 文件名。

Agent 独立制作的派生文件可使用 `easel.video_artifact_boundary.import_artifacts` 归档。传入本工程 `source_root`、人类可读主题的 `outputs/<主题>/` 作为 `destination_root`、`source_engine="hypit"`、当前 `target_profile` 和列明文件、kind、SHA-256、attribution 的 manifest。该函数返回引擎归属及目标相对路径，后续登记和交付使用真实返回值。

跨层制作及内容库展示沿用项目 `manifest.py` 的 `record`、`meta` 约定，记录画像、主题、交付路径和完成状态。需要预览时执行 `hypit studio --help`，按当前版本的 `studio --run` 用法启动本工程，验证可访问后提供地址。Easel 嵌入式 Studio 的 URL、origin 与 license 配置按现有 `easel/hypit_studio.py` 处理。
