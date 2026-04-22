#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# self_timer.sh — 自我定时催促脚本
#
# 用法：
#   bash scripts/self_timer.sh <agent_id> <task_id> <分钟数> "<提醒内容>"
#   bash scripts/self_timer.sh shangshu JJC-001 7 "礼部小说任务进展如何" &
#
# 子命令：
#   bash scripts/self_timer.sh list              查看活跃定时
#   bash scripts/self_timer.sh cancel <timer_id> 取消指定定时
#   bash scripts/self_timer.sh cancel-all        取消所有定时
#
# 原理：sleep 指定时间后，调用 openclaw agent --agent <id> -m <msg>
#       真正给 agent 发送一条消息，agent 会收到并响应。
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

TIMER_DIR="$HOME/.openclaw/timers"
mkdir -p "$TIMER_DIR"

# ── 工具函数 ──

usage() {
    cat <<'EOF'
自我定时催促脚本

用法：
  # 简化调用（默认7分钟）：
  bash scripts/self_timer.sh -<agent_id> <task_id> "<提醒内容>"
  bash scripts/self_timer.sh -shangshu JJC-001 "礼部小说任务进展如何" &

  # 完整调用（自定义分钟数）：
  bash scripts/self_timer.sh <agent_id> <task_id> <分钟数> "<提醒内容>"
  bash scripts/self_timer.sh shangshu JJC-001 7 "礼部小说任务进展如何" &

  # 管理定时器：
  bash scripts/self_timer.sh list
  bash scripts/self_timer.sh cancel <timer_id>
  bash scripts/self_timer.sh cancel-all

示例：
  bash scripts/self_timer.sh -shangshu JJC-001 "礼部小说任务进展如何" &
  bash scripts/self_timer.sh shangshu JJC-002 10 "工部部署完成了吗" &
EOF
    exit 0
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ── 子命令：list ──

cmd_list() {
    local count=0
    echo "═══════════════ 活跃定时器 ═══════════════"
    if [ -d "$TIMER_DIR" ]; then
        for state_file in "$TIMER_DIR"/*.state; do
            [ -f "$state_file" ] || continue
            count=$((count + 1))
            source "$state_file"
            local now epoch_now fire_in status_str
            epoch_now=$(date +%s)
            fire_in=$(( FIRE_EPOCH - epoch_now ))
            if [ "$fire_in" -gt 0 ]; then
                status_str="⏳ ${fire_in}秒后触发"
            else
                status_str="⚠️ 已过期（进程可能已结束）"
            fi
            echo "  📌 $TIMER_ID"
            echo "     Agent:   $AGENT_ID"
            echo "     任务:    $TASK_ID"
            echo "     提醒:    $MESSAGE"
            echo "     PID:     $PID"
            echo "     状态:    $status_str"
            echo "     创建于:  $CREATED_AT"
            echo ""
        done
    fi
    if [ "$count" -eq 0 ]; then
        echo "  (无活跃定时器)"
    fi
    echo "═════════════════════════════════════════"
}

# ── 子命令：cancel ──

cmd_cancel() {
    local timer_id="$1"
    local state_file="$TIMER_DIR/${timer_id}.state"
    if [ ! -f "$state_file" ]; then
        echo "❌ 定时器不存在: $timer_id"
        echo "   使用 list 查看所有定时器"
        return 1
    fi
    source "$state_file"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        log "已终止进程 $PID"
    fi
    rm -f "$state_file"
    log "已取消定时器: $timer_id"
}

# ── 子命令：cancel-all ──

cmd_cancel_all() {
    local count=0
    if [ -d "$TIMER_DIR" ]; then
        for state_file in "$TIMER_DIR"/*.state; do
            [ -f "$state_file" ] || continue
            source "$state_file"
            if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null
            fi
            rm -f "$state_file"
            count=$((count + 1))
        done
    fi
    log "已取消 $count 个定时器"
}

# ── 主逻辑：创建定时器 ──

cmd_timer() {
    local agent_id="$1"
    local task_id="$2"
    local minutes="$3"
    local message="$4"

    # 参数校验
    if [ -z "$agent_id" ] || [ -z "$task_id" ] || [ -z "$minutes" ]; then
        echo "❌ 缺少参数"
        echo "   用法: bash scripts/self_timer.sh <agent_id> <task_id> <分钟数> \"<提醒内容>\""
        exit 1
    fi

    if ! echo "$minutes" | grep -qE '^[0-9]+$'; then
        echo "❌ 分钟数必须为正整数"
        exit 1
    fi

    if [ "$minutes" -lt 1 ]; then
        echo "❌ 分钟数最少为1"
        exit 1
    fi

    if [ -z "$message" ]; then
        message="请检查任务 $task_id 的执行进度"
    fi

    local seconds=$(( minutes * 60 ))
    local fire_epoch=$(( $(date +%s) + seconds ))
    local timer_id="$(date '+%Y%m%d_%H%M%S')_${agent_id}_${task_id}"
    local state_file="$TIMER_DIR/${timer_id}.state"
    local log_file="$TIMER_DIR/${timer_id}.log"

    # 生成定时器状态文件
    cat > "$state_file" <<STATE
AGENT_ID="$agent_id"
TASK_ID="$task_id"
MINUTES="$minutes"
MESSAGE="$message"
TIMER_ID="$timer_id"
PID=""
FIRE_EPOCH="$fire_epoch"
CREATED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
STATE

    # 后台执行定时逻辑
    (
        log "⏰ 定时器已启动: $timer_id"
        log "   Agent: $agent_id | 任务: $task_id | ${minutes}分钟后提醒"
        log "   提醒内容: $message"

        # 等待
        sleep "$seconds"

        # 检查定时器是否已被取消
        if [ ! -f "$state_file" ]; then
            log "定时器 $timer_id 已被取消，不发送提醒"
            exit 0
        fi

        # 构造消息
        local msg="⏰ 定时提醒 | 任务 ${task_id}\n${message}"
        log "🔔 触发提醒 → $agent_id: $message"

        # 调用 openclaw agent 发送消息
        openclaw agent --agent "$agent_id" -m "$msg" --timeout 60 2>&1 >> "$log_file" || {
            log "❌ 发送失败: openclaw agent 命令出错"
        }

        # 清理状态文件
        rm -f "$state_file"
        log "✅ 定时器 $timer_id 已完成并清理"
    ) &

    local pid=$!

    # 更新状态文件写入 PID
    echo "PID=\"$pid\"" >> "$state_file"

    echo "✅ 定时器已设置"
    echo "   ID:     $timer_id"
    echo "   Agent:  $agent_id"
    echo "   任务:   $task_id"
    echo "   延时:   ${minutes} 分钟"
    echo "   提醒:   $message"
    echo "   PID:    $pid"
    echo ""
    echo "   查看所有定时: bash scripts/self_timer.sh list"
    echo "   取消此定时:  bash scripts/self_timer.sh cancel $timer_id"
}

# ── 入口 ──

case "${1:-}" in
    # -<agent_id> <task_id> "<提醒内容>"  → 简化调用，默认7分钟
    -*)
        local_agent_id="${1#-}"
        local_task_id="${2:-}"
        local_msg="${3:-请检查任务 ${local_task_id} 的执行进度}"
        if [ -z "$local_task_id" ]; then
            echo "❌ 缺少任务ID"
            echo "   用法: bash scripts/self_timer.sh -<agent_id> <task_id> \"<提醒内容>\""
            exit 1
        fi
        cmd_timer "$local_agent_id" "$local_task_id" 7 "$local_msg"
        ;;
    list|ls)
        cmd_list
        ;;
    cancel|rm)
        [ -n "${2:-}" ] || { echo "❌ 请指定 timer_id，用 list 查看"; exit 1; }
        cmd_cancel "$2"
        ;;
    cancel-all|clear)
        cmd_cancel_all
        ;;
    -h|--help|help)
        usage
        ;;
    "")
        usage
        ;;
    *)
        cmd_timer "$1" "${2:-}" "${3:-}" "${4:-}"
        ;;
esac
