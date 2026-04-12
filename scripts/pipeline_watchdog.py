#!/usr/bin/env python3
"""
三省六部 · 监察脚本 (pipeline_watchdog.py)

V8 架构：监察系统仅保留 V8 检测函数（看板停滞、封驳循环、agentLog 异常、redirect 纠正），
不再进行事后审计（越权、跳步、断链、会话违规等已由 pipeline_orchestrator.py 管理状态流转）。

V8 检测功能：
  - check_kanban_stall: 基于 last_activity 时间戳的看板停滞检测
  - check_review_round_limit: 封驳循环检测（reviewRound >= MAX_REJECT_COUNT）
  - check_agent_log_anomalies: agentLog 异常关键词扫描
  - execute_redirect: 御史台纠正流转错误

辅助功能：
  - 自动归档 Done 超过 5 分钟的任务
  - 清理 Agent 会话（clear_agent_sessions）
  - 审计日志管理（heartbeat、violation 归档）

通知方式：
  V8 通知走 agent_notifier.py，本脚本不直接调用 notify_agent。

过滤规则：
  - 只监察 JJC- 开头的旨意任务，不监察对话
  - 支持手动排除特定任务（audit_exclude.json）

用法：由 run_loop.sh 每 60 秒调用一次，也可手动运行：
  python3 scripts/pipeline_watchdog.py
"""

import json
import pathlib
import subprocess
import sys
import time
import datetime
import os
import threading

# 平台兼容：Windows 使用 msvcrt，Linux/macOS 使用 fcntl
_IS_WINDOWS = os.name == 'nt'
if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl

# 北京时区 (UTC+8)
_BJT = datetime.timezone(datetime.timedelta(hours=8))

# ── 路径配置 ──────────────────────────────────────────────────────
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
TASKS_FILE = DATA_DIR / "tasks_source.json"
AUDIT_FILE = DATA_DIR / "pipeline_audit.json"
EXCLUDE_FILE = DATA_DIR / "audit_exclude.json"
WATCHDOG_CONFIG_FILE = DATA_DIR / "watchdog_config.json"
OCLAW_HOME = pathlib.Path.home() / ".openclaw"

# ── V8: 导入配置中心与看板命令模块 ────────────────────────────────
sys.path.insert(0, str(REPO_DIR / 'scripts'))
try:
    from config import (
        KANBAN_PATH, STALE_WARNING_TIMEOUT, STALE_ESCALATE_TIMEOUT,
        MAX_REJECT_COUNT, STATE_AGENT_MAP, VALID_TRANSITIONS, TERMINAL_STATES,
        ALL_AGENTS, MINISTRY_AGENTS, MESSAGE_TYPES, AGENT_LABELS
    )
    from kanban_commands import (
        find_task, get_task_state, add_message, log_flow,
        add_audit_flag, append_agent_log, get_unread_messages
    )
    _V8_MODULES_LOADED = True
except ImportError as e:
    _V8_MODULES_LOADED = False
    print(f"[V8兼容] 警告: 无法导入V8模块 (config/kanban_commands): {e}", flush=True)

# ── 超时阈值（秒）─────────────────────────────────────────────────
RECENT_DONE_MINUTES = 10  # 最近 N 分钟内完成的任务也需检查（防止速通逃逸）
AUTO_ARCHIVE_MINUTES = 5  # Done 超过 N 分钟自动归档

# ── 运行时配置 ────────────────────────────────────────────────────
# 运行时可覆盖的配置（由 load_watchdog_config() 从 watchdog_config.json 加载）
_cfg = {
    "auto_archive_minutes": AUTO_ARCHIVE_MINUTES,
    "recent_done_minutes": RECENT_DONE_MINUTES,
    "enabled_checks": {
        "kanban_stall": True,      # V8: 看板停滞检测（基于last_activity时间戳）
        "review_round_limit": True, # V8: 封驳循环检测（reviewRound >= MAX_REJECT_COUNT）
        "agent_log_anomalies": True, # V8: agentLog异常关键词扫描
    },
    "max_notifications": 200,
    "max_violations": 200,
    "max_archived_violations": 500,
    "max_archived_notifications": 100,
}


def load_watchdog_config():
    """从 data/watchdog_config.json 加载配置，覆盖默认阈值。

    如果配置文件不存在或格式错误，使用内置默认值（不中断运行）。
    """
    global _cfg
    try:
        if not WATCHDOG_CONFIG_FILE.exists():
            return
        with open(WATCHDOG_CONFIG_FILE, "r", encoding="utf-8") as _f:
            _user_cfg = json.load(_f)
        # 仅覆盖已知的 key，忽略未知字段
        for _key, _val in _user_cfg.items():
            if _key in _cfg:
                _cfg[_key] = _val
        log(f"已加载配置: auto_archive={_cfg['auto_archive_minutes']}min, "
            f"recent_done={_cfg['recent_done_minutes']}min")
    except Exception as _e:
        log(f"加载 watchdog_config.json 失败，使用默认值: {_e}")

