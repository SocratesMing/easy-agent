#!/usr/bin/env bash
# ==============================================================================
# memory_guard.sh —— Pod 容器内内存监控与自愈守护
#
# 功能：
#   1. 读取「容器自身 cgroup」的内存使用量与上限（不读 /proc/meminfo，
#      因为容器内 /proc/meminfo 显示的是宿主机内存，用它算出的使用率是错的）
#   2. 每 INTERVAL 秒（默认 60）检查使用率是否超过 THRESHOLD%（默认 75）
#   3. 超限时找出 RSS 最大的进程，按「子进程 → 父进程」顺序终止：
#      先杀子进程，父进程有机会 wait() 回收它，从而避免产生僵尸进程；
#      若反过来先杀父进程，子进程会被 reparent 到 PID 1，
#      而容器内 PID 1 通常不回收 SIGCHLD，子进程即成为僵尸。
#   4. 全过程记录日志；日志路径、阈值、间隔等均可配置
#
# 配置优先级（高 → 低）：命令行参数 > 环境变量 > 配置文件 > 内置默认值
#
# 配置项：
#   THRESHOLD              内存使用率阈值（百分比整数，默认 75）
#   LOG_FILE               日志文件路径（默认 /var/log/memory-guard.log）
#   INTERVAL               检查间隔秒数（默认 60）
#   KILL_GRACE             SIGTERM 后等待优雅退出的秒数，超时再 SIGKILL（默认 10）
#   ON_UNLIMITED           容器无内存上限时：skip=跳过(默认) / use_host=按宿主机内存计算
#   PROTECTED_PIDS         永不终止的 PID 列表，空格分隔（默认 "1"）
#   PROTECTED_NAMES        永不终止的进程名关键字，空格分隔（默认本脚本名）
#   DRY_RUN                1=只记录不真正 kill（默认 0）
#
# 用法：
#   ./memory_guard.sh                       # 常驻守护，每 60s 检查一次
#   ./memory_guard.sh --once                # 只检查一次（配合 cron 使用）
#   ./memory_guard.sh --dry-run             # 演练模式，不真正终止进程
#   ./memory_guard.sh -t 80 -l /tmp/mg.log -i 30
#   ./memory_guard.sh --status              # 只打印当前容器内存状态
#   ./memory_guard.sh --help
#
# 环境变量：MEMORY_GUARD_CONFIG / MEMORY_GUARD_THRESHOLD / MEMORY_GUARD_LOG_FILE
#           MEMORY_GUARD_INTERVAL / MEMORY_GUARD_KILL_GRACE / MEMORY_GUARD_ON_UNLIMITED
# ==============================================================================
set -uo pipefail

# ---------------------------------------------------------------- 内置默认值 --
THRESHOLD=75
LOG_FILE="/var/log/memory-guard.log"
INTERVAL=60
KILL_GRACE=10
ON_UNLIMITED="skip"
PROTECTED_PIDS="1"
PROTECTED_NAMES="memory_guard.sh"
DRY_RUN=0
CONFIG_FILE="${MEMORY_GUARD_CONFIG:-/etc/memory-guard.conf}"

# ------------------------------------------------------------------ 配置文件 --
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

# ---------------------------------------------------------------- 环境变量覆盖 --
[[ -n "${MEMORY_GUARD_THRESHOLD:-}" ]]   && THRESHOLD="$MEMORY_GUARD_THRESHOLD"
[[ -n "${MEMORY_GUARD_LOG_FILE:-}" ]]    && LOG_FILE="$MEMORY_GUARD_LOG_FILE"
[[ -n "${MEMORY_GUARD_INTERVAL:-}" ]]    && INTERVAL="$MEMORY_GUARD_INTERVAL"
[[ -n "${MEMORY_GUARD_KILL_GRACE:-}" ]]  && KILL_GRACE="$MEMORY_GUARD_KILL_GRACE"
[[ -n "${MEMORY_GUARD_ON_UNLIMITED:-}" ]] && ON_UNLIMITED="$MEMORY_GUARD_ON_UNLIMITED"

