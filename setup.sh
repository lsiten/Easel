#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Easel 一键安装
# 用法: git clone <repo> && cd Easel && bash setup.sh [--api]
# ============================================================

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
for argument in "$@"; do
    case "$argument" in
        --api) ;;
        --check) export EASEL_SETUP_MODE=check ;;
        --strict) export EASEL_SETUP_STRICT=1 ;;
        *) echo "用法：bash setup.sh [--api] [--check] [--strict]" >&2; exit 1 ;;
    esac
done

# macOS ships Bash 3.2; keep the installer portable to that baseline.
if [ -z "${BASH_VERSION:-}" ]; then
    echo "请使用 Bash 运行 setup.sh（bash setup.sh）" >&2
    exit 1
fi

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
DIM='\033[2m'
NC='\033[0m'

info()  { echo -e "${CYAN}[easel]${NC} $*"; }
ok()    { echo -e "${GREEN}  ✓${NC} $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC} $*"; }
ask()   { if [ -t 0 ]; then printf "${CYAN}  ?${NC} %s " "$1" >&2; read -r REPLY; printf '%s' "$REPLY"; else printf ''; fi; }
step()  { echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${MAGENTA}  [$1]${NC} ${CYAN}$2${NC}"; echo -e "${DIM}  $3${NC}"; }

run_with_progress() {
    local label log_file pid started elapsed frame
    label="$1"
    shift
    log_file="$(mktemp "${TMPDIR:-/tmp}/easel-install.XXXXXX")"
    "$@" >"$log_file" 2>&1 &
    pid=$!
    started=$(date +%s)
    frame=0
    # 非 TTY（CI、重定向到文件）下不要转圈：1Hz 的 \r 会刷出几千行无用日志。
    if [ -t 2 ]; then
        while kill -0 "$pid" 2>/dev/null; do
            elapsed=$(( $(date +%s) - started ))
            case $((frame % 4)) in
                0) progress='[>   ]' ;;
                1) progress='[=>  ]' ;;
                2) progress='[==> ]' ;;
                *) progress='[===>]' ;;
            esac
            printf '\r  %s 进行中 %s 已运行 %ss' "$progress" "$label" "$elapsed" >&2
            frame=$((frame + 1))
            sleep 1
        done
    else
        # 这里不能自己 wait：后面的 `if wait "$pid"` 才是取退出码的地方，
        # 提前 wait 会把进程回收掉，使那句 wait 返回 127 而把成功判成失败。
        printf '  [....] %s 开始（非交互终端，不显示进度）\n' "$label" >&2
    fi
    if wait "$pid"; then
        printf '\r  [====] %s 完成                         \n' "$label" >&2
        rm -f "$log_file"
        return 0
    fi
    printf '\r  [FAIL] %s 失败                         \n' "$label" >&2
    tail -40 "$log_file" >&2 || true
    rm -f "$log_file"
    return 1
}

# check：跑完所有探测与分支，但不真的改变任何状态，只向 stdout 吐 JSON 动作流。
# CI 用它在 ubuntu 与 windows 上验证安装逻辑，不需要下载近 1GB 的依赖。
SETUP_MODE="${EASEL_SETUP_MODE:-run}"
# 把所有警告升回致命。给依赖「失败即非零退出」的自动化/provisioning 脚本用。
SETUP_STRICT="${EASEL_SETUP_STRICT:-}"

