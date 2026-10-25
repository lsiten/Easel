# Easel 项目约定

## 浏览器操作

- 需要登录态、页面交互、JS 渲染、截图或网页发布时，先检查本机是否安装 Ego（`ego-browser` 命令或 Ego 应用）；已安装则优先使用 `ego-browser`，未安装时使用 Chrome。
- 使用 Ego 前先读取 `ego-browser` Skill，并运行 `ego-browser skill`，按当前版本的 task space、控制权交接、验证和清理规范执行。
- Ego 应用已安装但命令不在 PATH 时，先检查用户本地命令目录和初始化状态；连接或启动失败应明确报告，不能直接认定未安装。用户明确指定浏览器时按用户要求执行。
- 使用 Chrome 时复用当前执行环境提供的 Chrome 工具；公开只读页面可以直接 HTTP 抓取。
- 此约定用于 Agent 直接操作网页；现有账号登录、发布和采集脚本的 Playwright 实现需单独适配，不能仅替换可执行路径就声称已接入 Ego。