# ------------------------------------------------------------------ 命令行参数 --
RUN_ONCE=0
SHOW_STATUS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--threshold)   THRESHOLD="$2";     shift 2 ;;
        -l|--log)         LOG_FILE="$2";      shift 2 ;;
        -i|--interval)    INTERVAL="$2";      shift 2 ;;
        -g|--grace)       KILL_GRACE="$2";    shift 2 ;;
        -c|--config)      CONFIG_FILE="$2";   shift 2 ;;
            --on-unlimited) ON_UNLIMITED="$2"; shift 2 ;;
            --once)        RUN_ONCE=1;        shift ;;
            --dry-run)     DRY_RUN=1;         shift ;;
            --status)      SHOW_STATUS=1;     shift ;;
        -h|--help)        sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数: $1（用 --help 查看用法）" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------- 日志 --
LOG_READY=0
init_log() {
    local dir
    dir="$(dirname "$LOG_FILE")"
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir" 2>/dev/null || LOG_FILE="/tmp/memory-guard.log"
    fi
    # 子 shell 整体重定向，避免重定向失败时 shell 打印 Permission denied 噪音
    if ( : >>"$LOG_FILE" ) 2>/dev/null; then
        LOG_READY=1
    else
        LOG_FILE="/tmp/memory-guard.log"
        if ( : >>"$LOG_FILE" ) 2>/dev/null; then LOG_READY=1; fi
    fi
}

log() {
    local level="$1"; shift
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [$level] [pid=$$] $*"
    echo "$msg"                                  # 同时进容器 stdout（kubectl logs 可见）
    [[ "$LOG_READY" -eq 1 ]] && echo "$msg" >>"$LOG_FILE"
}

log_startup() {
    log INFO "========== memory_guard 启动 =========="
    log INFO "配置: 阈值=${THRESHOLD}% 间隔=${INTERVAL}s 日志=${LOG_FILE}"
    log INFO "配置: 优雅等待=${KILL_GRACE}s 无上限策略=${ON_UNLIMITED} 演练模式=${DRY_RUN}"
    log INFO "配置: 保护PID=[${PROTECTED_PIDS}] 保护进程名=[${PROTECTED_NAMES}]"
}

# --------------------------------------------------- cgroup 内存探测（容器自身）--
CG_VERSION=""
CG_LIMIT_FILE=""
CG_USAGE_FILE=""