_json_esc() {
    local s=$1
    s=${s//\\/\\\\}; s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}; s=${s//$'\t'/\\t}; s=${s//$'\r'/\\r}
    printf '%s' "$s"
}

# 动作流只在 check 模式下输出，而它会进 CI 日志，所以脱敏不能只看 key 名：
# 整块写 provider 时 key 是 models.providers.anthropic，密钥藏在 value 的 JSON 里
# （实测会把 apiKey 明文打进日志）。key 和 value 两侧都要判。
emit_action() {
    [ "$SETUP_MODE" = check ] || return 0
    local action="$1" key="${2:-}" value="${3:-}"
    case "$key" in
        *[Kk]ey*|*[Tt]oken*|*[Ss]ecret*|*[Pp]assword*|*headers*) value='<redacted>' ;;
    esac
    # value 侧只按「真的像凭据」的形态匹配，不能用裸 token 子串 ——
    # provider 的 models JSON 里有 maxTokens，会被连带遮掉，而那是该看见的信息
    # （contextWindow / maxTokens 没声明会让部分网关直接 400，正是要能核对的东西）。
    case "$value" in
        *'"apiKey"'*|*apiKey=*|*api_key*|*apikey=*|*Authorization*|*Bearer\ *|\
        *'"token"'*|*token=*|*[Ss]ecret*|*[Pp]assword*)
            value='<redacted>' ;;
    esac
    printf '{"action":"%s","key":"%s","value":"%s"}\n' \
        "$(_json_esc "$action")" "$(_json_esc "$key")" "$(_json_esc "$value")"
}

# ---- 失败收集 ----
# Bash 3.2（macOS 自带）没有关联数组，用三个平行索引数组。
WARN_LABELS=(); WARN_DETAILS=(); WARN_FIXES=(); WARN_SEVERITY=()

# 即时打印 + 记录。只记不printf 本身就是一种失败模式：用户盯着十几分钟的安装，
# 问题发生时就该看见，而不是只在最后的汇总里（还可能被滚屏冲掉）。
warn_collect() {   # $1 label  $2 detail  [$3 fix 命令]  [$4 severity=low]
    WARN_LABELS+=("$1"); WARN_DETAILS+=("$2")
    WARN_FIXES+=("${3:-}"); WARN_SEVERITY+=("${4:-low}")
    warn "$1：$2"
    [ -n "${3:-}" ] && echo -e "${DIM}      → ${3}${NC}" >&2
    # strict 模式下警告即致命，立刻中断并给出同样的提示。
    if [ -n "$SETUP_STRICT" ]; then
        fatal "$1：$2（EASEL_SETUP_STRICT=1 下警告视为致命）"
    fi
    return 0
}

fatal() {
    echo -e "\n${RED}  ✗ 安装中止${NC}：$*" >&2
    write_install_report "$*" 2>/dev/null || true
    # check 模式用 2 与「真实安装失败」区分：CI 据此判断是逻辑走到了致命分支，
    # 而不是环境本身装不上。
    [ "$SETUP_MODE" = check ] && exit 2
    exit 1
}

# 会改变状态的外部命令统一走这里，check 模式下只记录不执行。
run_step() {   # $1 label  其余为命令
    local label="$1"
    shift
    if [ "$SETUP_MODE" = check ]; then
        emit_action run_step "$label" "$*"
        return 0
    fi
    "$@"
}

