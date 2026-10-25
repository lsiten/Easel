# 注意：本文件为 UTF-8 with BOM。Windows PowerShell 5.1 需 BOM 才能正确解析中文字符串（否则报「语法错误」）；请勿移除。
param(
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$env:PYTHONUTF8 = '1'

function Info($Message) { Write-Host "[easel] $Message" -ForegroundColor Cyan }
function Ok($Message) { Write-Host "  [OK] $Message" -ForegroundColor Green }
function Warn($Message) { Write-Host "  [!] $Message" -ForegroundColor Yellow }
# 用 [OK] / [!] 这种 ASCII 标记而不是 ✓ / ⚠：cmd.exe 与较老的终端代码页并不可靠地
# 支持 UTF-8，而本文件原有的 Ok() 已经是 [OK] 风格，不引入第二套符号。
function Step($Index, $Title, $Subtitle) {
    Write-Host ''
    Write-Host ('-' * 52) -ForegroundColor DarkBlue
    Write-Host "  [$Index] " -NoNewline -ForegroundColor Magenta
    Write-Host $Title -ForegroundColor Cyan
    Write-Host "  $Subtitle" -ForegroundColor DarkGray
}

# 长耗时步骤转圈。注意三点：
#   1. 用 Start-Process -PassThru 轮询 HasExited，不要用 Start-Job —— job 跑在全新
#      runspace 里，拿不到 $Root / $Python / Install-RequiredCommand 改过的 PATH。
#   2. 非 TTY（CI、重定向）下不要转圈：1Hz 的 `r 会刷出几千行无用日志。
#   3. 失败时把日志尾部打出来，否则用户只看到一个 [FAIL] 不知道发生了什么。
function Invoke-WithProgress([string]$Label, [string]$Exe, [string[]]$Arguments, [string]$WorkDir = $null) {
    $log = Join-Path ([System.IO.Path]::GetTempPath()) "easel-install-$(Get-Random).log"
    $startArgs = @{ FilePath = $Exe; ArgumentList = $Arguments; PassThru = $true;
                    NoNewWindow = $true; RedirectStandardOutput = $log;
                    RedirectStandardError = "$log.err" }
    if ($WorkDir) { $startArgs['WorkingDirectory'] = $WorkDir }
    $proc = Start-Process @startArgs
    $quiet = [Console]::IsOutputRedirected
    if ($quiet) { Write-Host "  [....] $Label 开始（非交互终端，不显示进度）" }
    $frames = @('[>   ]', '[=>  ]', '[==> ]', '[===>]')
    $t0 = Get-Date
    $i = 0
    while (-not $proc.HasExited) {
        if (-not $quiet) {
            $el = [int]((Get-Date) - $t0).TotalSeconds
            Write-Host ("`r  {0} 进行中 {1} 已运行 {2}s" -f $frames[$i % 4], $Label, $el) -NoNewline
            $i++
        }
        Start-Sleep -Seconds 1
    }
    if (-not $quiet) { Write-Host "`r$(' ' * 60)`r" -NoNewline }
    if ($proc.ExitCode -eq 0) {
        Ok "$Label 完成"
        Remove-Item $log, "$log.err" -Force -ErrorAction SilentlyContinue
        return $true
    }
    Write-Host "  [FAIL] $Label 失败" -ForegroundColor Red
    foreach ($f in @($log, "$log.err")) {
        if (Test-Path $f) { Get-Content $f -Tail 40 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ } }
    }
    Remove-Item $log, "$log.err" -Force -ErrorAction SilentlyContinue
    return $false
}