# ── 日志工具 ──────────────────────────────────────────────────────
def log(msg):
    ts = datetime.datetime.now(_BJT).strftime("%H:%M:%S")
    print(f"[{ts}] [监察] {msg}", flush=True)


def _make_notif(notif_type, to, detail, task_id="", task_ids=None, status="sent"):
    """创建标准格式的通知记录（字段与前端 AuditPanel 对齐）。

    前端 AuditNotification 接口要求:
      type: 通知类型（与 NOTIFY_TYPE_META 对齐）
      to: 通知目标部门
      summary: 简短摘要
      sent_at: 发送时间
      detail: 详细描述
      task_id: 关联任务ID
      task_ids: 关联任务ID列表
      status: 发送状态
    """
    return {
        "type": notif_type,
        "to": to,
        "summary": detail[:80] if detail else "",
        "sent_at": datetime.datetime.now(_BJT).isoformat(),
        "detail": detail,
        "task_id": task_id,
        "task_ids": task_ids or [],
        "status": status,
    }


def _write_heartbeat():
    """写入心跳时间戳（main 入口处调用），即使后续逻辑崩溃也能证明监察在运行。"""
    try:
        audit = load_audit()
        now_iso = datetime.datetime.now(_BJT).isoformat()
        audit["last_check"] = now_iso
        audit.pop("error", None)  # 清除上次的错误标记
        save_audit(audit)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  部门名称映射
# ═══════════════════════════════════════════════════════════════════

# flow_log 中 from/to 的各种写法 → 统一 agent_id
NAME_TO_ID = {
    "皇上":   "huangshang",
    "太子":   "taizi",
    "中书省": "zhongshu",
    "中书":   "zhongshu",
    "门下省": "menxia",
    "门下":   "menxia",
    "尚书省": "shangshu",
    "尚书":   "shangshu",
    "工部":   "gongbu",
    "兵部":   "bingbu",
    "户部":   "hubu",
    "礼部":   "libu",
    "刑部":   "xingbu",
    "吏部":   "libu_hr",
    "吏部_hr": "libu_hr",
    "太子殿下": "taizi",
    "中书令": "zhongshu",
    "门下侍中": "menxia",
    "尚书令": "shangshu",
    # 「六部」不是具体部门名称，映射为特殊标记
    "六部":   "_liubu_invalid",
    "六部中": "_liubu_invalid",
}

# agent_id → 中文显示名（优先使用完整名称如"中书省"而非简称"中书"）
ID_TO_LABEL = {}
for _k, _v in NAME_TO_ID.items():
    if _v == "huangshang":
        continue
    if _v not in ID_TO_LABEL or len(_k) > len(ID_TO_LABEL[_v]):
        ID_TO_LABEL[_v] = _k
ID_TO_LABEL.setdefault("huangshang", "皇上")


# ═══════════════════════════════════════════════════════════════════
#  数据读写（带文件锁，防止并发读写导致数据丢失）
# ═══════════════════════════════════════════════════════════════════

def _lock_file(lock_f, exclusive=True):
    """平台无关的文件锁获取"""
    if _IS_WINDOWS:
        try:
            msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_NBLCK, 1)
        except (IOError, OSError):
            if not exclusive:
                pass  # 共享锁失败时降级为无锁读取
            else:
                raise
    else:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def _unlock_file(lock_f):
    """平台无关的文件锁释放"""
    try:
        if _IS_WINDOWS:
            msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

try:
    from file_lock import atomic_json_read, atomic_json_write
except ImportError:
    # Fallback: keep inline implementations
    def atomic_json_read(filepath, default):
        """带共享锁读取 JSON 文件（允许多个读者并发，阻止写者）"""
        if not pathlib.Path(filepath).exists():
            return default
        lock_path = pathlib.Path(str(filepath) + '.lock')
        lock_f = open(lock_path, 'a')
        try:
            _lock_file(lock_f, exclusive=False)
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
        finally:
            _unlock_file(lock_f)
            lock_f.close()

    def atomic_json_write(filepath, data):
        """带排他锁写入 JSON 文件（阻止所有其他读写）+ 原子写入"""
        lock_path = pathlib.Path(str(filepath) + '.lock')
        lock_f = open(lock_path, 'a')
        try:
            _lock_file(lock_f, exclusive=True)
            tmp_path = pathlib.Path(str(filepath) + '.tmp')
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            os.replace(str(tmp_path), str(filepath))
        except Exception as e:
            log(f"写入文件失败 {filepath}: {e}")
        finally:
            _unlock_file(lock_f)
            lock_f.close()


def load_tasks():
    """安全读取 tasks_source.json（带文件锁，兼容新旧两种格式）。

    新格式: {"tasks": [...], "global_counters": {...}}
    旧格式: [...]
    始终返回任务列表。
    """
    data = atomic_json_read(TASKS_FILE, {"tasks": [], "global_counters": {}})
    if isinstance(data, list):
        return data  # 兼容旧格式（纯列表）
    return data.get("tasks", [])