# ---- 安装报告 ----
# 把本次安装的遗留问题落盘，供 `easel doctor` 复述。这样「装完还差什么」不依赖用户
# 记住十几分钟前滚过去的终端输出。
INSTALL_REPORT="$PROJECT_ROOT/outputs/_install/last-install.json"
write_install_report() {   # [$1 fatal 原因]
    [ "$SETUP_MODE" = check ] && return 0
    local i n fatal_json="null"
    [ -n "${1:-}" ] && fatal_json="\"$(_json_esc "$1")\""
    mkdir -p "$(dirname "$INSTALL_REPORT")" 2>/dev/null || return 0
    {
        printf '{\n'
        printf '  "ts": %s,\n' "$(date +%s)"
        printf '  "installer": "setup.sh",\n'
        printf '  "node": "%s",\n' "$(_json_esc "$(node -v 2>/dev/null || echo unknown)")"
        printf '  "fatal": %s,\n' "$fatal_json"
        printf '  "warnings": ['
        n=${#WARN_LABELS[@]}
        i=0
        while [ "$i" -lt "$n" ]; do
            [ "$i" -gt 0 ] && printf ','
            printf '\n    {"label":"%s","detail":"%s","fix":"%s","severity":"%s"}' \
                "$(_json_esc "${WARN_LABELS[$i]}")" \
                "$(_json_esc "${WARN_DETAILS[$i]}")" \
                "$(_json_esc "${WARN_FIXES[$i]}")" \
                "$(_json_esc "${WARN_SEVERITY[$i]}")"
            i=$((i + 1))
        done
        [ "$n" -gt 0 ] && printf '\n  '
        printf ']\n}\n'
    } > "$INSTALL_REPORT" 2>/dev/null || true
}

# ---- 结尾汇总 ----
# 变宽内容不套框线：CJK 是双宽字符，用字符数做 padding 的 ASCII 框线一定错位
# （本脚本开头那两个框就是错位的）。框线只留给纯 ASCII 的标题行。
print_summary() {
    local n i sev
    n=${#WARN_LABELS[@]}
    echo ""
    if [ "$n" -eq 0 ]; then
        echo -e "${GREEN}  ✓ Easel 安装完成${NC}"
    else
        echo -e "${YELLOW}  ✓ Easel 安装完成（有 ${n} 项降级）${NC}"
    fi
    if [ "$n" -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}  需要处理（${n}）：${NC}"
        # high 先于 low
        for sev in high low; do
            i=0
            while [ "$i" -lt "$n" ]; do
                if [ "${WARN_SEVERITY[$i]}" = "$sev" ]; then
                    echo -e "   ${YELLOW}⚠${NC} ${WARN_LABELS[$i]} — ${WARN_DETAILS[$i]}"
                    [ -n "${WARN_FIXES[$i]}" ] && echo -e "       ${DIM}→ ${WARN_FIXES[$i]}${NC}"
                fi
                i=$((i + 1))
            done
        done
        echo ""
        echo -e "${DIM}  以上均不影响已装好的部分；逐项修完可用 easel doctor 复检。${NC}"
        echo -e "${DIM}  若希望这些问题直接让安装失败（CI/自动化场景）：EASEL_SETUP_STRICT=1 bash setup.sh${NC}"
    fi
}


clear 2>/dev/null || true
echo -e "\n${CYAN}╭────────────────────────────────────────────────────╮${NC}"
echo -e "${CYAN}│${NC}  ${MAGENTA}Easel${NC} · 社媒内容工作台安装向导                 ${CYAN}│${NC}"
echo -e "${CYAN}│${NC}  ${DIM}多 Agent 执行引擎 · Linux / macOS${NC}               ${CYAN}│${NC}"
echo -e "${CYAN}╰────────────────────────────────────────────────────╯${NC}"
echo -e "\n${DIM}  安装 Easel 与 Web 依赖；进入 Web 设置选择 Codex / OpenCode / ACP / Pi，或 API 仅聊天模式。${NC}\n"

# 默认先走国内镜像再回落 nodejs.org：实测同一个包 nodejs.org ~20KB/s（20 分钟都下不完），
# 阿里云 ~590KB/s（53 秒）。EASEL_NODE_MIRROR 可覆盖，镜像目录结构需与 nodejs.org/dist 一致。
NODE_MIRROR_DEFAULT="https://mirrors.aliyun.com/nodejs-release"
step "1/6" "检查系统环境" "Python · Node.js · Git · FFmpeg"
info "检查 Node.js..."
node_version_ok() {
    { [ "$1" -eq 20 ] && [ "$2" -ge 19 ]; } \
        || { [ "$1" -eq 22 ] && [ "$2" -ge 12 ]; } \
        || [ "$1" -ge 23 ]
}
NODE_OK=false
if command -v node &>/dev/null; then
    NODE_VER=$(node -v | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
    NODE_MINOR=$(echo "$NODE_VER" | cut -d. -f2)
    if node_version_ok "$NODE_MAJOR" "$NODE_MINOR"; then
        NODE_OK=true
    fi
fi

if $NODE_OK; then
    ok "Node.js $NODE_VER"
else
    if [ -n "${NODE_VER:-}" ]; then
        info "Node.js $NODE_VER 不满足当前安装依赖，安装 Node.js 24..."
    else
        info "安装 Node.js 24..."
    fi
    if [ "$(uname -s)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install node@24
            BREW_NODE24_PREFIX="$(brew --prefix node@24)"
            export PATH="$BREW_NODE24_PREFIX/bin:$PATH"
        else
            echo "macOS 未找到 Homebrew。请先安装 Node.js 24（Homebrew: brew install node@24），再重新运行 setup.sh。" >&2
            exit 1
        fi
    else
        NODE_TARGET="v24.21.0"
        NODE_DIR="node-${NODE_TARGET}-linux-x64"
        # 已有 nvm 就装进用户目录：不需要 root，也不覆盖系统 node。
        if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
            # shellcheck disable=SC1091
            . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
            NVM_NODEJS_ORG_MIRROR="${EASEL_NODE_MIRROR:-$NODE_MIRROR_DEFAULT}" \
                nvm install "${NODE_TARGET#v}" >/dev/null 2>&1 || nvm install "${NODE_TARGET#v}"
            # --delete-prefix 必须在版本号之前；放在后面 nvm 会静默不切换，甚至让 node 从
            # PATH 上消失。它用来盖掉 ~/.npmrc 里与 nvm 冲突的 prefix/globalconfig 设置。
            nvm use --delete-prefix "${NODE_TARGET#v}" >/dev/null 2>&1 \
                || nvm use "${NODE_TARGET#v}"
        else
            # 原来写死 --max-time 120：这个包 30MB，nodejs.org 在国内链路常跌到 ~20KB/s，
            # 120 秒必然超时，配合 set -euo pipefail 让整个安装从第 1 步就断掉。
            # 改成多镜像依次重试（npm registry 那边早就有镜像回退，这里缺了）+ 放宽超时。
            NODE_OK_DL=false
            for base in "${EASEL_NODE_MIRROR:-$NODE_MIRROR_DEFAULT}" "https://nodejs.org/dist"; do
                if curl -fL --connect-timeout 15 --max-time 900 --retry 2 --retry-delay 3 \
                    "$base/${NODE_TARGET}/${NODE_DIR}.tar.xz" -o /tmp/node24.tar.xz; then
                    NODE_OK_DL=true; break
                fi
                warn "从 $base 下载 Node 失败，尝试下一个源"
            done
            if [ "$NODE_OK_DL" != true ]; then
                echo "Node.js ${NODE_TARGET} 下载失败。请手动安装 Node 24 后重新运行，或用" >&2
                echo "  EASEL_NODE_MIRROR=<镜像地址> bash setup.sh   指定可用镜像。" >&2
                exit 1
            fi
            cd /tmp && tar xf node24.tar.xz
            # /usr/local 对非 root 不可写，原来的裸 cp 必然 permission denied。
            NODE_SUDO=""
            if [ "$(id -u)" -ne 0 ]; then
                if command -v sudo >/dev/null 2>&1; then
                    NODE_SUDO="sudo"
                else
                    echo "需要 root 才能写入 /usr/local，且未找到 sudo。请手动安装 Node 24 后重试。" >&2
                    exit 1
                fi
            fi
            $NODE_SUDO cp -rf "${NODE_DIR}/bin/." /usr/local/bin/
            $NODE_SUDO cp -rf "${NODE_DIR}/lib/." /usr/local/lib/
            rm -rf "/tmp/${NODE_DIR}" /tmp/node24.tar.xz
            cd "$PROJECT_ROOT"
        fi
    fi
    ok "Node.js $(node -v)"
fi
if command -v python3 >/dev/null 2>&1; then
    PYTHON_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    PYTHON_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 10)))')
    if [ "$PYTHON_OK" -eq 1 ]; then
        ok "Python $(python3 --version 2>&1 | awk '{print $2}')"
    else
        echo "Python 版本过低：检测到 ${PYTHON_VER}，需要 Python 3.10+。" >&2
        exit 1
    fi
else
    echo "未找到 python3；Easel 需要 Python 3.10+。" >&2
    exit 1
fi
if [ -t 0 ]; then
    USE_VENV="$(ask '使用项目虚拟环境 .venv 安装 Python 依赖？[Y/n]')"
    case "${USE_VENV:-Y}" in
        n|N) warn "将使用当前 Python 环境" ;;
        *)
            if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
                if ! python3 -c 'import venv' >/dev/null 2>&1; then
                    echo "当前 Python 缺少 venv 模块，无法创建虚拟环境。" >&2
                    echo "Debian/Ubuntu 请运行：sudo apt install python3-venv" >&2
                    echo "RHEL/CentOS 请安装对应的 python3 virtualenv/venv 包后重试。" >&2
                    exit 1
                fi
                info "创建虚拟环境 .venv..."
                python3 -m venv "$PROJECT_ROOT/.venv"
            fi
            # shellcheck disable=SC1091
            source "$PROJECT_ROOT/.venv/bin/activate"
            if ! python3 -m pip --version >/dev/null 2>&1; then
                echo "虚拟环境已创建但缺少 pip，请检查系统 Python 的 ensurepip/venv 包后重试。" >&2
                exit 1
            fi
            ok "已使用虚拟环境：$PROJECT_ROOT/.venv"
            ;;
    esac