# 结尾汇总。变宽内容不套框线：中文是双宽字符，按字符数 padding 的 ASCII 框一定错位。
function Show-Summary {
    $n = $script:Warnings.Count
    Write-Host ''
    if ($n -eq 0) { Write-Host '  [OK] Easel 安装完成' -ForegroundColor Green }
    else { Write-Host "  [OK] Easel 安装完成（有 $n 项降级）" -ForegroundColor Yellow }
    if ($n -gt 0) {
        Write-Host ''
        Write-Host "  需要处理（$n）：" -ForegroundColor Yellow
        foreach ($sev in @('high', 'low')) {
            foreach ($w in $script:Warnings) {
                if ($w.Severity -ne $sev) { continue }
                Write-Host "   [!] $($w.Label) - $($w.Detail)" -ForegroundColor Yellow
                if ($w.Fix) { Write-Host "       -> $($w.Fix)" -ForegroundColor DarkGray }
            }
        }
        Write-Host ''
        Write-Host '  以上均不影响已装好的部分；逐项修完可用 easel doctor 复检。' -ForegroundColor DarkGray
        Write-Host '  若希望这些问题直接让安装失败（CI/自动化场景）：$env:EASEL_SETUP_STRICT=1' -ForegroundColor DarkGray
    }
}

function Fail($Message) {
    if ($script:Warnings) { Show-Summary }
    Write-Host ''
    Write-Host "  [X] 安装中止：$Message" -ForegroundColor Red
    exit 1
}
function Assert-Command($Name, $Hint) { if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { Fail "$Name 未找到。$Hint" } }
function Install-RequiredCommand($Name, $PackageId, $Hint) {
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "$Name 未找到。$Hint`n也可以先安装 Windows App Installer（winget）后重试。" }
    Info "未找到 $Name，使用 winget 安装 $PackageId..."
    & winget install --id $PackageId --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Fail "$Name 自动安装失败。$Hint" }
    $env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    Assert-Command $Name $Hint
}
# ---- 失败收集（与 setup.sh 的 warn_collect 对等）----
# 非致命问题不再中断安装，收集起来在结尾汇总。即时打印也要有：用户盯着十几分钟的
# 安装，问题发生时就该看见，而不是只在最后的汇总里（还可能被滚屏冲掉）。
$script:Warnings = New-Object System.Collections.ArrayList
function Add-Warning([string]$Label, [string]$Detail, [string]$Fix = '', [string]$Severity = 'low') {
    [void]$script:Warnings.Add([pscustomobject]@{
        Label = $Label; Detail = $Detail; Fix = $Fix; Severity = $Severity })
    Write-Host "  [!] $Label : $Detail" -ForegroundColor Yellow
    if ($Fix) { Write-Host "      -> $Fix" -ForegroundColor DarkGray }
    if ($env:EASEL_SETUP_STRICT) {
        Fail "$Label : $Detail（EASEL_SETUP_STRICT 下警告视为致命）"
    }
}

function Invoke-NativeCapture([string]$Exe, [string[]]$Arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $Exe @Arguments 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return [pscustomobject]@{ Ok = ($code -eq 0); Output = $out; Code = $code }
}