def load_audit():
    """读取历史审计日志（带文件锁）"""
    return atomic_json_read(AUDIT_FILE, {"last_check": "", "violations": [], "notifications": []})


def save_audit(audit):
    """写入审计日志（带文件锁 + 原子写入）"""
    atomic_json_write(AUDIT_FILE, audit)


def load_exclude_list():
    """读取手动排除的任务 ID 列表"""
    try:
        text = EXCLUDE_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
        return set(data.get("excluded_tasks", []))
    except Exception:
        return set()


def save_tasks(tasks):
    """写入任务文件（带文件锁 + 原子写入，保留字典格式和 global_counters）。

    如果文件当前为旧列表格式，自动升级为字典格式。
    """
    # 读取现有数据以保留 global_counters 等元数据
    data = atomic_json_read(TASKS_FILE, {"tasks": [], "global_counters": {}})
    if isinstance(data, list):
        data = {"tasks": data, "global_counters": {}}
    data["tasks"] = tasks
    atomic_json_write(TASKS_FILE, data)


# ═══════════════════════════════════════════════════════════════════
#  Agent 工具函数
# ═══════════════════════════════════════════════════════════════════

def normalize_name(raw):
    """将 flow_log 中的名称统一为 agent_id"""
    if not raw:
        return None
    stripped = raw.strip()
    return NAME_TO_ID.get(stripped, stripped.lower())


def _find_task_session_key_for_agent(task_data, target_agent_id):
    """按目标 Agent 查找该任务中已存在的 session key（忽略方向性）。

    用于断链处理时精准发送消息到该任务的子代理 session，
    而不是打到 Agent 的 main session。
    """
    if not task_data or not target_agent_id:
        return None
    target = target_agent_id.strip().lower()
    for pair, entry in task_data.get('session_keys', {}).items():
        agents = entry.get('agents', [])
        if target in [a.strip().lower() for a in agents]:
            key = entry.get('sessionKey')
            if key and str(key).strip().lower() not in ('null', 'none', ''):
                return str(key).strip()
    return None


# ═══════════════════════════════════════════════════════════════════
#  V8 新增检测函数（看板停滞、封驳循环、agentLog异常、redirect纠正）
# ═══════════════════════════════════════════════════════════════════

# agentLog 异常关键词（V8: 监察扫描）
_ANOMALY_KEYWORDS = {
    "ESCALATE": {"level": "error", "desc": "异常上报"},
    "ERROR": {"level": "error", "desc": "错误"},
    "HELP": {"level": "warning", "desc": "求助"},
    "STUCK": {"level": "warning", "desc": "卡住"},
    "FAIL": {"level": "error", "desc": "失败"},
    "BLOCKED": {"level": "warning", "desc": "被阻塞"},
}


def check_kanban_stall(kanban_data, task_id, task):
    """V8: 检测看板停滞（基于 last_activity 时间戳）。

    读取任务的 last_activity 字段，与当前时间比较。
    - 超过 STALE_WARNING_TIMEOUT → 记录 warning 级别的 auditFlags
    - 超过 STALE_ESCALATE_TIMEOUT → 记录 error 级别的 auditFlags

    Args:
        kanban_data: 看板完整数据
        task_id: 任务ID
        task: 任务对象

    Returns:
        str | None: 停滞描述，无停滞返回 None
    """
    if not _V8_MODULES_LOADED:
        return None
    task_state = task.get("state", "")
    if task_state in ("Done", "Cancelled", "archived"):
        return None

    last_activity_str = task.get("last_activity", "")
    if not last_activity_str:
        return None

    try:
        last_activity = datetime.datetime.fromisoformat(
            last_activity_str.replace("Z", "+00:00")
        )
        now = datetime.datetime.now(_BJT)
        elapsed = (now - last_activity).total_seconds()

        if elapsed >= STALE_ESCALATE_TIMEOUT:
            # 严重停滞：超过上报阈值
            agent_label = AGENT_LABELS.get(
                STATE_AGENT_MAP.get(task_state, ""), task_state
            )
            detail = (
                f"看板严重停滞：任务 {task_id} 在 {task_state}({agent_label}) 状态"
                f"已 {int(elapsed // 60)} 分钟无活动"
                f"（阈值 {STALE_ESCALATE_TIMEOUT // 60} 分钟），"
                f"编排引擎应已催办"
            )
            try:
                add_audit_flag(task_id, "stall_escalate", detail)
            except Exception:
                pass
            return detail
        elif elapsed >= STALE_WARNING_TIMEOUT:
            # 轻度停滞：超过警告阈值
            detail = (
                f"看板停滞警告：任务 {task_id} 在 {task_state} 状态"
                f"已 {int(elapsed // 60)} 分钟无活动"
                f"（阈值 {STALE_WARNING_TIMEOUT // 60} 分钟）"
            )
            try:
                add_audit_flag(task_id, "stall_warning", detail)
            except Exception:
                pass
            return detail
    except Exception:
        pass

    return None