elif [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    # Non-interactive runs reuse an existing project environment when available.
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
    info "检测到 .venv，非交互模式自动使用项目虚拟环境"
else
    warn "当前为非交互模式且未找到 .venv，将使用系统 Python；建议先创建 Python 3.10+ 虚拟环境"
fi
command -v git >/dev/null 2>&1 && ok "Git $(git --version | awk '{print $3}')" || warn "未找到 git"
if command -v ffmpeg >/dev/null 2>&1; then
    ok "FFmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
else
    info "安装 FFmpeg（媒体功能必需）..."
    if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        brew install ffmpeg
    elif command -v apt-get >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then apt-get update && apt-get install -y ffmpeg
        elif command -v sudo >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y ffmpeg
        fi
    elif command -v dnf >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then dnf install -y ffmpeg
        elif command -v sudo >/dev/null 2>&1; then sudo dnf install -y ffmpeg
        fi
    elif command -v yum >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then yum install -y ffmpeg
        elif command -v sudo >/dev/null 2>&1; then sudo yum install -y ffmpeg
        fi
    fi
    if command -v ffmpeg >/dev/null 2>&1; then
        ok "FFmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
    else
        # 降级而非中断：缺 ffmpeg 只影响视频/音频类技能，对话与图文发布照常。
        warn_collect 'FFmpeg' '自动安装失败，视频/音频类技能不可用' \
            '手动安装 ffmpeg 后重新运行 bash setup.sh'
    fi
fi

# ---- 2. npm 源 ----
# 镜像选择：EASEL_NPM_REGISTRY 显式指定 > 连通性探测自动选择 > 默认官方源。
# 国内网络直连 registry.npmjs.org 经常极慢或超时，探测失败自动回落 npmmirror。
# 注意不再 `npm config set registry` 写用户全局配置 —— 改为安装命令上挂
# --registry，只影响本次安装，装完用户 npm 配置一个字节都没动。
step "2/6" "准备 Node.js 工具链" "选择 npm registry"
NPM_REGISTRY="${EASEL_NPM_REGISTRY:-}"
if [ -z "$NPM_REGISTRY" ]; then
    if npm ping --registry https://registry.npmjs.org --fetch-timeout=5000 \
        --fetch-retries=0 --fetch-retry-mintimeout=0 >/dev/null 2>&1; then
        NPM_REGISTRY="https://registry.npmjs.org"
        ok "npm registry: npmjs.org（连通性正常）"
    else
        NPM_REGISTRY="https://registry.npmmirror.com"
        warn "npmjs.org 连通性差，自动使用国内镜像 npmmirror.com（可用 EASEL_NPM_REGISTRY 覆盖）"
    fi
else
    ok "npm registry: ${NPM_REGISTRY}（EASEL_NPM_REGISTRY 指定）"
fi
NPM_REGISTRY_ARGS=(--registry "$NPM_REGISTRY")

# ---- 3. 安装 easel CLI ----
step "3/6" "安装 Easel 运行依赖" "Web · 媒体 · 浏览器发布"
info "[1/2] 安装 Python 依赖与 easel CLI..."
# pip 镜像：EASEL_PIP_INDEX 显式指定 > 官方源连通性探测 > 默认官方源（同 npm 逻辑）。
# 国内直连 pypi.org 装依赖（fastapi/playwright 等）经常超时；探测失败自动回落清华源。
PIP_INDEX="${EASEL_PIP_INDEX:-}"
if [ -z "$PIP_INDEX" ]; then
    # timeout 8 兜底：urlopen 的 timeout=4 不含 DNS 解析卡死等极端情形（macOS 无
    # coreutils timeout，command -v 判空时退化为仅靠 urlopen 自身超时）。
    if command -v timeout >/dev/null 2>&1; then
        PYPI_PROBE=(timeout 8 python3 -c "import urllib.request;urllib.request.urlopen('https://pypi.org/simple/', timeout=4)")
    else
        PYPI_PROBE=(python3 -c "import urllib.request;urllib.request.urlopen('https://pypi.org/simple/', timeout=4)")
    fi
    if "${PYPI_PROBE[@]}" >/dev/null 2>&1; then
        PIP_INDEX_ARGS=()
        ok "PyPI: pypi.org（连通性正常）"
    else
        PIP_INDEX_ARGS=(-i "https://pypi.tuna.tsinghua.edu.cn/simple")
        warn "pypi.org 连通性差，自动使用清华镜像（可用 EASEL_PIP_INDEX 覆盖）"
    fi
else
    PIP_INDEX_ARGS=(-i "$PIP_INDEX")
    ok "PyPI: ${PIP_INDEX}（EASEL_PIP_INDEX 指定）"
fi
# --prefer-binary: 新版 biliup 常先发 sdist 后补 wheel，源码构建要求最新 rustc；优先选有 wheel 的旧版本
PIP_ARGS=(install -e "$PROJECT_ROOT" --prefer-binary --progress-bar on ${PIP_INDEX_ARGS[@]+"${PIP_INDEX_ARGS[@]}"})
if [ "$(id -u)" -eq 0 ]; then
    PIP_ARGS+=(--root-user-action=ignore)
    warn "当前以 root 安装；生产服务器建议使用虚拟环境"
fi
run_step 'pip install' run_with_progress "Python 依赖安装" python3 -m pip "${PIP_ARGS[@]}"
# check 模式下 pip 安装被跳过，easel 自然不会出现 —— 这里不能当成失败，否则 check
# 模式必然在依赖安装后中断，整个「不下载依赖也能验证安装逻辑」的用途就没了。
if [ "$SETUP_MODE" != check ] && ! command -v easel >/dev/null 2>&1; then
    fatal "easel 命令未找到；请检查 Python 环境和 PATH（重试：bash setup.sh）"
fi
ok "[2/2] easel 命令可用"

# ---- 4. 构建 Web 前端（Node 已装 → easel web 直接出真 UI，无需手动构建） ----
step "4/6" "构建 Web 工作台" "React production bundle"
info "构建 Web 前端..."
if [ -d "$PROJECT_ROOT/web/frontend" ]; then
    if [ "$SETUP_MODE" = check ]; then
        emit_action run_step '前端依赖与构建' '依赖安装 + production bundle'
    elif ! (
        cd "$PROJECT_ROOT/web/frontend"
        if [ -f package-lock.json ]; then npm ci --no-audit --no-fund "${NPM_REGISTRY_ARGS[@]}" || npm install --no-audit --no-fund "${NPM_REGISTRY_ARGS[@]}"; else npm install --no-audit --no-fund "${NPM_REGISTRY_ARGS[@]}"; fi
        npm run build
    ); then
        echo "前端依赖安装或构建失败；请检查 Node.js/npm 网络后重新运行 bash setup.sh。" >&2
        exit 1
    fi
    ok "前端已构建 → web/frontend/dist/"
else
    echo "未找到 web/frontend，无法完成 Web 工作台安装。" >&2
    exit 1
fi

step "5/6" "准备执行引擎设置" "Codex · OpenCode · ACP · Pi · API 仅聊天模式"
if [ -f "$PROJECT_ROOT/.env" ]; then
    ok ".env 已存在"
else
    run_step '创建 .env' cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    ok "已创建 .env；模型与执行引擎可以稍后在 Web 设置"
fi
run_step '执行引擎初始设置' python3 -m easel.installation "$PROJECT_ROOT" api
ok "执行引擎可稍后在 Web 设置；新安装默认 API 仅聊天模式"

step "6/6" "安装并验证浏览器" "平台操作优先 Ego，其次 Chrome；Chromium 用于渲染"
if [ "$SETUP_MODE" = check ]; then
    # check 模式跳过了 pip 安装，playwright 模块自然不在。这里不能当成失败，否则
    # check 模式必然在最后一步中断 —— 与上面 easel 命令那处同理。
    emit_action run_step 'playwright chromium' 'python3 -m playwright install chromium'
elif python3 -c 'import playwright' >/dev/null 2>&1; then
    # Chromium 保留给 HTML/PDF/卡片渲染，账号流程另行检测 Ego / Chrome。
    CHROMIUM_OK=true
    run_step 'playwright chromium' python3 -m playwright install chromium || CHROMIUM_OK=false
    if [ "$CHROMIUM_OK" = true ] && [ "$SETUP_MODE" != check ]; then
        python3 -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); path=Path(p.chromium.executable_path); p.stop(); raise SystemExit(0 if path.is_file() else 1)' \
            || CHROMIUM_OK=false
    fi
    if [ "$CHROMIUM_OK" = true ]; then
        ok "Playwright Chromium 已就绪"
    else
        warn_collect 'Playwright Chromium' '下载或校验失败，HTML/PDF/卡片渲染不可用' \
            'python3 -m playwright install chromium'
    fi