Write-Host "`nEasel · Windows 安装向导" -ForegroundColor Magenta
Info '安装 Easel 与 Web 依赖；进入 Web 设置选择 Codex / OpenCode / ACP / Pi，或 API 仅聊天模式。'
Step '1/5' '检查系统环境' 'Python · Node.js · Git · FFmpeg'
Info '检查系统环境...'
Install-RequiredCommand 'git' 'Git.Git' '请安装 Git for Windows 并加入 PATH。'
Install-RequiredCommand 'node' 'OpenJS.NodeJS.LTS' '请安装 Node.js 24 并加入 PATH。'
Install-RequiredCommand 'npm' 'OpenJS.NodeJS.LTS' '请安装 Node.js 24 并加入 PATH。'
if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) { Install-RequiredCommand 'python' 'Python.Python.3.12' '请安装 Python 3.10+ 并勾选 Add Python to PATH。' }
Install-RequiredCommand 'ffmpeg' 'Gyan.FFmpeg' '请安装 FFmpeg 并加入 PATH。'
$nodeParts = (& node -p 'process.versions.node').Split('.') | ForEach-Object { [int]$_ }
$nodeOk = ($nodeParts[0] -eq 20 -and $nodeParts[1] -ge 19) -or ($nodeParts[0] -eq 22 -and $nodeParts[1] -ge 12) -or ($nodeParts[0] -ge 23)
if (-not $nodeOk) { Fail '当前 Node.js 不满足 Web 安装依赖；需要 20.19+（20.x）或 22.12+。' }
$pythonCommand = (Get-Command python -ErrorAction SilentlyContinue).Source
# 这是一次可用性探测：python 存在但跑不起来（典型是 Windows 应用商店的占位 python.exe）
# 时要安静地换下一个候选，不能让它抛 NativeCommandError 把安装打断。
if ($pythonCommand -and -not (Invoke-NativeCapture $pythonCommand @('--version')).Ok) { $pythonCommand = $null }
if (-not $pythonCommand -and (Get-Command py -ErrorAction SilentlyContinue)) { $pythonCommand = (Get-Command py).Source; $pythonArgs = @('-3') } else { $pythonArgs = @() }
if (-not $pythonCommand) { Fail '未找到可运行的 Python 3；请安装 Python 3.10+。' }
& $pythonCommand @pythonArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) { Fail 'Python 3.10+ 是必需依赖。' }
if (-not (Test-Path $Venv)) { Info '创建 Python 虚拟环境...'; & $pythonCommand @pythonArgs -m venv $Venv }
if (-not (Test-Path $Python)) { Fail 'Python venv 创建失败。' }
Ok '系统环境检查完成'

Step '2/5' '安装 Easel 运行依赖' 'Web · 媒体 · 浏览器发布'
Info '安装 Easel Python 依赖...'
& $Python -m pip install --upgrade pip --progress-bar on
if ($LASTEXITCODE -ne 0) { Fail 'pip 升级失败。' }
& $Python -m pip install -e $Root --prefer-binary --progress-bar on
if ($LASTEXITCODE -ne 0) { Fail 'Easel Python 依赖安装失败。' }
Step '3/5' '构建 Web 工作台' 'React production bundle'
Info '构建 Web 前端...'
$Frontend = Join-Path $Root 'web\frontend'
Push-Location $Frontend
try {
    & npm install
    if ($LASTEXITCODE -ne 0) { Fail 'Web 前端依赖安装失败。' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { Fail 'Web 前端构建失败。' }
} finally { Pop-Location }
Step '4/5' '安装 Playwright Chromium' '浏览器登录与发布'
Info '安装 Playwright Chromium...'
# 降级而非中断：这是个 ~300MB 的下载，弱网下失败很常见，且只影响浏览器登录/发布。
if (-not (Invoke-WithProgress 'Playwright Chromium' $Python @('-m','playwright','install','chromium'))) {
    Add-Warning 'Playwright Chromium' '下载失败，浏览器登录/发布不可用' `
        "$Python -m playwright install chromium"
}

$envPath = Join-Path $Root '.env'
if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root '.env.example') $envPath }
Step '5/5' '准备执行引擎设置' 'Codex · OpenCode · ACP · Pi · API 仅聊天模式'
& $Python -m easel.installation $Root api
if ($LASTEXITCODE -ne 0) { Fail '执行引擎设置初始化失败；已有配置未覆盖，请检查文件后重试。' }

Show-Summary
Write-Host ''
Write-Host '  下一步：启动 Web → 设置 → Agent 底座 → 执行引擎 → 选择 Codex / OpenCode / ACP / Pi → 保存' -ForegroundColor Cyan
Write-Host '  原生认证由对应 CLI 管理；新用户默认 API 仅聊天模式，可稍后填写模型 API 配置。' -ForegroundColor DarkGray
Write-Host ''
Write-Host "  启动 Web：$Venv\Scripts\easel.exe web" -ForegroundColor Cyan
Write-Host "  检查环境：$Venv\Scripts\easel.exe doctor" -ForegroundColor Cyan