def check_review_round_limit(kanban_data, task_id, task):
    """V8: 检测封驳循环（reviewRound >= MAX_REJECT_COUNT）。

    当门下省反复封驳超过最大次数时，建议强制准奏。

    Args:
        kanban_data: 看板完整数据
        task_id: 任务ID
        task: 任务对象

    Returns:
        str | None: 封驳循环描述，无异常返回 None
    """
    if not _V8_MODULES_LOADED:
        return None

    review_round = task.get("reviewRound", 0)
    if review_round >= MAX_REJECT_COUNT:
        detail = (
            f"封驳循环：reviewRound={review_round} >= {MAX_REJECT_COUNT}，"
            f"建议强制准奏。任务 {task_id} 已被门下省封驳 {review_round} 次，"
            f"中书省方案可能无法满足审议要求，建议太子介入或强制准奏。"
        )
        try:
            add_audit_flag(task_id, "reject_loop", detail)
        except Exception:
            pass
        return detail

    return None


def check_agent_log_anomalies(kanban_data, task_id, task):
    """V8: 扫描 agentLog 中的异常关键词。

    检测 ESCALATE、ERROR、HELP 等关键词，用于发现 Agent 的异常状态。

    Args:
        kanban_data: 看板完整数据
        task_id: 任务ID
        task: 任务对象

    Returns:
        list: 异常描述列表（每项为字符串），空列表表示正常
    """
    if not _V8_MODULES_LOADED:
        return []

    anomalies = []
    agent_log = task.get("agentLog", [])
    if not agent_log:
        return anomalies

    for entry in agent_log:
        text = entry.get("text", "")
        agent = entry.get("agent", "unknown")
        if not text:
            continue

        for keyword, info in _ANOMALY_KEYWORDS.items():
            if keyword in text.upper():
                # 避免重复报告同一个 agent 的同一个关键词
                anomaly_key = f"{agent}:{keyword}"
                if any(anomaly_key in a for a in anomalies):
                    continue
                anomalies.append(
                    f"[{info['level']}] {agent} agentLog包含关键词 {keyword}"
                    f"（{info['desc']}）: {text[:100]}"
                )

    if anomalies:
        try:
            add_audit_flag(
                task_id, "agent_log_anomaly",
                f"agentLog异常检测发现 {len(anomalies)} 项异常"
            )
        except Exception:
            pass

    return anomalies