else
    # 这个仍然致命：playwright 模块缺失意味着 pip 安装没装全，不是网络抖动。
    fatal "未找到 Playwright 模块；Python 依赖安装不完整（重试：bash setup.sh）"
fi

if [ "$SETUP_MODE" = check ]; then
    emit_action run_step '平台浏览器检测' 'python3 -m easel.browser'
elif python3 -m easel.browser; then
    ok "复用本机已安装的 Ego / Chrome"
else
    warn_collect '平台浏览器' '未找到可用的 Ego / Chrome，请安装 Chrome 或检查 Ego 安装' \
        'python3 -m playwright install chrome'
fi

write_install_report
print_summary
echo -e "\n  ${CYAN}下一步：easel web → 设置 → Agent 底座 → 执行引擎${NC}"
echo -e "  ${DIM}选择 Codex / OpenCode / ACP / Pi 并保存，再新建对话；原生认证由对应 CLI 管理。${NC}"
echo -e "  ${DIM}API 仅聊天模式通过模型 API 直连；请在 Web 设置填写模型 API 配置。${NC}"
echo -e "\n  ${CYAN}开始使用：${NC}"
if [ -x "$PROJECT_ROOT/.venv/bin/easel" ]; then
    echo -e "    ${CYAN}source .venv/bin/activate${NC}    # 先激活虚拟环境，easel 命令才可用"
    echo -e "    ${DIM}# 新开终端都要先激活；或不激活直接用 .venv/bin/easel <命令>${NC}"
fi
echo "    easel web                    # 启动 Web 工作台"
echo "    easel chat                   # 终端对话"
echo "    easel doctor                 # 检查环境"
echo "    easel ping                   # 当前模式连通性"
echo -e "  ${DIM}项目目录：$PROJECT_ROOT${NC}\n"
