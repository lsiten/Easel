---
name: easel-upstream-sync
description: 同步 ZJU-REAL/Easel 上游到 lsiten/Easel，逐项比较并融合双方有效改进，处理 OpenFilm 子模块依赖，验证后按授权提交和推送。用户要求更新 Easel 上游、合并官方更新或解决 fork 分歧时使用。
---

# Easel 上游同步

目标是吸收上游修复和能力，同时保留 fork 中仍然有价值的功能。不能只以“无 Git 冲突”判断同步成功，也不能把保留双方理解为重复实现或保留已被修复的缺陷。

## 仓库边界

| 用途 | 仓库 / 分支 |
| --- | --- |
| 官方上游 | `https://github.com/ZJU-REAL/Easel.git`，`upstream/main` |
| 推送目标 | `https://github.com/lsiten/Easel.git`，`lsiten/main` |
| OpenFilm 子模块 | `subprojects/openfilm`，`https://github.com/lsiten/openfilm.git` |

`origin` 是历史上游别名，不依靠它判断推送目标。执行前用 `git remote -v` 核实地址；缺少 `upstream` / `lsiten` 时才添加上述远端，现有地址不符时先查明原因，不静默覆盖。
在本仓库根执行；代码定位遵循根 `AGENTS.md`。维护 Skill 位于 `.agents/skills/`，不同于面向内容创作的 `skills/builtin/`。

## 先建立比较基线

1. 读取 `git status --short --branch`、暂存与未暂存差异、`git submodule status`、当前分支和跟踪分支。先检查子模块自身的工作区；父仓库干净不代表子模块干净。
2. 脏工作区先判断是否属于本次任务。已授权提交时，先审阅并提交相关改动；否则保持原样并说明阻塞。不得自动 stash、覆盖文件或丢弃用户改动。
3. `git fetch upstream` 和 `git fetch lsiten`；读取双方新增提交、版本要求、迁移说明与 CI。若 fork 远端领先本地，也必须纳入比较，不能覆盖远端。
4. 找到 `git merge-base HEAD upstream/main`，分别阅读共同祖先到本地、到上游的差异。浅克隆找不到共同祖先时，先对相关远端逐步 deepen，必要时 unshallow；不得用 `--allow-unrelated-histories` 绕过。
5. 记录起始 HEAD、上游 SHA、fork 远端 SHA；在改变已提交历史前创建不重名的 `codex/pre-sync-时间戳` 本地备份分支。默认在当前工作分支合并，不切换、重置或重写共享主分支。

## 逐项融合双方改进

先列出每个重叠模块的上游改动、本地改动、采用方式及验证方法，再以普通 merge 保留历史。fork 远端领先时，先整合其提交，再处理官方上游。真正分叉的合并使用 `git merge --no-commit --no-ff <已核实的引用>`，审查暂存结果后再提交；已包含则跳过。

- 上游单独的修复通常吸收；本地独有能力保留；同一目标的两个实现选更可靠的实现并补齐另一方独有能力，不机械叠加。
- 冲突必须阅读共同祖先、本地、上游三个版本及调用者、测试。禁止整文件 `ours` / `theirs`、全局偏向某一侧或按“更新日期”决定取舍。
- 无文本冲突也要检查语义：接口变化、环境变量、依赖和锁文件、安装脚本、默认行为、路径、文档与测试是否仍匹配。
- 以下是 fork 当前的重要能力，需先确认其实现位置并验证；上游有等价或更好实现时可以融合替换，但必须保留用户可观察的能力：
  - Ego 平台浏览器选择、独立账号目录、登录标记、失效恢复与 Chrome 回退边界。
  - 每画像素材登记、内容理解整理、存储隔离、任务恢复，以及内容库的账号范围。
  - OpenFilm Studio 与消息内播放预览、按会话绑定项目、结构化引用、无项目时不展示其他会话的视频。
  - 系统生图配置优先、缺配置时使用底座真实工具，以及图文预览、禁 emoji、发布前检查。
  - 执行底座、会话流式响应与附件的现有兼容性。
- 测试因行为有意变化而更新时，检查原断言保护的需求是否仍被覆盖；不得通过删断言、全局跳过或篡改期待值掩盖退化。

## OpenFilm 必须先于父仓库落地

上游改变 gitlink 或需要 OpenFilm 新接口时，先读取子项目的 [openfilm-upstream-sync](../../../subprojects/openfilm/.agents/skills/openfilm-upstream-sync/SKILL.md)。子模块是独立仓库，不能把父仓库提交误认为已保存子项目文件。

1. 比较父仓库双方 gitlink 指向的 OpenFilm SHA 与本地 OpenFilm HEAD；必要时取回上游指向的 SHA，逐项整合本地播放器、Studio、atelier 与官方更新。
2. 保持 `.gitmodules` 指向 `lsiten/openfilm`。不要运行 `git submodule update --remote` 用官方 HEAD 覆盖本地集成结果。
3. 在已授权推送时先推 OpenFilm，并用 `git ls-remote` 确认子模块 SHA 已在 fork 的目标分支可取回，再提交父仓库的 gitlink。仅同步本地时记录子模块尚未推送，不宣称父仓库可远程复现。

## 验证与交付

根据本次改动选择检查，并先阅读当前 CI / package scripts，命令随项目演进调整：

```bash
.venv/bin/python scripts/validate_skills.py
.venv/bin/python scripts/validate_skill_commands.py
.venv/bin/python scripts/validate_document_paths.py
.venv/bin/python -m pytest -q
node --test tests/frontend/*.test.mjs
npm --prefix web/frontend run lint
npm --prefix web/frontend run build
```

必要时用已有环境中的 Python 代替 `.venv/bin/python`。对受影响的真实界面，按仓库浏览器规范验证。先记录同步前已存在的失败，合并后区分原有失败与新增退化，不能宣称未跑或失败的检查通过。

提交前检查冲突标记、`git diff --check`、完整暂存差异、子模块状态与敏感信息。报告“上游采用 / 本地保留 / 融合替代”的实际决策及测试结果；若某项需求无法兼容，说明具体取舍并等待必要的用户选择。

用户已授权推送时直接推送：先复查 `lsiten` 地址、fetch 确认没有新增分歧，再显式 `git push lsiten HEAD:main`（用户指定其他分支则使用其分支）。普通 push 被拒绝时重新获取并融合新提交；不得 force push。尚有未解决冲突或同步引入的检查失败时，不推送集成结果。只要求比较或本地同步时，不推送、不创建 PR。

最后核实远端 SHA、父子仓库 HEAD、跟踪关系与剩余未提交文件；提交摘要附上最终 SHA 和原有检查失败，便于下一次同步追溯。