detect_cgroup() {
    # cgroup v2：容器内 /sys/fs/cgroup 就是自身 cgroup 根
    if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
        CG_VERSION=2
        CG_LIMIT_FILE="/sys/fs/cgroup/memory.max"
        CG_USAGE_FILE="/sys/fs/cgroup/memory.current"
        return 0
    fi
    # cgroup v1
    if [[ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then
        CG_VERSION=1
        CG_LIMIT_FILE="/sys/fs/cgroup/memory/memory.limit_in_bytes"
        CG_USAGE_FILE="/sys/fs/cgroup/memory/memory.usage_in_bytes"
        return 0
    fi
    return 1
}

# 输出：当前使用量(字节)
read_usage_bytes() {
    local v
    v=$(cat "$CG_USAGE_FILE" 2>/dev/null) || return 1
    [[ "$v" =~ ^[0-9]+$ ]] || return 1
    echo "$v"
}

# 输出：上限(字节)，0 表示无限制
read_limit_bytes() {
    local v
    v=$(cat "$CG_LIMIT_FILE" 2>/dev/null) || { echo 0; return 0; }
    if [[ "$v" == "max" ]]; then echo 0; return 0; fi
    [[ "$v" =~ ^[0-9]+$ ]] || { echo 0; return 0; }
    # v1 无限制时是一个极大值（9223372036854771712）
    if (( v > 1099511627776 )); then echo 0; return 0; fi
    echo "$v"
}

# 宿主机内存总量（仅 ON_UNLIMITED=use_host 兜底用）
host_mem_bytes() {
    awk '/^MemTotal:/ {print $2 * 1024; exit}' /proc/meminfo 2>/dev/null
}

human_mb() { awk -v b="$1" 'BEGIN{printf "%.1f", b/1048576}'; }

# -------------------------------------------------------------- 进程信息读取 --
# 从 /proc/<pid>/stat 解析 PPID（comm 可能含空格和括号，需跳过）
get_ppid() {
    local pid="$1" s
    s=$(cat "/proc/$pid/stat" 2>/dev/null) || return 1
    s="${s##*) }"                       # 丢弃 "pid (comm) "
    # 此时字段为：state ppid ...
    set -- $s
    echo "${2:-}"
}

# 进程名（用于保护列表匹配）
get_comm() {
    local pid="$1" s
    s=$(cat "/proc/$pid/stat" 2>/dev/null) || return 1
    s="${s#*\(}"; s="${s%)*}"
    echo "$s"
}

# RSS（KB）
get_rss_kb() {
    awk '/^VmRSS:/ {print $2; exit}' "/proc/$1/status" 2>/dev/null
}

# /proc 下线程也有独立 tid 目录；只处理线程组 leader（Tgid == pid），
# 否则会把子线程当成独立进程，kill tid 并不能终止进程
is_thread_leader() {
    local pid="$1" tgid
    tgid=$(awk '/^Tgid:/ {print $2; exit}' "/proc/$pid/status" 2>/dev/null)
    [[ "$tgid" == "$pid" ]]
}

is_protected() {
    local pid="$1" p name
    [[ "$pid" == "$$" ]] && return 0
    for p in $PROTECTED_PIDS; do [[ "$pid" == "$p" ]] && return 0; done
    name=$(get_comm "$pid")
    for p in $PROTECTED_NAMES; do
        [[ "$name" == *"$p"* ]] && return 0
    done
    return 1
}

# ------------------------------------------------- 进程树：自底向上（子 → 父）--
declare -A CHILDREN_MAP=()

build_children_map() {
    CHILDREN_MAP=()
    local d pid ppid
    for d in /proc/[0-9]*; do
        pid="${d#/proc/}"
        is_thread_leader "$pid" || continue
        ppid=$(get_ppid "$pid") || continue
        [[ -n "$ppid" && "$ppid" != "$pid" ]] || continue
        CHILDREN_MAP["$ppid"]="${CHILDREN_MAP[$ppid]:-} $pid"
    done
}

# 后序遍历：先输出所有子孙，最后输出自身（保证子进程先于父进程被终止）
post_order() {
    local pid="$1" child
    for child in ${CHILDREN_MAP[$pid]:-}; do
        [[ -d "/proc/$child" ]] || continue
        post_order "$child"
    done
    echo "$pid"
}

# 找出 RSS 最大的进程（排除受保护进程）
find_top_process() {
    local d pid rss max_rss=0 top_pid=0 top_name=""
    for d in /proc/[0-9]*; do
        pid="${d#/proc/}"
        [[ -r "$d/status" ]] || continue
        is_thread_leader "$pid" || continue
        is_protected "$pid" && continue
        rss=$(get_rss_kb "$pid")
        [[ -n "$rss" && "$rss" =~ ^[0-9]+$ ]] || continue
        if (( rss > max_rss )); then
            max_rss=$rss; top_pid=$pid; top_name=$(get_comm "$pid")
        fi
    done
    echo "$top_pid $max_rss $top_name"
}

# ---------------------------------------------------------------- 终止进程树 --
terminate_process_tree() {
    local root="$1" root_name="$2" root_rss="$3"
    local order pid alive=() p

    order=$(post_order "$root")
    log WARN "准备终止进程树: root=${root}(${root_name}, RSS=${root_rss}KB) 终止顺序(子→父): $(echo "$order" | tr '\n' ' ')"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log WARN "[演练模式] 跳过实际终止。完整顺序: $(echo "$order" | tr '\n' ' ')"
        return 0
    fi

    # 第一轮：SIGTERM，按 子 → 父 顺序，让父进程有机会 wait() 回收子进程
    while read -r pid; do
        [[ -n "$pid" && -d "/proc/$pid" ]] || continue
        if kill -TERM "$pid" 2>/dev/null; then
            log INFO "发送 SIGTERM → pid=${pid} ($(get_comm "$pid"))"
        fi
    done <<<"$order"

    # 等待优雅退出
    sleep "$KILL_GRACE"

    # 第二轮：仍存活的进程 SIGKILL（同样 子 → 父）
    while read -r pid; do
        [[ -n "$pid" && -d "/proc/$pid" ]] || continue
        if kill -KILL "$pid" 2>/dev/null; then
            log WARN "进程未优雅退出，发送 SIGKILL → pid=${pid} ($(get_comm "$pid"))"
        fi
    done <<<"$order"

    # 残留检查（含僵尸进程统计）
    sleep 1
    alive=()
    while read -r pid; do
        [[ -n "$pid" && -d "/proc/$pid" ]] && alive+=("$pid")
    done <<<"$order"
    if ((${#alive[@]} > 0)); then
        log ERROR "以下进程未能终止: ${alive[*]}"
    else
        log INFO "进程树 ${root} 已全部终止"
    fi

    # 记录僵尸进程（父进程未 wait 的子进程）
    local zombies=0 d st
    for d in /proc/[0-9]*; do
        st=$(awk '{print $3}' "${d}/stat" 2>/dev/null)
        [[ "$st" == "Z" ]] && ((zombies++))
    done
    (( zombies > 0 )) && log WARN "当前容器残留僵尸进程数: ${zombies}（需由 PID 1 或父进程回收）"
    return 0
}

# ---------------------------------------------------------------- 单次检查 --
check_once() {
    if ! detect_cgroup; then
        log ERROR "未找到 cgroup 内存接口，无法读取容器内存（脚本可能不在容器中运行）"
        return 1
    fi

    local usage limit pct
    usage=$(read_usage_bytes) || { log ERROR "读取内存使用量失败: $CG_USAGE_FILE"; return 1; }
    limit=$(read_limit_bytes)

    if (( limit == 0 )); then
        if [[ "$ON_UNLIMITED" == "use_host" ]]; then
            limit=$(host_mem_bytes)
            log WARN "容器未设置内存上限，按宿主机内存计算: limit=$(human_mb "$limit")MB"
        else
            log WARN "容器未设置内存上限（${CG_LIMIT_FILE}），跳过本次检查（可设 ON_UNLIMITED=use_host 改变策略）"
            return 0
        fi
    fi
    (( limit <= 0 )) && { log ERROR "内存上限解析异常: $limit"; return 1; }

    pct=$(( usage * 100 / limit ))
    log INFO "内存检查: 使用=$(human_mb "$usage")MB 上限=$(human_mb "$limit")MB 使用率=${pct}% 阈值=${THRESHOLD}% (cgroup v${CG_VERSION})"

    if (( pct < THRESHOLD )); then
        return 0
    fi

    log WARN "内存使用率 ${pct}% 已超过阈值 ${THRESHOLD}%，开始查找占用内存最大的进程"

    build_children_map
    local top top_pid top_rss top_name
    top=$(find_top_process)
    top_pid=$(echo "$top" | awk '{print $1}')
    top_rss=$(echo "$top" | awk '{print $2}')
    top_name=$(echo "$top" | awk '{print $3}')

    if [[ -z "$top_pid" || "$top_pid" == "0" ]]; then
        log ERROR "未找到可终止的进程（可能全部处于保护列表）"
        return 1
    fi

    log WARN "最大内存进程: pid=${top_pid} name=${top_name} RSS=${top_rss}KB($(_mb_from_kb "$top_rss")MB)"
    terminate_process_tree "$top_pid" "$top_name" "$top_rss"
}

_mb_from_kb() { awk -v k="$1" 'BEGIN{printf "%.1f", k/1024}'; }

# ------------------------------------------------------------------ 状态输出 --
show_status() {
    detect_cgroup || { echo "未找到 cgroup 内存接口"; exit 1; }
    local usage limit
    usage=$(read_usage_bytes); limit=$(read_limit_bytes)
    echo "cgroup 版本   : v${CG_VERSION}"
    echo "使用量        : $(human_mb "$usage") MB"
    if (( limit > 0 )); then
        echo "上限          : $(human_mb "$limit") MB"
        echo "使用率        : $(( usage * 100 / limit ))%"
    else
        echo "上限          : 未限制"
    fi
    echo "阈值          : ${THRESHOLD}%"
    echo "日志文件      : ${LOG_FILE}"
}

# ---------------------------------------------------------------------- 主流程 --
init_log

if [[ "$SHOW_STATUS" -eq 1 ]]; then
    show_status
    exit 0
fi

log_startup

trap 'log INFO "========== memory_guard 退出 =========="; exit 0' SIGTERM SIGINT

while true; do
    check_once || true
    if [[ "$RUN_ONCE" -eq 1 ]]; then
        log INFO "单次检查模式，退出"
        break
    fi
    sleep "$INTERVAL"
done