def execute_redirect(task_id, to_agent, reason):
    """V8: 御史台纠正流转错误（通过 redirect 命令）。

    监察发现异常后，通过 redirect 命令将任务重定向到正确部门。
    编排引擎扫描到 redirect 消息后，会重新派发到目标 Agent。

    Args:
        task_id: 任务ID
        to_agent: 目标 Agent ID（如 "gongbu"、"zhongshu"）
        reason: 纠正原因（人类可读）

    Returns:
        bool: 是否成功执行 redirect
    """
    if not _V8_MODULES_LOADED:
        log(f"[V8兼容] execute_redirect: V8模块未加载，无法执行redirect")
        return False

    log(f"执行redirect: {task_id} → {to_agent} | 原因: {reason}")
    try:
        add_message(
            task_id, "redirect", "jiancha", to_agent, reason,
            {"action": "redirect", "reason": reason}
        )
        log_flow(task_id, "jiancha", to_agent, f"监察纠正: {reason}")
        log(f"redirect成功: {task_id} → {to_agent}")
        return True
    except Exception as e:
        log(f"redirect失败: {task_id} → {to_agent} | 错误: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
#  Gateway URL 解析（兼容 Docker bridge 端口映射）
# ═══════════════════════════════════════════════════════════════════

def _resolve_gateway_url():
    """解析 Gateway API 基础 URL，兼容 Docker bridge 端口映射场景。

    优先级：
    1. 环境变量 EDICT_GATEWAY_URL（最可靠，部署时显式配置）
    2. openclaw.json 中的 gateway.url 或 gateway.host+port
    3. 兜底 http://127.0.0.1:18789

    Docker bridge 注意事项：
    - openclaw.json 中 host 为 0.0.0.0 时，对内访问应替换为 127.0.0.1
    - 端口映射（如 -p 18900:18789）不影响容器内访问，内部端口 18789 仍有效
    - 跨容器通信需使用 Docker 网络别名或 host.docker.internal（非单容器场景）
    """
    # 1. 环境变量优先（部署时可显式配置）
    env_url = os.environ.get('EDICT_GATEWAY_URL', '').strip()
    if env_url:
        return env_url.rstrip('/')

    # 2. 从 openclaw.json 读取
    try:
        cfg = json.loads(OCLAW_HOME.joinpath('openclaw.json').read_text())
        gw = cfg.get('gateway', {})
        url = gw.get('url', '').strip()
        if url:
            return url.rstrip('/')
        host = gw.get('host', '127.0.0.1').strip()
        port = gw.get('port', 18789)
        # Docker bridge 下 0.0.0.0 不可作为目标地址，替换为 127.0.0.1
        if host in ('0.0.0.0', '0.0.0.0:', ''):
            host = '127.0.0.1'
        # 确保端口是整数（防止配置错误）
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 18789
        return f'http://{host}:{port}'
    except Exception:
        pass

    # 3. 最后兜底
    return 'http://127.0.0.1:18789'


def clear_agent_sessions(task):
    """通过 Gateway API 清理任务相关 Agent 的会话（仅清理非 main 会话，保留主会话上下文）。

    注意：不再自动调用此函数！仅在用户手动触发时使用。
    openclaw agent --agent xxx 默认使用 sessionKey agent:xxx:main 复用会话。
    清空主会话会导致 Agent 丢失所有历史上下文，引发无限循环。
    """
    import urllib.request
    import urllib.error

    # 读取 Gateway token
    gateway_cfg = OCLAW_HOME / "openclaw.json"
    token = ""
    try:
        cfg = json.loads(gateway_cfg.read_text())
        token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
    except Exception:
        pass

    flow_log = task.get("flow_log", [])
    agents_to_clear = set()
    for entry in flow_log:
        agent_id = normalize_name(entry.get("to", ""))
        if agent_id and agent_id not in ("huangshang", "皇上", "taizi"):
            agents_to_clear.add(agent_id)

    cleared_total = 0
    for agent_id in agents_to_clear:
        label = ID_TO_LABEL.get(agent_id, agent_id)
        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            # ── Gateway URL 解析（兼容 Docker bridge 端口映射）──
            _gw_base = os.environ.get('EDICT_GATEWAY_URL', '').strip()
            if not _gw_base:
                try:
                    _gw_cfg = json.loads(OCLAW_HOME.joinpath('openclaw.json').read_text())
                    _gw_gw = _gw_cfg.get('gateway', {})
                    _gw_url = _gw_gw.get('url', '').strip()
                    if _gw_url:
                        _gw_base = _gw_url.rstrip('/')
                    else:
                        _gw_host = _gw_gw.get('host', '127.0.0.1')
                        _gw_port = _gw_gw.get('port', 18789)
                        # Docker bridge 兼容：如果 host 配置为 0.0.0.0（Docker 默认），
                        # 在容器内应使用 127.0.0.1 访问自身映射的端口
                        if _gw_host in ('0.0.0.0', ''):
                            _gw_host = '127.0.0.1'
                        _gw_base = f'http://{_gw_host}:{_gw_port}'
                except Exception:
                    _gw_base = 'http://127.0.0.1:18789'

            # Docker bridge 二次兜底：如果配置的 URL 不可达，
            # 尝试 host.docker.internal（Docker Desktop）和 127.0.0.1（Linux Docker）
            _gw_reachable = False
            try:
                import urllib.request as _urllib_req
                _test_req = _urllib_req.Request(f"{_gw_base}/api/v1/conversations", headers=headers)
                _test_resp = _urllib_req.urlopen(_test_req, timeout=5)
                _gw_reachable = True
            except Exception:
                pass

            if not _gw_reachable:
                for _fallback_host in ['host.docker.internal', '127.0.0.1', 'localhost']:
                    if _fallback_host in _gw_base:
                        continue  # 已经试过了
                    # 从当前 base 提取端口
                    try:
                        from urllib.parse import urlparse as _urlparse
                        _parsed = _urlparse(_gw_base)
                        _port = _parsed.port or 18789
                        _fallback_url = f'http://{_fallback_host}:{_port}'
                        try:
                            import urllib.request as _urllib_req2
                            _fb_req = _urllib_req2.Request(f"{_fallback_url}/api/v1/conversations", headers=headers)
                            _urllib_req2.urlopen(_fb_req, timeout=5)
                            _gw_base = _fallback_url
                            _gw_reachable = True
                            log(f"Gateway 不可达，已切换到 Docker 兼容地址: {_fallback_url}")
                            break
                        except Exception:
                            continue
                    except Exception:
                        continue

            url = f"{_gw_base}/api/v1/conversations"
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            conversations = data if isinstance(data, list) else data.get("conversations", data.get("data", []))

            # 只删除非 main 会话（保留主会话上下文避免循环）
            for conv in conversations:
                conv_id = conv.get("id", "")
                title = conv.get("title", "")
                # main 会话的 title 通常包含 agent:xxx:main，保留它
                if "main" in str(conv_id).lower() or "main" in title.lower():
                    continue
                # 删除非 main 会话
                del_url = f"{_gw_base}/api/v1/conversations/{conv_id}"
                del_req = urllib.request.Request(del_url, method="DELETE", headers=headers)
                try:
                    urllib.request.urlopen(del_req, timeout=10)
                    cleared_total += 1
                except Exception:
                    pass

            log(f"已通过 Gateway API 清理 {label} ({agent_id}) 的非 main 会话")
        except Exception as e:
            log(f"通过 Gateway API 清理 {label} ({agent_id}) 会话失败: {e}")

    return cleared_total


# ═══════════════════════════════════════════════════════════════════
#  自动归档
# ═══════════════════════════════════════════════════════════════════

def auto_archive_done_tasks(tasks, now_iso):
    """自动归档 Done/Cancelled 超过阈值分钟且未归档的任务。

    同时清理归档任务的 _lastNotify 元数据（防止陈旧通知冷却数据残留）。
    """
    _archive_min = _cfg.get("auto_archive_minutes", AUTO_ARCHIVE_MINUTES)
    archived_count = 0
    archived_ids = []
    for t in tasks:
        state = t.get("state", "")
        if state not in ("Done", "Cancelled"):
            continue
        if t.get("archived"):
            continue
        # 检查 updatedAt 是否超过阈值
        updated_at = t.get("updatedAt", "")
        if not updated_at:
            continue
        try:
            dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00").replace("+08:00", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
            now = datetime.datetime.now(_BJT)
            if (now - dt).total_seconds() >= _archive_min * 60:
                t["archived"] = True
                t["archivedAt"] = now_iso
                # 清理陈旧通知冷却元数据，防止归档后的通知冷却数据干扰其他任务
                t.pop("_lastNotify", None)
                archived_count += 1
                archived_ids.append(t.get("id", ""))
        except Exception:
            continue
    if archived_count > 0:
        save_tasks(tasks)
        log(f"自动归档 {archived_count} 个已完成任务: {', '.join(archived_ids)}")
    return archived_count


# ═══════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════

def _is_recently_done(task):
    """判断任务是否在最近 N 分钟内完成（用于速通逃逸检测）"""
    updated_at = task.get("updatedAt", "")
    if not updated_at:
        return False
    try:
        dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00").replace("+08:00", ""))
        now = datetime.datetime.now(_BJT)
        _recent_min = _cfg.get("recent_done_minutes", RECENT_DONE_MINUTES)
        return (now - dt).total_seconds() < _recent_min * 60
    except Exception:
        return False


def _is_edict_task(task):
    """判断是否为旨意任务（JJC- 开头）"""
    task_id = task.get("id", "")
    return task_id.upper().startswith("JJC-")


# ── 单实例锁（防止多个 watchdog 进程同时运行导致审计数据混乱）──
_WATCHDOG_LOCK_FILE = REPO_DIR / "data" / ".pipeline_watchdog.pid"


def _acquire_watchdog_lock():
    """尝试获取 watchdog 单实例锁。返回 (lock_file, success)。"""
    try:
        lock_f = open(_WATCHDOG_LOCK_FILE, 'w')
        if _IS_WINDOWS:
            # Windows: 尝试获取排他锁，失败则说明已有实例
            try:
                msvcrt.locking(lock_f.fileno(), msvcrt.LK_NBLCK, 1)
            except (IOError, OSError):
                lock_f.close()
                return None, False
        else:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_f.write(str(os.getpid()))
        lock_f.flush()
        return lock_f, True
    except (IOError, OSError):
        return None, False


def _release_watchdog_lock(lock_f):
    """释放 watchdog 单实例锁。"""
    if lock_f:
        try:
            _unlock_file(lock_f)
            lock_f.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def main():
    # ── 单实例锁：防止多个 watchdog 同时运行 ──
    lock_f, locked = _acquire_watchdog_lock()
    if not locked:
        log("已有 watchdog 实例运行中，跳过本轮")
        return

    try:
        _main_inner()
    finally:
        _release_watchdog_lock(lock_f)


def _main_inner():
    """watchdog 主逻辑（在单实例锁保护下运行）。

    V8 架构：仅执行 V8 检测函数（kanban_stall, review_round_limit, agent_log_anomalies），
    不再进行事后审计。通知走 agent_notifier.py，本脚本只负责检测和记录。
    """
    # ── 加载配置 ──
    load_watchdog_config()

    # ── 提取配置到局部变量 ──
    _enabled = _cfg.get("enabled_checks", {})
    _max_arch_violations = _cfg.get("max_archived_violations", 500)
    _max_arch_notifs = _cfg.get("max_archived_notifications", 100)

    # ── 第一时间写入心跳，表明监察在运行（即使后续崩溃也能证明）──
    _write_heartbeat()

    tasks = load_tasks()
    if not tasks:
        # 即使没有任务也写入审计日志（标记监察在运行）
        audit = load_audit()
        now_iso = datetime.datetime.now(_BJT).isoformat()
        audit["last_check"] = now_iso
        audit["watched_tasks"] = []
        audit["watched_count"] = 0
        audit.setdefault("notifications", [])
        save_audit(audit)
        log("本轮检查完成，无任务")
        return

    now = datetime.datetime.now(_BJT)
    now_iso = now.isoformat()

    # 加载排除列表
    exclude_list = load_exclude_list()

    # ── 自动归档 Done 超过阈值分钟的任务 ──
    archived_count = auto_archive_done_tasks(tasks, now_iso)
    if archived_count > 0:
        # 记录归档动作到通报记录
        _arch_notif = _make_notif(
            notif_type="归档", to="系统",
            detail=f"自动归档 {archived_count} 个已完成任务",
        )
        audit = load_audit()
        audit.setdefault("notifications", [])
        audit["notifications"].append(_arch_notif)
        save_audit(audit)

    # 过滤需要检查的任务：
    #   1. 只监察 JJC- 开头的旨意任务（不监察对话）
    #   2. 排除手动排除的任务
    #   3. 活跃任务 + 最近完成的任务（防止速通逃逸）
    active = []
    for t in tasks:
        task_id = t.get("id", "")
        # 只监察旨意任务
        if not _is_edict_task(t):
            continue
        # 跳过手动排除的任务
        if task_id in exclude_list:
            continue
        state = t.get("state", "")
        if state not in ("Done", "Cancelled"):
            active.append(t)
        elif _is_recently_done(t):
            active.append(t)

    # 构建正在监察的任务列表（只含真正活跃的旨意任务，不含已完成的）
    truly_active = [
        t for t in tasks
        if t.get("state") not in ("Done", "Cancelled")
        and _is_edict_task(t)
        and t.get("id", "") not in exclude_list
    ]
    watched_tasks = []
    for t in truly_active:
        # 构造 session_keys 摘要（供前端展示）
        session_keys_raw = t.get('session_keys', {})
        session_keys_summary = {}
        for pair_key, key_entry in session_keys_raw.items():
            session_keys_summary[pair_key] = {
                "sessionKey": key_entry.get("sessionKey", ""),
                "savedAt": key_entry.get("savedAt", ""),
                "agents": key_entry.get("agents", []),
            }
        watched_tasks.append({
            "task_id": t.get("id", ""),
            "title": t.get("title", ""),
            "state": t.get("state", ""),
            "org": t.get("org", ""),
            "flow_count": len(t.get("flow_log", [])),
            "session_keys": session_keys_summary,
            "session_key_count": len(session_keys_summary),
        })

    # 加载审计日志
    audit = load_audit()
    audit.setdefault("notifications", [])

    if not active:
        audit["last_check"] = now_iso
        audit["watched_tasks"] = watched_tasks
        audit["watched_count"] = len(watched_tasks)
        save_audit(audit)
        log(f"本轮检查完成，{len(watched_tasks)} 个活跃任务均正常")
        return

    new_violations = []

    # ── 去重：构建已有违规 key 集合，避免每轮重复写入相同违规 ──
    existing_violation_keys = set()
    for v in audit.get("violations", []):
        v_key = (v.get("task_id", ""), v.get("type", ""), v.get("flow_index", -1), v.get("detail", ""))
        existing_violation_keys.add(v_key)

    # ── 清理已归档任务的陈旧违规 ──
    _active_task_ids = {t.get('id', '') for t in tasks if not t.get('archived')}
    _stale_violations = []
    for _v in audit.get("violations", []):
        _v_task = _v.get("task_id", "")
        if _v_task and _v_task not in _active_task_ids:
            _stale_violations.append(_v)
    if _stale_violations:
        _stale_count = len(_stale_violations)
        # 只保留仍属于非归档任务的违规
        audit["violations"] = [v for v in audit.get("violations", []) if v.get("task_id", "") in _active_task_ids or not v.get("task_id")]
        # 将清理的违规归档
        audit.setdefault("archived_violations", [])
        audit["archived_violations"].extend(_stale_violations)
        # 限制归档大小
        audit["archived_violations"] = audit["archived_violations"][-_max_arch_violations:]
        log(f"已清理 {_stale_count} 条已归档任务的陈旧违规")

    # ══════════════════════════════════════════════════════════════
    #  V8 检测循环
    # ══════════════════════════════════════════════════════════════
    for task in active:
        task_id = task.get("id", "?")
        title = task.get("title", "")

        # ── V8 检查 A：看板停滞检测（基于 last_activity 时间戳）──
        if _enabled.get("kanban_stall", True) and _V8_MODULES_LOADED:
            try:
                kanban_stall_detail = check_kanban_stall(tasks, task_id, task)
                if kanban_stall_detail:
                    log(f"[V8] 看板停滞检测: {task_id} | {kanban_stall_detail[:100]}")
            except Exception as e:
                log(f"[V8] 看板停滞检测异常: {task_id} | {e}")

        # ── V8 检查 B：封驳循环检测（reviewRound >= MAX_REJECT_COUNT）──
        if _enabled.get("review_round_limit", True) and _V8_MODULES_LOADED:
            try:
                reject_loop_detail = check_review_round_limit(tasks, task_id, task)
                if reject_loop_detail:
                    log(f"[V8] 封驳循环检测: {task_id} | {reject_loop_detail[:100]}")
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": "封驳循环",
                        "detail": reject_loop_detail,
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)
            except Exception as e:
                log(f"[V8] 封驳循环检测异常: {task_id} | {e}")

        # ── V8 检查 C：agentLog 异常关键词扫描 ──
        if _enabled.get("agent_log_anomalies", True) and _V8_MODULES_LOADED:
            try:
                anomaly_list = check_agent_log_anomalies(tasks, task_id, task)
                if anomaly_list:
                    log(f"[V8] agentLog异常检测: {task_id} | 发现 {len(anomaly_list)} 项异常")
                    for anomaly_detail in anomaly_list:
                        v_key = (task_id, "agentLog异常", -1, anomaly_detail)
                        if v_key not in existing_violation_keys:
                            violation = {
                                "task_id": task_id,
                                "title": title,
                                "type": "agentLog异常",
                                "detail": anomaly_detail,
                                "detected_at": now_iso,
                            }
                            new_violations.append(violation)
            except Exception as e:
                log(f"[V8] agentLog异常检测异常: {task_id} | {e}")

    # ── 写入审计日志（保留本轮通知，合并到最新数据）──
    # 保存本轮累积的通知（防止重读丢失）
    _loop_notifications = list(audit.get("notifications", []))

    audit = load_audit()  # 重新读取，获取可能被其他进程更新的数据

    # 合并本轮通知（去重，防止并发重复写入）
    _existing_notif_keys = set()
    for _n in audit.get("notifications", []):
        _n_key = (_n.get("sent_at", "") or _n.get("at", ""), _n.get("type", ""), _n.get("detail", ""))
        _existing_notif_keys.add(_n_key)

    for _n in _loop_notifications:
        _n_key = (_n.get("sent_at", "") or _n.get("at", ""), _n.get("type", ""), _n.get("detail", ""))
        if _n_key not in _existing_notif_keys:
            audit.setdefault("notifications", []).append(_n)
            _existing_notif_keys.add(_n_key)

    if new_violations:
        # 再次去重，防止并发时重复写入
        current_keys = set()
        for v in audit.get("violations", []):
            current_keys.add((v.get("task_id", ""), v.get("type", ""), v.get("flow_index", -1), v.get("detail", "")))
        for v in new_violations:
            v_key = (v.get("task_id", ""), v.get("type", ""), v.get("flow_index", -1), v.get("detail", ""))
            if v_key not in current_keys:
                audit.setdefault("violations", []).append(v)
                current_keys.add(v_key)

    # ── 归档已完成任务违规记录（转移到 archived_violations，而非删除）──
    archived_task_ids = set()
    for t in tasks:
        if t.get("archived") and t.get("state") in ("Done", "Cancelled"):
            archived_task_ids.add(t.get("id", ""))
    if archived_task_ids:
        old_violations = audit.get("violations", [])
        if old_violations:
            active_violations = []
            archived_new = []
            for v in old_violations:
                if v.get("task_id", "") in archived_task_ids:
                    archived_new.append(v)
                else:
                    active_violations.append(v)
            if archived_new:
                audit["violations"] = active_violations
                audit.setdefault("archived_violations", []).extend(archived_new)
                audit["archived_violations"] = audit["archived_violations"][-_max_arch_violations:]
                log(f"归档 {len(archived_new)} 条已归档任务的违规记录（保留在 archived_violations）")
        # 同时归档已归档任务的通知记录
        old_notifications = audit.get("notifications", [])
        if old_notifications:
            active_notifs = [
                n for n in old_notifications
                if n.get("task_id", "") not in archived_task_ids
                and not any(tid in archived_task_ids for tid in (n.get("task_ids") or []))
            ]
            archived_notifs = [
                n for n in old_notifications
                if n.get("task_id", "") in archived_task_ids
                or any(tid in archived_task_ids for tid in (n.get("task_ids") or []))
            ]
            if archived_notifs:
                audit.setdefault("archived_notifications", []).extend(archived_notifs)
                audit["archived_notifications"] = audit["archived_notifications"][-_max_arch_notifs:]
            audit["notifications"] = active_notifs

    # 更新审计元数据
    audit["last_check"] = now_iso
    audit["watched_tasks"] = watched_tasks
    audit["watched_count"] = len(watched_tasks)
    audit["check_count"] = audit.get("check_count", 0) + 1
    audit["total_violations"] = audit.get("total_violations", 0) + len(new_violations)

    # 记录本轮巡检摘要
    audit["notifications"].append(_make_notif(
        notif_type="巡检", to="系统",
        detail=f"检查完成，{len(active)} 个检查任务，{len(watched_tasks)} 个活跃，发现 {len(new_violations)} 项问题",
    ))

    save_audit(audit)

    active_count = len(active)
    watched_count = len(watched_tasks)
    violation_count = len(new_violations)
    log(f"本轮检查完成，检查 {active_count} 个任务（{watched_count} 活跃），发现 {violation_count} 项问题")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"监察脚本异常退出: {e}")
        # 确保即使崩溃也更新 last_check 并标记错误
        try:
            audit = load_audit()
            now_iso = datetime.datetime.now(_BJT).isoformat()
            audit["last_check"] = now_iso
            audit["error"] = str(e)[:500]
            save_audit(audit)
        except Exception:
            pass
