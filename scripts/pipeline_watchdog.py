#!/usr/bin/env python3
"""
三省六部 · 监察脚本 (pipeline_watchdog.py)

定期扫描 tasks_source.json，校验每个活跃任务的 flow_log 流程合法性。

检测三类问题：
  1. 越权调用 — from→to 不在合法流转对表内
  2. 流程跳步 — 标准链缺少必要环节
  3. 断链超时 — 最后一条 flow 的目标部门 1 分钟内无回应

处理方式：
  - 越权 → 写入审计日志 + 通知太子（会话消息）
  - 跳步 → 写入审计日志（不通知）
  - 断链/超时 → 唤醒目标部门 + 通知上级重新派发

过滤规则：
  - 只监察 JJC- 开头的旨意任务，不监察对话
  - 支持手动排除特定任务（audit_exclude.json）

额外功能：
  - 自动归档 Done 超过 5 分钟的任务
  - 所有通知记录写入 pipeline_audit.json 供前端展示

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

# ── 超时阈值（秒）─────────────────────────────────────────────────
# 【V5 修复】BREAK_TIMEOUT 从 90s → 210s（3.5 分钟）
# 根因：openclaw agent 启动 + Gateway 连接 + Agent 上下文加载 + LLM 推理
# 整个链路实测需要 2-3 分钟。旧值 90s 导致监察在 Agent 还没收到消息时就误报越权。
BREAK_TIMEOUT_SEC = 210  # 3.5 分钟无回应判定为断链（覆盖 sessions_spawn 投递延迟）
RECENT_DONE_MINUTES = 10  # 最近 N 分钟内完成的任务也需检查（防止速通逃逸）
AUTO_ARCHIVE_MINUTES = 5  # Done 超过 N 分钟自动归档

# ── 审议宽限期（秒）─────────────────────────────────────────────
# 门下省审议需要较长时间，在此期间不触发断链检测
REVIEW_GRACE_PERIODS = {
    "Menxia": 360,   # 门下省审议：6 分钟宽限期（审议本身需要充分思考时间）
}

# ── 极端停滞阈值 ─────────────────────────────────────────────────
EXTREME_STALL_THRESHOLD = 20 * 60  # 20分钟无任何更新视为极端停滞

# ── 多任务动态调整配置 ────────────────────────────────────────────
# 多任务并行时，中书省等关键节点需要处理多个任务，断链超时应适当放宽
# 避免因 Agent 正在处理其他任务而误判为断链
MULTITASK_BREAK_TIMEOUT = {     # 断链超时（秒）— 按活跃任务数阶梯递增
    1: 210,   # 单任务：210s（3.5分钟，覆盖 spawn 延迟）
    2: 270,   # 双任务：270s（+60s）
    3: 330,   # 三任务：330s（+120s）
    4: 390,   # 四任务：390s（+180s，上限）
}
MULTITASK_ACTIVITY_GRACE = {   # 看板活动宽限期（秒）— 第3层断链检测
    1: 300,   # 单任务：300s（5分钟，给 Agent 充足响应时间）
    2: 360,   # 双任务：360s（6分钟）
    3: 420,   # 三任务：420s（7分钟）
    4: 480,   # 四任务：480s（8分钟，上限）
}
MULTITASK_REVIEW_GRACE_SCALE = {  # 审议宽限期乘数
    1: 1.0,
    2: 1.3,
    3: 1.6,
    4: 2.0,
}

# ── 自适应配置 ────────────────────────────────────────────────────
# 运行时可覆盖的配置（由 _load_watchdog_config() 从 watchdog_config.json 加载）
_cfg = {
    "break_timeout_sec": BREAK_TIMEOUT_SEC,
    "auto_archive_minutes": AUTO_ARCHIVE_MINUTES,
    "recent_done_minutes": RECENT_DONE_MINUTES,
    "extreme_stall_threshold_sec": EXTREME_STALL_THRESHOLD,
    "review_grace_periods": dict(REVIEW_GRACE_PERIODS),
    "enabled_checks": {
        "illegal_flow": True,
        "skip_steps": True,
        "broken_chain": True,
        "cross_agent": True,
        "session_violation": True,
        "direct_execution": True,
        "extreme_stall": True,
        "liubu_evidence": True,  # 六部执行证据验证（假派发真越权检测）
    },
    "max_notifications": 200,
    "max_violations": 200,
    "max_archived_violations": 500,
    "max_archived_notifications": 100,
    "wake_retry_enabled": True,
    "adaptive_enabled": True,
    "stability_window": 20,  # 最近 N 轮检查用于计算稳定性
    "stability_threshold_high": 0.8,  # 高稳定性阈值（>此值减少通知）
    "adaptive_grace_boost_sec": 60,  # 自适应宽限期增量（秒）
    "multitask_enabled": True,  # 多任务动态调整开关
}


def load_watchdog_config():
    """从 data/watchdog_config.json 加载自适应配置，覆盖默认阈值。

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
        log(f"已加载自适应配置: break_timeout={_cfg['break_timeout_sec']}s, "
            f"auto_archive={_cfg['auto_archive_minutes']}min, "
            f"extreme_stall={_cfg['extreme_stall_threshold_sec']}s")
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
    # 「六部」不是具体部门名称，映射为特殊标记用于越权检测
    # 尚书省胡说使用「六部」泛称属于越权行为
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

# agent_id → 部门名称（用于 LEGAL_FLOWS / PARENT_MAP / REQUIRED_STEPS 匹配）
# normalize_name() 返回的是 agent_id，但 LEGAL_FLOWS 等表用的是部门名称
ID_TO_DEPT = {
    "huangshang": "皇上",
    "taizi":      "太子",
    "zhongshu":   "中书省",
    "menxia":     "门下省",
    "shangshu":   "尚书省",
    "gongbu":     "工部",
    "bingbu":     "兵部",
    "hubu":       "户部",
    "libu":       "礼部",
    "xingbu":     "刑部",
    "libu_hr":    "吏部",
}

# 合法流转对（基于标准链 + 实际业务需要）
# 规则：所有旨意必须走完整链路 皇上→太子→中书→门下→中书→尚书→六部
LEGAL_FLOWS = {
    # ── 上行：皇上→太子→中书→门下→尚书→六部 ──
    ("皇上",   "太子"),
    # ("皇上", "中书省") 已移除：旨意必须经过太子分拣
    ("太子",   "中书省"),
    ("中书省", "门下省"),
    ("门下省", "中书省"),   # 封驳退回 / 准奏后通知中书
    ("中书省", "尚书省"),   # 审批通过转交派发
    ("尚书省", "工部"),
    ("尚书省", "兵部"),
    ("尚书省", "户部"),
    ("尚书省", "礼部"),
    ("尚书省", "刑部"),
    ("尚书省", "吏部"),
    ("尚书省", "吏部_hr"),

      # ── 六部之间协作通信 ──
    ("工部",   "兵部"), ("兵部",   "工部"),
    ("工部",   "户部"), ("户部",   "工部"),
    ("工部",   "礼部"), ("礼部",   "工部"),
    ("工部",   "刑部"), ("刑部",   "工部"),
    ("工部",   "吏部"), ("吏部",   "工部"),
    ("兵部",   "户部"), ("户部",   "兵部"),
    ("兵部",   "礼部"), ("礼部",   "兵部"),
    ("兵部",   "刑部"), ("刑部",   "兵部"),
    ("兵部",   "吏部"), ("吏部",   "兵部"),
    ("户部",   "礼部"), ("礼部",   "户部"),
    ("户部",   "刑部"), ("刑部",   "户部"),
    ("户部",   "吏部"), ("吏部",   "户部"),
    ("礼部",   "刑部"), ("刑部",   "礼部"),
    ("礼部",   "吏部"), ("吏部",   "礼部"),
    ("刑部",   "吏部"), ("吏部",   "刑部"),
    # ── 吏部_hr 参与六部协作 ──
    ("吏部_hr", "工部"), ("工部",   "吏部_hr"),
    ("吏部_hr", "兵部"), ("兵部",   "吏部_hr"),
    ("吏部_hr", "户部"), ("户部",   "吏部_hr"),
    ("吏部_hr", "礼部"), ("礼部",   "吏部_hr"),
    ("吏部_hr", "刑部"), ("刑部",   "吏部_hr"),
  

    # ── 下行：六部→尚书→中书→太子→皇上 ──
    ("工部",   "尚书省"),
    ("兵部",   "尚书省"),
    ("户部",   "尚书省"),
    ("礼部",   "尚书省"),
    ("刑部",   "尚书省"),
    ("吏部",   "尚书省"),
    ("吏部_hr", "尚书省"),
    ("尚书省", "中书省"),   # 汇总返回
    ("中书省", "太子"),     # 回奏
    ("太子",   "皇上"),     # 汇报皇上
    ("尚书省", "太子"),      # 汇报太子

    # ── 省部内部消息（自己给自己发内部处理消息）──
    ("中书省", "中书省"),   # 中书省内部处理（收准奏/方案修订等）
    ("门下省", "门下省"),   # 门下省内部审议
    ("尚书省", "尚书省"),   # 尚书省内部汇总/调度
    ("太子",   "太子"),     # 太子内部消息
    ("工部",   "工部"),
    ("兵部",   "兵部"),
    ("户部",   "户部"),
    ("礼部",   "礼部"),
    ("刑部",   "刑部"),
    ("吏部",   "吏部"),
    ("吏部_hr", "吏部_hr"),

    # ── 太子调度系统（flow_log from='太子调度'）──
    ("太子调度", "中书省"),
    ("太子调度", "门下省"),
    ("太子调度", "尚书省"),
    ("太子调度", "工部"),
    ("太子调度", "兵部"),
    ("太子调度", "户部"),
    ("太子调度", "礼部"),
    ("太子调度", "刑部"),
    ("太子调度", "吏部"),
    ("太子调度", "吏部_hr"),
    ("太子调度", "太子"),     # 太子调度也可发给太子自己
}

# 部门 → 直接上级（断链时需要通知上级）
# 【V7 修复】尚书省的上级是中书省（不是太子！），旧代码重复定义导致覆盖
# 根因：Python dict 同一个 key 出现两次时，后者覆盖前者。
# 旧代码先写 "尚书省":"中书省" 再写 "尚书省":"太子" → 实际存储的是 "太子"。
# 后果：断链时监察通知太子而非中书省 → 中书省不知道要催尚书省 → 恢复链断裂。
PARENT_MAP = {
    "中书省": "太子",
    "门下省": "中书省",
    "尚书省": "中书省",     # 尚书省的直接上级是中书省（V7 修复：去重）
    "工部":   "尚书省",
    "兵部":   "尚书省",
    "户部":   "尚书省",
    "礼部":   "尚书省",
    "刑部":   "尚书省",
    "吏部":   "尚书省",
    "吏部_hr": "尚书省",
}

# 标准流转链必须经过的环节（按顺序），用于跳步检测
# 包含上行（皇上下旨）和下行（六部回奏）完整链路
REQUIRED_STEPS = [
    # ── 上行：皇上下旨 ──
    ("太子", "中书省"),
    ("中书省", "门下省"),
    ("门下省", "中书省"),   # 门下省准奏/封驳后返回中书省
    ("中书省", "尚书省"),   # 中书省必须转交尚书省派发
    # ── 下行：六部回奏 ──
    ("尚书省", "中书省"),   # 尚书省汇总六部成果后返回中书省
    ("中书省", "太子"),     # 中书省回奏太子
    ("太子", "皇上"),       # 太子汇报皇上（必须！标记 Done 前必须有此步）
]

# 六部名称集合（用于直接执行越权检测）
LIU_BU_DEPTS = {"工部", "兵部", "户部", "礼部", "刑部", "吏部", "吏部_hr"}
# 三省名称集合
SAN_SHENG_DEPTS = {"中书省", "门下省", "尚书省"}


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
    """安全读取 tasks_source.json（带文件锁）"""
    return atomic_json_read(TASKS_FILE, [])


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
    """写入任务文件（带文件锁 + 原子写入）"""
    atomic_json_write(TASKS_FILE, tasks)


# ═══════════════════════════════════════════════════════════════════
#  Agent 唤醒与通知
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


def wake_agent(agent_id, reason=""):
    """唤醒指定 Agent。返回 (success, detail)。
    
    【关键修复】使用 openclaw agent（非 sessions spawn）唤醒 Agent。
    
    根因分析：
    - openclaw sessions spawn：仅创建会话记录，不触发 Agent LLM 处理，Agent 不会醒来
    - openclaw agent：直接向 Agent 发送消息，触发 LLM 处理，Agent 真正被唤醒
    
    dashboard/server.py 的 wake_agent 一直使用 openclaw agent，所以 scheduler-scan
    的 10 分钟心跳催办能正常唤醒中书省。但 pipeline_watchdog.py 和 kanban_update.py
    之前用的是 sessions spawn，导致所有程序层唤醒全部无效。
    """
    if agent_id in ("huangshang", "皇上"):
        return False, "不唤醒皇上"
    label = ID_TO_LABEL.get(agent_id, agent_id)
    msg = (
        f"🔔 监察心跳通知\n"
        f"原因: {reason or '流程断链，需要你恢复在线'}\n"
        f"时间: {datetime.datetime.now(_BJT).isoformat()}\n"
        f"请确认在线并继续处理待办任务。"
    )
    try:
        # ── 使用 openclaw agent 唤醒（与 dashboard/server.py 一致）──
        result = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id, "-m", msg, "--timeout", "120"],
            capture_output=True, text=True, timeout=130,
        )
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        
        if result.returncode == 0:
            log(f"已唤醒 {label} ({agent_id}) [openclaw agent 成功]")
            # 异步验证：30秒后在后台线程中确认 Agent 已活跃
            def _verify_agent():
                time.sleep(30)
                if not is_agent_awake(agent_id):
                    log(f"{label} ({agent_id}) 唤醒后30秒仍无活动，尝试二次唤醒")
                    try:
                        result2 = subprocess.run(
                            ["openclaw", "agent", "--agent", agent_id, "-m", msg, "--timeout", "120"],
                            capture_output=True, text=True, timeout=130,
                        )
                        if result2.returncode == 0:
                            log(f"已二次唤醒 {label} ({agent_id}) [成功]")
                        else:
                            err2 = (result2.stderr or "").strip()
                            log(f"二次唤醒 {label} ({agent_id}) 失败: rc={result2.returncode} | stderr: {err2[:200]}")
                    except Exception as e2:
                        log(f"二次唤醒 {label} ({agent_id}) 异常: {e2}")
                else:
                    log(f"{label} ({agent_id}) 唤醒后已活跃")
            threading.Thread(target=_verify_agent, daemon=True).start()
            return True, f"已向 {label} 发送唤醒消息 [openclaw agent 成功]"
        else:
            # 同步失败，记录详细错误
            log(f"唤醒 {label} ({agent_id}) 失败: rc={result.returncode} | stdout: {output[:200]} | stderr: {error[:200]}")
            # 异步重试一次（不阻塞主循环）
            def _retry_wake():
                time.sleep(5)
                try:
                    retry_result = subprocess.run(
                        ["openclaw", "agent", "--agent", agent_id, "-m", msg, "--timeout", "120"],
                        capture_output=True, text=True, timeout=130,
                    )
                    retry_err = (retry_result.stderr or "").strip()
                    if retry_result.returncode == 0:
                        log(f"唤醒重试 {label} ({agent_id}) 成功")
                    else:
                        log(f"唤醒重试 {label} ({agent_id}) 仍失败: rc={retry_result.returncode} | stderr: {retry_err[:200]}")
                except Exception as e2:
                    log(f"唤醒重试 {label} ({agent_id}) 异常: {e2}")
            threading.Thread(target=_retry_wake, daemon=True).start()
            return False, f"唤醒失败: rc={result.returncode} | {error[:100]}"
    except subprocess.TimeoutExpired:
        log(f"唤醒 {label} ({agent_id}) 超时(130s)")
        return False, "命令执行超时(130s)"
    except Exception as e:
        log(f"唤醒 {label} ({agent_id}) 异常: {e}")
        return False, str(e)[:200]


def notify_agent(agent_id, message):
    """向指定 Agent 同步发送通知消息。返回 (success, detail)。
    
    【关键修复】使用 openclaw agent（非 sessions spawn）通知 Agent。
    openclaw sessions spawn 只创建会话但不触发 LLM 处理，Agent 不会收到消息。
    openclaw agent 直接触发 Agent 的 LLM 处理管道，确保消息被接收。
    """
    if agent_id in ("huangshang", "皇上"):
        return False, "不通知皇上"
    label = ID_TO_LABEL.get(agent_id, agent_id)
    try:
        result = subprocess.run(
            ["openclaw", "agent", "--agent", agent_id, "-m", message, "--timeout", "120"],
            capture_output=True, text=True, timeout=130
        )
        success = result.returncode == 0
        detail = (result.stdout + "\n" + result.stderr).strip()[:500]
        if success:
            log(f"通知 {label} 成功 [openclaw agent]: {detail[:200]}")
        else:
            log(f"通知 {label} 失败: rc={result.returncode} | stdout: {(result.stdout or '')[:200]} | stderr: {(result.stderr or '')[:200]}")
        return success, detail
    except subprocess.TimeoutExpired:
        log(f"通知 {label} ({agent_id}) 超时(130s)")
        return False, "命令执行超时(130s)"
    except Exception as e:
        log(f"通知 {label} ({agent_id}) 异常: {e}")
        return False, str(e)[:200]


def is_agent_awake(agent_id):
    """检查 Agent 是否醒着。
    
    修复：增加多维度检测，不再仅依赖文件修改时间。
    检测维度：
    1. sessions 目录下最近 3 分钟内有文件活动（原逻辑）
    2. openclaw sessions list 命令检查 agent 是否有活跃会话
    """
    if agent_id in ("huangshang", "皇上"):
        return True
    sessions_dir = OCLAW_HOME / "agents" / agent_id / "sessions"
    if not sessions_dir.exists():
        log(f"Agent {agent_id} 的 sessions 目录不存在: {sessions_dir}")
        return False
    cutoff = time.time() - 180  # 3 分钟
    try:
        for f in sessions_dir.iterdir():
            if f.is_file() and f.stat().st_mtime > cutoff:
                return True
    except Exception:
        pass
    # 补充检测：尝试通过 openclaw CLI 查询活跃会话
    try:
        check_result = subprocess.run(
            ["openclaw", "sessions", "list", "--agent", agent_id],
            capture_output=True, text=True, timeout=15,
        )
        if check_result.returncode == 0 and check_result.stdout.strip():
            # CLI 返回了会话列表，说明 agent 有注册的会话
            return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════════════
#  流程校验核心逻辑
# ═══════════════════════════════════════════════════════════════════

def check_illegal_flow(task_id, flow_from, flow_to, index):
    """检查单条 flow 是否合法（越权检测）。返回违规描述或 None。"""
    # 特殊检测：使用「六部」这个泛称属于越权（优先于其他检测）
    # normalize_name 会将「六部」映射为 _liubu_invalid，需在此拦截
    if flow_to == '_liubu_invalid' or flow_from == '_liubu_invalid':
        dept_from = ID_TO_DEPT.get(flow_from, flow_from)
        dept_to = ID_TO_DEPT.get(flow_to, flow_to)
        return (
            f"越权调用：{dept_from} → {dept_to}。「六部」不是有效的部门名称，"
            f"必须使用具体部名：工部、兵部、户部、礼部、刑部、吏部之一。"
            f"使用泛称「六部」属于尚书省越权行为。"
        )
    # flow_from/flow_to 是 agent_id（来自 normalize_name），需转为部门名匹配 LEGAL_FLOWS
    dept_from = ID_TO_DEPT.get(flow_from, flow_from)
    dept_to = ID_TO_DEPT.get(flow_to, flow_to)
    pair = (dept_from, dept_to)
    if pair in LEGAL_FLOWS:
        return None
    # 兜底检测：原始中文名是否包含「六部」
    if '六部' in dept_to or '六部' in dept_from:
        return (
            f"越权调用：{dept_from} → {dept_to}。「六部」不是有效的部门名称，"
            f"必须使用具体部名：工部、兵部、户部、礼部、刑部、吏部之一。"
            f"使用泛称「六部」属于尚书省越权行为。"
        )
    return (
        f"越权调用：{dept_from} → {dept_to}（不在合法流转对表内）。"
        f"合法的上游调用链为：太子→中书省→门下省→中书省→尚书省→六部"
    )


def check_skip_steps(task_id, flow_log):
    """检查整个 flow_log 是否跳步（缺少必要环节）。
    改进：增加后向检查 — 如果后续步骤已出现但中间步骤缺失，也报违规。
    返回违规列表。"""
    violations = []
    # 从 flow_log 中提取所有 (from, to) 对，转为部门名称
    pairs = set()
    for entry in flow_log:
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if f and t:
            pf = ID_TO_DEPT.get(f, f)
            pt = ID_TO_DEPT.get(t, t)
            pairs.add((pf, pt))

    # 前向检查：找到流程当前已到达的位置
    last_reached_index = -1
    for i, step in enumerate(REQUIRED_STEPS):
        if step in pairs:
            last_reached_index = i

    # 只检查已到达位置及之前的步骤
    for i in range(last_reached_index + 1):
        req_from, req_to = REQUIRED_STEPS[i]
        if (req_from, req_to) not in pairs:
            violations.append(f"流程跳步：缺少必要环节 {req_from} → {req_to}")

    # 后向检查：如果后续步骤已出现但中间步骤缺失
    # 例如：尚书省→中书省 出现了（step 4），但 step 2（门下→中书）缺失
    future_steps_seen = set()
    for i in range(last_reached_index + 1, len(REQUIRED_STEPS)):
        if REQUIRED_STEPS[i] in pairs:
            future_steps_seen.add(i)

    for future_i in future_steps_seen:
        # 检查 future_i 之前的所有必需步骤是否存在
        for i in range(future_i):
            if REQUIRED_STEPS[i] not in pairs:
                req_from, req_to = REQUIRED_STEPS[i]
                skip_msg = f"流程跳步：{REQUIRED_STEPS[future_i][0]}→{REQUIRED_STEPS[future_i][1]} 已执行，但缺少前置环节 {req_from} → {req_to}"
                if skip_msg not in violations:
                    violations.append(skip_msg)

    return violations


def check_direct_execution(task_id, flow_log, task_state=""):
    """检查三省（中书/门下/尚书）是否直接执行了六部的工作。
    
    检测逻辑（增强版 — 五维证据验证）：
    1. 任务已完成（Done）但 flow_log 中没有任何六部实际执行证据
    2. 六部实际执行证据 = 六部作为 from 出现在 flow_log 中（不是只作为 to）
    3. 或者：三省的 flow_log remark 中包含执行产出类关键词但没有六部参与
    
    注意：旧版只检查六部是否出现在 flow_log 的任何位置（包括作为 to），
    这会被尚书省写一条假 flow 骗过。新版要求六部必须作为 from 出现
    （证明六部主动发出了回复），才算有执行证据。
    
    返回违规描述或 None。
    """
    # 只检查已到达尚书省及之后阶段的任务
    has_shangshu = False
    has_liubu_as_target = False  # 六部是否作为 to 出现（被派发）
    has_liubu_response = False   # 六部是否作为 from 出现（实际回复了）
    depts_involved = set()
    
    for entry in flow_log:
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if f:
            dept_f = ID_TO_DEPT.get(f, f)
            dept_t = ID_TO_DEPT.get(t, t) if t else ""
            depts_involved.add(dept_f)
            if dept_t:
                depts_involved.add(dept_t)
        if t:
            dept_t = ID_TO_DEPT.get(t, t)
            if dept_t == "尚书省" or f == "shangshu":
                has_shangshu = True
            # 六部作为 to（被派发目标）
            if dept_t in LIU_BU_DEPTS or t in LIU_BU_AGENT_IDS:
                has_liubu_as_target = True
        # 六部作为 from（实际发出了回复 — 真正的执行证据）
        if f:
            dept_f = ID_TO_DEPT.get(f, f)
            if dept_f in LIU_BU_DEPTS or f in LIU_BU_AGENT_IDS:
                has_liubu_response = True
    
    # 如果任务已完成，有尚书省参与，且六部没有任何实际回复
    # 即使六部作为 to 出现（被派发），但没有 as from 回复 → 尚书省代劳了
    if task_state == "Done" and has_shangshu and not has_liubu_response:
        if has_liubu_as_target:
            # 六部被派发了但没有回复 → 假派发
            return (
                "直接执行越权：任务已完成，flow_log 中尚书省声称派发了六部，"
                "但六部没有任何回复记录。尚书省可能写了假派发 flow 后自行代劳。"
                "具体执行必须由六部完成，六部必须有 flow 回复、session 或进展记录。"
            )
        else:
            # 六部根本没有被派发
            return (
                "直接执行越权：任务已完成，但整个流程中没有任何六部参与执行。"
                "中书省、门下省、尚书省只能规划/审议/派发，具体执行必须由六部完成。"
            )
    
    # 如果尚书省参与了但没有六部实际回复，且任务已推进到较后阶段
    # 【V6 修复】仅对 Done 状态严格判定，非Done状态增加检查
    # 根因：Doing/Review 阶段六部可能正在执行，尚书省已派发但六部回复还没写入，
    # 旧代码对非Done状态也报"疑似越权"，导致误报。
    if has_shangshu and not has_liubu_response and task_state not in ("", "Pending", "Taizi", "Zhongshu", "Assigned", "Doing", "Next"):
        return (
            "疑似直接执行越权：流程已到达尚书省，但六部没有任何执行回复记录。"
            "尚书省收到门下省准奏方案后，必须派发给六部执行，不可自行代劳。"
        )
    
    return None


# ═══════════════════════════════════════════════════════════════════
#  六部执行证据验证（假派发真越权检测）
# ═══════════════════════════════════════════════════════════════════
# 问题场景：
#   15:44:19 尚书省→礼部   派发：撰写正式内部公告
#   15:45:18 尚书省→中书省  尚书省回奏（仅59秒后，礼部完全没有执行痕迹）
#
# 根因：现有 check_direct_execution 只检查六部是否出现在 flow_log 的任何位置
# （包括作为 to 被派发），不检查六部是否实际执行了工作。尚书省写一条
# "尚书省→礼部"的 flow 就能骗过检查，然后自己代劳六部任务。
#
# 本检测用五维证据验证六部是否真正执行了工作：
#   1. flow_log 中六部作为 from 出现（六部主动发出了回复）
#   2. session_keys 中有该六部 agent 的 session key（说明真正建立了会话）
#   3. progress_log 中有六部的进展记录
#   4. 任务的 org 字段已切换到六部名称（程序级状态 Doing 被触发）
#   5. activeAgent 字段指向六部 agent_id（程序级活跃标记）
#
# 以上五项全部为空 → 判定为"假派发真越权"。

# 六部 agent_id 集合（与 LIU_BU_DEPTS 对应）
LIU_BU_AGENT_IDS = {"gongbu", "bingbu", "hubu", "libu", "xingbu", "libu_hr"}

def check_liubu_execution_evidence(task_id, flow_log, session_keys=None,
                                     progress_log=None, task_state="",
                                     task_org="", task_active_agent=""):
    """检查尚书省→六部派发后，六部是否有实际执行证据（五维验证）。

    检测逻辑：
    1. 在 flow_log 中找到所有 尚书省→六部 的派发记录
    2. 对每条派发记录，检查五维证据中是否至少有一项证明六部执行了工作
    3. 如果尚书省已经推进到回奏环节（尚书省→中书省），但六部零证据 → 假派发越权

    Args:
        flow_log: 任务的 flow_log 列表
        session_keys: 任务的 session_keys 字典
        progress_log: 任务的 progress_log 列表
        task_state: 当前任务状态
        task_org: 当前任务 org 字段（如 "礼部"）
        task_active_agent: 当前任务 activeAgent 字段

    Returns:
        list: 违规描述列表（每项代表一个被跳过的六部），空列表表示正常
    """
    violations = []
    if not flow_log:
        return violations

    # 已归档/终态不检测（这些任务的六部执行已结束，证据可能被清理）
    # 但最近完成的任务仍需检测（防止速通逃逸）
    if task_state in ("Cancelled",):
        return violations

    # ── Step 1: 找所有 尚书省→六部 的派发记录 ──
    dispatch_records = []  # [(flow_index, target_agent_id, target_label, dispatch_time_str)]
    for i, entry in enumerate(flow_log):
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if f == "shangshu" and t in LIU_BU_AGENT_IDS:
            t_label = ID_TO_DEPT.get(t, t)
            dispatch_records.append((i, t, t_label, entry.get("at", "")))

    if not dispatch_records:
        return violations  # 没有派发给六部的记录

    # ── Step 2: 检查尚书省是否已经推进到回奏环节 ──
    # 如果尚书省还没有回奏（尚书省→中书省），可能六部还在执行中，暂不判定
    shangshu_returned = False
    shangshu_return_idx = -1
    for i, entry in enumerate(flow_log):
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if f == "shangshu" and t == "zhongshu":
            shangshu_returned = True
            shangshu_return_idx = i
            break

    if not shangshu_returned:
        # 尚书省还没回奏，但任务已经 Done（可能是自动补全），也需检测
        if task_state != "Done":
            return violations  # 六部可能还在执行中，暂不判定

    # ── Step 3: 对每个被派发的六部进行五维证据验证 ──
    for dispatch_idx, target_agent, target_label, dispatch_time in dispatch_records:
        evidence_found = []
        evidence_labels = []

        # 【V6 修复】时间宽限期：派发后 210 秒内不判违规
        # 根因：openclaw agent 启动 + Gateway连接 + Agent上下文加载 + LLM推理
        # 整个链路需要 2-3 分钟。旧代码不考虑时间，派发后立即检测导致误报。
        _LIUBU_EVIDENCE_GRACE_SEC = 210  # 3.5分钟宽限期
        if dispatch_time:
            try:
                _dt = datetime.datetime.fromisoformat(dispatch_time.replace("Z", "+00:00"))
                _elapsed = (datetime.datetime.now(_BJT) - _dt).total_seconds()
                if _elapsed < _LIUBU_EVIDENCE_GRACE_SEC:
                    # 派发时间太短，六部可能还在启动中，暂不判定
                    continue
            except Exception:
                pass

        # 维度 1: flow_log 中六部作为 from 出现（在派发之后）
        for j, entry in enumerate(flow_log):
            if j <= dispatch_idx:
                continue
            f = normalize_name(entry.get("from", ""))
            if f == target_agent:
                evidence_found.append("flow_reply")
                evidence_labels.append(f"flow_log 中有 {target_label}→尚书省 的回复记录")
                break

        # 维度 2: session_keys 中有该六部 agent 的 session key
        if not evidence_found and session_keys:
            for pair_key, key_entry in session_keys.items():
                agents = [a.strip().lower() for a in key_entry.get("agents", [])]
                if target_agent in agents:
                    evidence_found.append("session_key")
                    evidence_labels.append(f"session_keys 中有 {target_label} 的会话密钥")
                    break

        # 维度 3: progress_log 中有六部的进展记录
        if not evidence_found and progress_log:
            for p in progress_log:
                # progress_log 条目可能有 from、dept、agent 等字段
                p_from = (p.get("from", "") or "").strip().lower()
                p_dept = (p.get("dept", "") or "").strip().lower()
                p_agent = (p.get("agent", "") or "").strip().lower()
                if any(x == target_agent for x in [p_from, p_dept, p_agent]):
                    evidence_found.append("progress_log")
                    evidence_labels.append(f"progress_log 中有 {target_label} 的进展记录")
                    break

        # 维度 4: 任务的 org 字段已切换到六部名称（程序级状态 Doing 被触发过）
        if not evidence_found and task_org:
            org_agent = _ORG_TO_AGENT_ID.get(task_org.strip(), "")
            if org_agent == target_agent:
                evidence_found.append("task_org")
                evidence_labels.append(f"任务 org 字段为 {target_label}（程序级 Doing 状态已触发）")

        # 维度 5: activeAgent 字段指向六部 agent_id
        if not evidence_found and task_active_agent:
            if task_active_agent.strip().lower() == target_agent:
                evidence_found.append("active_agent")
                evidence_labels.append(f"任务 activeAgent 字段指向 {target_label}")

        # ── Step 4: 五维全部为空 → 假派发真越权 ──
        if not evidence_found:
            violations.append({
                "target_agent": target_agent,
                "target_label": target_label,
                "dispatch_time": dispatch_time,
                "dispatch_idx": dispatch_idx,
                "detail": (
                    f"假派发越权：尚书省声称已于 {dispatch_time} 派发至{target_label}，"
                    f"但{target_label}没有任何执行证据。"
                    f"五维验证全部为空："
                    f"无flow_log回复、无session_key、无进展记录、"
                    f"任务org未指向{target_label}、activeAgent未指向{target_label}。"
                    f"尚书省已回奏中书省（跳过了六部直接代劳），属于严重越权行为。"
                ),
            })

    return violations


# ── 部门中文名 → agent_id 映射（用于 check_liubu_execution_evidence 维度4）──
_ORG_TO_AGENT_ID = {}
for _aid, _dept in ID_TO_DEPT.items():
    if _aid in LIU_BU_AGENT_IDS or _dept in LIU_BU_DEPTS:
        _ORG_TO_AGENT_ID[_dept] = _aid
        # 也添加 agent_id → agent_id（self-mapping，用于 org 字段可能是 agent_id 的情况）
        _ORG_TO_AGENT_ID[_aid] = _aid


def check_broken_chain(task_id, flow_log, task_state="",
                        progress_log=None, task_updated_at="",
                        break_timeout=None, review_grace=None,
                        active_task_count=1):
    """检查最后一条 flow 是否断链（目标部门无回应）。返回断链信息或 None。

    判断"回应"的依据（按优先级递进）：
    1. flow_log 中目标部门作为 from 出现（已流转到下一步 → 正常回应）
    2. progress_log 中目标部门有进展记录（正在工作 → 不算断链）
    3. task.updatedAt 在最后 flow 之后被刷新（看板有活动 → 6 分钟内不断链）
    以上均不满足且超过阈值 → 判定为断链，触发提醒。

    Args:
        progress_log: 任务的 progress_log 列表（看板进展记录）
        task_updated_at: 任务的 updatedAt 时间戳（看板最后刷新时间）
    """
    if not flow_log:
        return None

    _timeout = break_timeout if break_timeout is not None else BREAK_TIMEOUT_SEC
    _grace = review_grace if review_grace is not None else REVIEW_GRACE_PERIODS

    last = flow_log[-1]
    last_to = normalize_name(last.get("to", ""))
    last_at_str = last.get("at", "")

    # 不检查终点为皇上或太子的（太子以上的不由监察处理）
    if not last_to or last_to in ("huangshang", "皇上", "taizi"):
        return None

    # ── 审议宽限期：如果任务处于审议状态，给更长的等待时间 ──
    dept_to = ID_TO_DEPT.get(last_to, last_to)
    grace_period = _grace.get(task_state, 0) or _grace.get(dept_to, 0)

    # 解析最后一条 flow 的时间
    try:
        last_at = datetime.datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
    except Exception:
        return None

    now = datetime.datetime.now(_BJT)
    elapsed = (now - last_at).total_seconds()

    if elapsed < _timeout:
        return None  # 还没超时

    # 审议宽限期内不触发断链
    if grace_period and elapsed < grace_period:
        return None

    # ── 第 1 层：检查目标部门是否有后续 flow 记录（作为 from 出现）──
    target_has_response = False
    for entry in flow_log:
        entry_from = normalize_name(entry.get("from", ""))
        entry_at_str = entry.get("at", "")
        if entry_from == last_to and entry_at_str:
            try:
                entry_at = datetime.datetime.fromisoformat(entry_at_str.replace("Z", "+00:00"))
                if entry_at > last_at:
                    target_has_response = True
                    break
            except Exception:
                continue

    if target_has_response:
        return None  # 已经流转到下一步，正常回应

    # ── 第 2 层：检查 progress_log 判断目标部门是否正在工作 ──
    # Agent 在处理任务时会通过 progress 命令更新进展，这会写入 progress_log。
    # 如果目标部门有在最后 flow 之后的进展记录，说明该部门正在工作，不算断链。
    #
    # ── BUG FIX #2: 原代码使用 p_org（任务所属部门）匹配目标部门，但 p_org 是
    #    task.org 的副本，表示"任务当前挂在哪个部门名下"，而非"谁在写这条 progress"。
    #    当太子为中书省的任务写 progress 时，p_org = "中书省" 会匹配 last_to = "zhongshu"，
    #    导致误判为"目标部门正在工作"，断链永远不被检测到。
    #    修复：使用 p_agent（实际写入进展的 Agent）作为主匹配条件，p_org 作为辅助参考，
    #    只有当 p_agent 明确指向目标部门时才判定为活跃。
    if progress_log:
        for p_entry in progress_log:
            p_at_str = p_entry.get("at", "")
            p_agent = normalize_name(str(p_entry.get("agent", "")).lower())
            p_org = normalize_name(str(p_entry.get("org", "")).lower())
            if not p_at_str:
                continue
            # 以 p_agent 作为主匹配条件（实际写入者），p_org 不再单独用于判定活跃
            if p_agent == last_to:
                try:
                    p_at = datetime.datetime.fromisoformat(p_at_str.replace("Z", "+00:00"))
                    if p_at > last_at:
                        return None  # 目标部门自身有进展记录，正在工作中
                except Exception:
                    continue

    # ── 第 3 层：检查 task.updatedAt 是否在最后 flow 之后被刷新 ──
    # 任何对看板的写操作（progress/flow/state）都会刷新 updatedAt。
    # 如果 updatedAt 被刷新了但 6 分钟内没有新的 flow，仍视为活跃（不断链）。
    # 超过 6 分钟则说明虽有看板活动但流程未推进，此时才视为断链。
    #
    # ── BUG FIX #2 (补充): 将宽限期从 360s 缩短至 180s，并增加目标部门刷新检查。
    #    原宽限期 360s（6分钟）过长，导致断链需要等 6 分钟以上才能被检测到。
    #    同时，仅检查 updatedAt 不够，需确认目标部门确实有活动。
    # ── 多任务动态调整：看板活动宽限期 ──
    # 多任务并行时 Agent 处理速度变慢，活动宽限期按活跃任务数动态延长
    _ACTIVITY_GRACE_SEC = 180  # 单任务默认值
    if active_task_count and active_task_count >= 2:
        # 按阶梯取值，超过4个任务取最大值
        _ACTIVITY_GRACE_SEC = MULTITASK_ACTIVITY_GRACE.get(
            min(active_task_count, 4),
            360  # 兜底上限
        )
    if task_updated_at:
        try:
            upd_str = task_updated_at.replace("Z", "+00:00").replace("+08:00", "")
            upd_at = datetime.datetime.fromisoformat(upd_str)
            if upd_at.tzinfo is None:
                upd_at = upd_at.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
            if upd_at > last_at:
                upd_elapsed = (now - upd_at).total_seconds()
                if upd_elapsed < _ACTIVITY_GRACE_SEC:
                    return None  # 看板 3 分钟内有活动，不判定断链
        except Exception:
            pass

    # ── 三层检查均未通过，判定为断链 ──
    label_to = ID_TO_LABEL.get(last_to, last_to)
    label_from = ID_TO_LABEL.get(
        normalize_name(last.get("from", "")),
        last.get("from", "")
    )
    return {
        "target_agent_id": last_to,
        "target_label": label_to,
        "from_label": label_from,
        "elapsed_sec": int(elapsed),
        "detail": f"断链超时：{label_from} → {label_to} 已等待 {int(elapsed // 60)} 分钟无回应",
    }


def check_session_violation(task_id, flow_log, session_keys, task_state=""):
    """检查任务是否存在会话违规（重复 spawn、该用 send 却 spawn、未注册 session key）。

    检测逻辑（三层检测）：
    1. 会话未注册：flow_log 有跨部门通信记录，但 session_keys 为空
       → 说明 Agent 从未使用 session-keys save，所有通信都是裸 spawn
    2. 重复通信：同一 from→to 对出现超过阈值次数
       - 中书↔门下：超过 7 次（3轮审议正常范围）
       - 其他 pair：超过 3 次
       → 说明 Agent 可能没有复用 session，反复 spawn 新会话
    3. 有 key 但通信仍过多（加强版）：有 sessionKey 的 pair 通信超过 2 次
       → 即使有 key 也可能没遵守 send 规范

    返回违规列表。
    """
    violations = []

    # 统计每个 from→to 对的出现次数（排除自身消息）
    pair_counts = {}
    has_cross_dept_comm = False
    for entry in flow_log:
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if not f or not t or f == t:
            continue
        pair = (f, t)
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        has_cross_dept_comm = True

    # ── 检测 1：会话未注册 ──
    if has_cross_dept_comm and not session_keys:
        # 跳过终态任务（已完成/已取消的不需要再检测）
        if task_state not in ("Done", "Cancelled"):
            total_comm = sum(pair_counts.values())
            violations.append(
                f"会话未注册：任务有 {total_comm} 次跨部门通信记录，"
                f"但 session_keys 为空。Agent 未使用 session-keys save 保存会话密钥，"
                f"所有通信可能都是裸 spawn（每次创建新会话），导致会话膨胀。"
                f"应使用 kanban_update.py session-keys save 保存首次通信的 sessionKey。"
            )
        # 有了"会话未注册"警告后，不需要继续其他检测（没有 keys 可比较）
        return violations

    if not session_keys:
        return violations

    # ── 检测 2 + 3：通信频率检测 ──
    for pair_key, key_entry in session_keys.items():
        agents = key_entry.get("agents", [])
        if len(agents) < 2:
            continue
        a, b = agents[0].lower(), agents[1].lower()

        # 双向检查
        for (src, dst) in [(a, b), (b, a)]:
            count = pair_counts.get((src, dst), 0)
            dept_from = ID_TO_DEPT.get(src, src)
            dept_to = ID_TO_DEPT.get(dst, dst)

            # 中书↔门下合法多轮（最多 3 轮 = 最多 6 次双向通信 + 1 次初始）
            if {a, b} == {"zhongshu", "menxia"}:
                if count > 7:
                    violations.append(
                        f"会话通信过多：{dept_from} → {dept_to} 通信 {count} 次"
                        f"（已超过中书↔门下 3 轮审议的正常范围 7 次，"
                        f"可能存在会话爆炸风险。已有 sessionKey 应使用 sessions_send 复用会话）"
                    )
            else:
                # 其他 pair：有 key 但超过 3 次 → 可能未复用
                if count > 3:
                    violations.append(
                        f"会话通信过多：{dept_from} → {dept_to} 通信 {count} 次"
                        f"（该 pair 已注册 sessionKey，超过 3 次通信说明可能未使用 sessions_send 复用会话，"
                        f"请确保后续通信使用 sessions_send 而非 sessions_spawn）"
                    )

    return violations


# ═══════════════════════════════════════════════════════════════════
#  跨 Agent 越权通信检测（方案 B+ 增强）
# ═══════════════════════════════════════════════════════════════════

# 子代理允许的通信白名单：每个 Agent 只能与其直接上级/下级通信
# 格式：(from_agent_id, to_agent_id) → 是否合法
_CHILD_AGENT_ALLOWED_PAIRS = {
    # 上行：上级调用下级
    ("huangshang", "taizi"),
    ("taizi", "zhongshu"),
    ("zhongshu", "menxia"),
    ("zhongshu", "shangshu"),
    ("shangshu", "gongbu"),
    ("shangshu", "bingbu"),
    ("shangshu", "hubu"),
    ("shangshu", "libu"),
    ("shangshu", "xingbu"),
    ("shangshu", "libu_hr"),
    # 下行：下级返回上级
    ("zhongshu", "taizi"),
    ("menxia", "zhongshu"),
    ("shangshu", "zhongshu"),
    ("shangshu", "taizi"),
    ("gongbu", "shangshu"),
    ("bingbu", "shangshu"),
    ("hubu", "shangshu"),
    ("libu", "shangshu"),
    ("xingbu", "shangshu"),
    ("libu_hr", "shangshu"),
    # 太子→皇上汇报（下行链路最终环节）
    ("taizi", "huangshang"),
      # 六部之间协作通信
    ("gongbu",  "bingbu"), ("bingbu",  "gongbu"),
    ("gongbu",  "hubu"),   ("hubu",   "gongbu"),
    ("gongbu",  "libu"),   ("libu",   "gongbu"),
    ("gongbu",  "xingbu"), ("xingbu", "gongbu"),
    ("gongbu",  "libu_hr"),("libu_hr","gongbu"),
    ("bingbu",  "hubu"),   ("hubu",   "bingbu"),
    ("bingbu",  "libu"),   ("libu",   "bingbu"),
    ("bingbu",  "xingbu"), ("xingbu", "bingbu"),
    ("bingbu",  "libu_hr"),("libu_hr","bingbu"),
    ("hubu",    "libu"),   ("libu",   "hubu"),
    ("hubu",    "xingbu"), ("xingbu", "hubu"),
    ("hubu",    "libu_hr"),("libu_hr","hubu"),
    ("libu",    "xingbu"), ("xingbu", "libu"),
    ("libu",    "libu_hr"),("libu_hr","libu"),
    ("xingbu",  "libu_hr"),("libu_hr","xingbu"),
    # 自身内部消息
    ("taizi", "taizi"),
    ("zhongshu", "zhongshu"),
    ("menxia", "menxia"),
    ("shangshu", "shangshu"),
    ("gongbu", "gongbu"),
    ("bingbu", "bingbu"),
    ("hubu", "hubu"),
    ("libu", "libu"),
    ("xingbu", "xingbu"),
    ("libu_hr", "libu_hr"),
    # 监察系统（jiancha）可通知所有部门
    ("jiancha", "taizi"),
    ("jiancha", "zhongshu"),
    ("jiancha", "menxia"),
    ("jiancha", "shangshu"),
    ("jiancha", "gongbu"),
    ("jiancha", "bingbu"),
    ("jiancha", "hubu"),
    ("jiancha", "libu"),
    ("jiancha", "xingbu"),
    ("jiancha", "libu_hr"),
}

# 子代理绝对禁止的通信对（即使不在上面的白名单中）
# 这些是跨层级或跨部门的越权通信
_FORBIDDEN_CROSS_PAIRS = {
    # 六部之间禁止互相通信
#     ("bingbu", "gongbu"), ("gongbu", "bingbu"),
#     ("bingbu", "hubu"), ("hubu", "bingbu"),
#     ("bingbu", "libu"), ("libu", "bingbu"),
#     ("bingbu", "xingbu"), ("xingbu", "bingbu"),
#     ("bingbu", "libu_hr"), ("libu_hr", "bingbu"),
#     ("gongbu", "hubu"), ("hubu", "gongbu"),
#     ("gongbu", "libu"), ("libu", "gongbu"),
#     ("gongbu", "xingbu"), ("xingbu", "gongbu"),
#     ("gongbu", "libu_hr"), ("libu_hr", "gongbu"),
#     ("hubu", "libu"), ("libu", "hubu"),
#     ("hubu", "xingbu"), ("xingbu", "hubu"),
#     ("hubu", "libu_hr"), ("libu_hr", "hubu"),
#     ("libu", "xingbu"), ("xingbu", "libu"),
#     ("libu", "libu_hr"), ("libu_hr", "libu"),
#     ("xingbu", "libu_hr"), ("libu_hr", "xingbu"),
  
    # 六部禁止直接给中书省/门下省/太子发消息
    ("bingbu", "taizi"), ("gongbu", "taizi"), ("hubu", "taizi"),
    ("libu", "taizi"), ("xingbu", "taizi"), ("libu_hr", "taizi"),
    ("bingbu", "menxia"), ("gongbu", "menxia"), ("hubu", "menxia"),
    ("libu", "menxia"), ("xingbu", "menxia"), ("libu_hr", "menxia"),
    # 门下省禁止直接调用尚书省或六部
    ("menxia", "shangshu"),
    ("menxia", "gongbu"), ("menxia", "bingbu"), ("menxia", "hubu"),
    ("menxia", "libu"), ("menxia", "xingbu"), ("menxia", "libu_hr"),
    # 太子禁止直接调用门下省/尚书省/六部
    ("taizi", "menxia"),
    ("taizi", "shangshu"),
    ("taizi", "gongbu"), ("taizi", "bingbu"), ("taizi", "hubu"),
    ("taizi", "libu"), ("taizi", "xingbu"), ("taizi", "libu_hr"),
}


def check_cross_agent_violation(task_id, flow_log, task_state=""):
    """检测子代理越权通信（方案 B+ 增强）。

    检测逻辑：
    1. 六部之间互相通信 → 越权（六部只能与尚书省通信）
    2. 子代理跨层级通信 → 越权（如六部直接给太子发消息）
    3. 子代理调用非上下级部门 → 越权（如门下省直接调用尚书省）

    注意：
    - 太子调度系统（from='太子调度'）的 flow_log 不检测
    - 已终态的任务不检测
    """
    violations = []

    if task_state in ("Done", "Cancelled"):
        return violations

    detected_pairs = set()
    for entry in flow_log:
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if not f or not t or f == t:
            continue
        # 跳过太子调度系统
        dept_f = ID_TO_DEPT.get(f, f)
        if dept_f == "太子调度":
            continue
        # 跳过皇上的消息
        if f in ("huangshang", "皇上"):
            continue

        pair = (f, t)
        if pair in detected_pairs:
            continue  # 同一对只检测一次
        detected_pairs.add(pair)

        # 检查是否在禁止列表中
        if pair in _FORBIDDEN_CROSS_PAIRS:
            label_from = ID_TO_DEPT.get(f, f)
            label_to = ID_TO_DEPT.get(t, t)
            violations.append(
                f"子代理越权通信：{label_from} → {label_to}。"
                f"该通信对不在 allowAgents 白名单中，违反会话隔离规则。"
                f"子代理只能与创建它的直接上级通信，禁止跨部门/跨层级通信。"
            )
        # 额外检查：既不在 allowed 也不在 forbidden 中，且不是监察系统
        elif pair not in _CHILD_AGENT_ALLOWED_PAIRS and f != "jiancha" and t != "jiancha":
            label_from = ID_TO_DEPT.get(f, f)
            label_to = ID_TO_DEPT.get(t, t)
            violations.append(
                f"可疑跨部门通信：{label_from} → {label_to}。"
                f"该通信对不在合法通信白名单中，请确认是否为预期行为。"
            )

    return violations


# 极端停滞阈值（使用模块顶部定义，此处仅为注释参考）
# EXTREME_STALL_THRESHOLD 已在模块顶部定义为 20 * 60（20分钟）


def check_extreme_stall(task_id, flow_log, task_state, updated_at_str, stall_threshold=None):
    """检查任务是否极端停滞。"""
    _threshold = stall_threshold if stall_threshold is not None else EXTREME_STALL_THRESHOLD
    if task_state in ("Done", "Cancelled", "Blocked"):
        return None
    if not updated_at_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(updated_at_str.replace("Z", "+00:00").replace("+08:00", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        now = datetime.datetime.now(_BJT)
        elapsed = (now - dt).total_seconds()
        if elapsed >= _threshold:
            label = ID_TO_LABEL.get(
                normalize_name(flow_log[-1].get("to", "")) if flow_log else "",
                "未知"
            ) if flow_log else "未知"
            return {
                "type": "极端停滞",
                "detail": f"任务在 {task_state} 状态已停滞 {int(elapsed // 60)} 分钟无更新（阈值 {_threshold // 60} 分钟）",
                "elapsed_sec": int(elapsed),
                "target_label": label,
            }
    except Exception:
        pass
    return None


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
#  主流程
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
    """watchdog 主逻辑（在单实例锁保护下运行）。"""
    # ── 加载自适应配置 ──
    load_watchdog_config()

    # ── 提取配置到局部变量（避免频繁 dict 查找）──
    _break_timeout = _cfg.get("break_timeout_sec", BREAK_TIMEOUT_SEC)
    _review_grace = _cfg.get("review_grace_periods", REVIEW_GRACE_PERIODS)
    _extreme_stall = _cfg.get("extreme_stall_threshold_sec", EXTREME_STALL_THRESHOLD)
    _enabled = _cfg.get("enabled_checks", {})
    _max_notifs = _cfg.get("max_notifications", 200)
    _max_violations = _cfg.get("max_violations", 200)
    _max_arch_violations = _cfg.get("max_archived_violations", 500)
    _max_arch_notifs = _cfg.get("max_archived_notifications", 100)
    _multitask_enabled = _cfg.get("multitask_enabled", True)

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

    # 即使没有活跃任务也写入审计（标记监察在运行，显示 watched_tasks=[]）
    audit = load_audit()
    audit.setdefault("notifications", [])

    # 构建正在监察的任务列表（只含真正活跃的旨意任务，不含已完成的）
    truly_active = [
        t for t in tasks
        if t.get("state") not in ("Done", "Cancelled")
        and _is_edict_task(t)
        and t.get("id", "") not in exclude_list
                        and not t.get("archived")
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

    if not active:
        audit["last_check"] = now_iso
        audit["watched_tasks"] = watched_tasks
        audit["watched_count"] = len(watched_tasks)
        save_audit(audit)
        log(f"本轮检查完成，{len(watched_tasks)} 个活跃任务均正常")
        return

    new_violations = []
    woken_agents = set()  # 同一轮只唤醒一次

    # ═══════════════════════════════════════════════════════════════════
    #  多任务动态调整：根据活跃任务数动态调整监察参数
    # ═══════════════════════════════════════════════════════════════════
    # 多任务并行时，中书省等关键节点需同时处理多个任务，
    # 原有的单任务断链超时（90s）和活动宽限期（180s）会导致大量误报。
    # 此处根据当前活跃旨意任务数动态调整各项阈值。
    _active_task_count = len(truly_active)  # 活跃旨意任务数（不含 Done/Cancelled）

    if _multitask_enabled and _active_task_count >= 2:
        # ── 动态调整断链超时 ──
        _mt_key = min(_active_task_count, 4)  # 超过4个任务取最大值
        _mt_break_timeout = MULTITASK_BREAK_TIMEOUT.get(_mt_key, 180)
        if _mt_break_timeout > _break_timeout:
            log(f"多任务检测：{_active_task_count} 个活跃任务，断链超时 {_break_timeout}s → {_mt_break_timeout}s")
            _break_timeout = _mt_break_timeout

        # ── 动态调整审议宽限期（按乘数放大）──
        _mt_grace_scale = MULTITASK_REVIEW_GRACE_SCALE.get(_mt_key, 2.0)
        _review_grace_dynamic = {}
        for _k, _v in _review_grace.items():
            if isinstance(_v, (int, float)):
                _review_grace_dynamic[_k] = int(_v * _mt_grace_scale)
            else:
                _review_grace_dynamic[_k] = _v
        if _mt_grace_scale > 1.0:
            log(f"多任务检测：审议宽限期乘数 {_mt_grace_scale}x，Menxia 宽限期 {_review_grace.get('Menxia', 0)}s → {_review_grace_dynamic.get('Menxia', 0)}s")
            _review_grace = _review_grace_dynamic
    else:
        _active_task_count = 1  # 单任务或关闭多任务检测时，传 1

    # ── 自适应行为：根据历史稳定性动态调整检测参数 ──
    _adaptive_enabled = _cfg.get("adaptive_enabled", True)
    _stability_score = 1.0  # 默认完全稳定
    if _adaptive_enabled:
        _stability_window = _cfg.get("stability_window", 20)
        _history = audit.get("check_history", [])
        if len(_history) >= 5:
            recent = _history[-_stability_window:]
            clean = sum(1 for h in recent if h.get("violations", 0) == 0)
            _stability_score = clean / len(recent)
            log(f"稳定性评分: {_stability_score:.0%}（近 {len(recent)} 轮，{clean} 轮无问题）")
            # 高稳定性时增加宽限期（减少误报）
            if _stability_score >= _cfg.get("stability_threshold_high", 0.8):
                _boost = _cfg.get("adaptive_grace_boost_sec", 60)
                _break_timeout += _boost
                log(f"高稳定性模式：断链超时 +{_boost}s → {_break_timeout}s")

    # ── 去重：构建已有违规 key 集合，避免每轮重复写入相同违规 ──
    existing_violation_keys = set()
    for v in audit.get("violations", []):
        v_key = (v.get("task_id", ""), v.get("type", ""), v.get("flow_index", -1), v.get("detail", ""))
        existing_violation_keys.add(v_key)

    # ── Fix #2: 仅清理已归档（archived=True）任务的违规 ──
    # 违规记录仅在任务被归档时转移到 archived_violations，
    # 而非在任务 Done/Cancelled 时就转移（用户要求：记录不应被清除，除非任务被删除/归档）。
    # 后续的归档步骤（archived_task_ids）会处理 archived=True 的任务。
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

    # ── Fix #2 原因A: 自动修复活跃任务的过时违规 ──
    # 对于仍在活跃的任务，如果 flow_log 已经包含了之前报缺失的步骤，
    # 则该违规已经自动修复，应从 violations 中移除。
    # 这解决了中书↔门下多轮审议期间，watchdog 在 flow_log 尚未更新时
    # 误报跳步违规后永远留在审计日志中的问题。
    _resolved_violations = []
    _tasks_by_id = {t.get('id', ''): t for t in tasks}
    for _v in audit.get("violations", []):
        if _v.get("type") != "流程跳步":
            continue  # 只处理流程跳步类违规的自动修复
        _v_task = _v.get("task_id", "")
        _v_detail = _v.get("detail", "")
        if not _v_task or not _v_detail:
            continue
        _task_obj = _tasks_by_id.get(_v_task)
        if not _task_obj:
            continue
        # 从违规详情中提取缺失的步骤 (from → to)
        # 违规格式: "流程跳步：缺少必要环节 门下省 → 中书省"
        # 或: "流程跳步：XXX 已执行，但缺少前置环节 门下省 → 中书省"
        import re as _re
        _step_match = _re.search(r'缺少(?:必要环节|前置环节)\s+(\S+)\s*→\s*(\S+)', _v_detail)
        if not _step_match:
            continue
        _missing_from = _step_match.group(1)
        _missing_to = _step_match.group(2)
        # 检查当前 flow_log 是否已包含该步骤
        _v_flow = _task_obj.get("flow_log", [])
        _step_now_exists = False
        for _entry in _v_flow:
            _ef = _entry.get("from", "")
            _et = _entry.get("to", "")
            if _missing_from in _ef and _missing_to in _et:
                _step_now_exists = True
                break
        if _step_now_exists:
            _resolved_violations.append(_v)
    if _resolved_violations:
        _resolved_count = len(_resolved_violations)
        _resolved_ids = {id(_v) for _v in _resolved_violations}
        audit["violations"] = [_v for _v in audit.get("violations", []) if id(_v) not in _resolved_ids]
        audit.setdefault("resolved_violations", [])
        for _rv in _resolved_violations:
            _rv["resolved_at"] = now_iso
            _rv["resolve_reason"] = "流程已补全缺失步骤，违规自动修复"
            audit["resolved_violations"].append(_rv)
        if len(audit["resolved_violations"]) > _max_arch_violations:
            audit["resolved_violations"] = audit["resolved_violations"][-_max_arch_violations:]
        log(f"已自动修复 {_resolved_count} 条过时跳步违规（flow_log 已补全缺失步骤）")

    for task in active:
        task_id = task.get("id", "?")
        title = task.get("title", "")
        flow_log = task.get("flow_log", [])
        task_state = task.get("state", "")

        if not flow_log:
            continue

        # ── Issue #3: 跳过太子手动操作的任务（不受监察回退）──
        # 用户在看板手动点击推进/叫停/取消时，server.py 会标记 _taiziManual
        # 这些操作视为太子行为，不检测越权/跳步/断链
        sched = task.get("_scheduler") or {}
        if sched.get("_taiziManual"):
            continue

        # ── Fix #2: 优雅期 — 如果任务刚在 60 秒内更新过，跳过本轮检查 ──
        # 防止 flow_log 还未被 Agent 更新时误报跳步违规
        _task_updated = task.get("updatedAt", "")
        if _task_updated:
            try:
                _udt = datetime.datetime.fromisoformat(_task_updated.replace("Z", "+00:00").replace("+08:00", ""))
                if _udt.tzinfo is None:
                    _udt = _udt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
                if (now - _udt).total_seconds() < 60:
                    continue  # 刚更新不到 60 秒，给 Agent 时间写 flow_log
            except Exception:
                pass

        # ── 检查 1：越权调用（逐条检查）──
        if _enabled.get("illegal_flow", True):
            for i, entry in enumerate(flow_log):
                f = normalize_name(entry.get("from", ""))
                t = normalize_name(entry.get("to", ""))
                if not f or not t:
                    continue
                # 跳过太子调度系统产生的 flow_log（合法的系统行为）
                dept_f = ID_TO_DEPT.get(f, f)
                if dept_f == "太子调度":
                    continue
                illegal = check_illegal_flow(task_id, f, t, i)
                if illegal:
                    v_key = (task_id, "越权调用", i, f"{dept_f} → {ID_TO_DEPT.get(t, t)}：{illegal}")
                    if v_key not in existing_violation_keys:
                        violation = {
                            "task_id": task_id,
                            "title": title,
                            "type": "越权调用",
                            "detail": f"{dept_f} → {ID_TO_DEPT.get(t, t)}：{illegal}",
                            "flow_index": i,
                            "detected_at": now_iso,
                        }
                        new_violations.append(violation)

        # ── 检查 2：流程跳步（改进：含后向检查）──
        if _enabled.get("skip_steps", True):
            skips = check_skip_steps(task_id, flow_log)
            for skip_detail in skips:
                _skip_hash = hash(skip_detail) & 0xFFFFFFFF
                v_key = (task_id, "流程跳步", _skip_hash, skip_detail)
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": "流程跳步",
                        "detail": skip_detail,
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)

        # ── 检查 2.5：直接执行越权（三省代劳六部工作）──
        if _enabled.get("direct_execution", True):
            direct_exec = check_direct_execution(task_id, flow_log, task_state)
            if direct_exec:
                v_key = (task_id, "直接执行越权", -1, direct_exec)
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": "直接执行越权",
                        "detail": direct_exec,
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)

        # ── 检查 2.6：六部执行证据验证（假派发真越权检测 — 五维验证）──
        # 核心场景：尚书省写了一条"尚书省→六部"的 flow_log 就回奏了，
        # 但六部完全没有执行痕迹（无回复、无 session、无进展）。
        # 比现有 check_direct_execution 严格得多：后者只检查六部是否出现在 flow_log 中。
        if _enabled.get("liubu_evidence", True):
            _liubu_session_keys = task.get('session_keys', {})
            _liubu_progress_log = task.get('progress_log', [])
            _liubu_task_org = task.get('org', '')
            _liubu_task_active_agent = task.get('activeAgent', '')
            liubu_violations = check_liubu_execution_evidence(
                task_id, flow_log,
                session_keys=_liubu_session_keys,
                progress_log=_liubu_progress_log,
                task_state=task_state,
                task_org=_liubu_task_org,
                task_active_agent=_liubu_task_active_agent,
            )
            for lv in liubu_violations:
                v_key = (task_id, "假派发越权", lv["dispatch_idx"], lv["detail"])
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": "假派发越权",
                        "detail": lv["detail"],
                        "flow_index": lv["dispatch_idx"],
                        "detected_at": now_iso,
                        "target_agent": lv.get("target_agent", ""),
                        "target_label": lv.get("target_label", ""),
                    }
                    new_violations.append(violation)

                    # ── 介入处理：通知尚书省必须重新用 sessions_spawn 派发 ──
                    target_label = lv.get("target_label", "六部")
                    target_agent_id = lv.get("target_agent", "")
                    re_dispatch_detail = (
                        f"任务 {task_id} 检测到假派发越权："
                        f"尚书省声称已派发至{target_label}，但{target_label}无任何执行证据。"
                        f"五维验证全部为空（无flow回复、无session_key、无进展记录、"
                        f"org未指向{target_label}、activeAgent未指向{target_label}）。"
                        f"必须立即使用 sessions_spawn 重新派发给{target_label}，禁止自行代劳。"
                        f"⚠️ 禁止使用 sessions_yield，必须使用 sessions_spawn！"
                    )
                    # 查找尚书省的 session_key 进行精准通知
                    _ss_session_key = _find_task_session_key_for_agent(task, "shangshu")
                    if _ss_session_key:
                        try:
                            subprocess.Popen(
                                ["openclaw", "sessions", "send", "--session-key", _ss_session_key,
                                 "-m", f"🛡️ 监察介入 — 假派发越权\n\n{re_dispatch_detail}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已通知尚书省假派发越权 | {task_id} → {target_label} [复用 session]")
                        except Exception as e:
                            log(f"通知尚书省假派发越权失败: {e}")
                    else:
                        try:
                            subprocess.Popen(
                                ["openclaw", "agent", "--agent", "shangshu",
                                 "--task", f"🛡️ 监察介入 — 假派发越权\n\n{re_dispatch_detail}",
                                 "--mode", "run", "--thread", "false"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已通知尚书省假派发越权 | {task_id} → {target_label} [新 subagent]")
                        except Exception as e:
                            log(f"通知尚书省假派发越权失败: {e}")

                    # 记录介入通知
                    audit["notifications"].append(_make_notif(
                        notif_type="假派发越权介入", to="尚书省",
                        detail=f"检测到{target_label}无执行证据，已通知尚书省重新派发",
                        task_id=task_id,
                    ))

        # ── 检查 2.7：极端停滞检测 ──
        if _enabled.get("extreme_stall", True):
            updated_at_str = task.get("updatedAt", "")
            stall_info = check_extreme_stall(task_id, flow_log, task_state, updated_at_str, _extreme_stall)
            if stall_info:
                v_key = (task_id, "极端停滞", -1, stall_info["detail"])
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": "极端停滞",
                        "detail": stall_info["detail"],
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)

        # ── 检查 2.8：会话违规检测（session-keys 合规性）──
        if _enabled.get("session_violation", True):
            session_keys = task.get('session_keys', {})
            session_violations = check_session_violation(task_id, flow_log, session_keys, task_state)
            for sv_detail in session_violations:
                if "未注册" in sv_detail:
                    sv_type = "会话未注册"
                elif "过多" in sv_detail:
                    sv_type = "会话通信过多"
                elif "可疑" in sv_detail:
                    sv_type = "会话可疑"
                else:
                    sv_type = "会话违规"
                v_key = (task_id, sv_type, -1, sv_detail)
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": sv_type,
                        "detail": sv_detail,
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)

        # ── 检查 2.9：跨 Agent 越权通信检测（方案 B+ 增强）──
        if _enabled.get("cross_agent", True):
            cross_violations = check_cross_agent_violation(task_id, flow_log, task_state)
            for cv_detail in cross_violations:
                if "越权通信" in cv_detail:
                    cv_type = "子代理越权通信"
                elif "可疑跨部门" in cv_detail:
                    cv_type = "可疑跨部门通信"
                else:
                    cv_type = "通信违规"
                v_key = (task_id, cv_type, -1, cv_detail)
                if v_key not in existing_violation_keys:
                    violation = {
                        "task_id": task_id,
                        "title": title,
                        "type": cv_type,
                        "detail": cv_detail,
                        "detected_at": now_iso,
                    }
                    new_violations.append(violation)

        # ── 检查 3：断链超时 ──
        if _enabled.get("broken_chain", True):
            _progress_log = task.get("progress_log", [])
            _task_updated_at = task.get("updatedAt", "")
            broken = check_broken_chain(
                task_id, flow_log, task_state,
                progress_log=_progress_log,
                task_updated_at=_task_updated_at,
                break_timeout=_break_timeout,
                review_grace=_review_grace,
                active_task_count=_active_task_count,
            )
            if broken:
                target_id = broken["target_agent_id"]
                target_label = broken["target_label"]
                from_label = broken["from_label"]
                target_dept = ID_TO_DEPT.get(target_id, target_id)
                parent_id = PARENT_MAP.get(target_dept)

                violation = {
                    "task_id": task_id,
                    "title": title,
                    "type": "断链超时",
                    "detail": broken["detail"],
                    "detected_at": now_iso,
                }
                new_violations.append(violation)

                # ── 介入处理：唤醒目标部门 ──
                if target_id and target_id not in woken_agents:
                    wake_ok, wake_detail = wake_agent(target_id, f"任务 {task_id} 流程断链，{from_label}已等你 {broken['elapsed_sec'] // 60} 分钟")
                    woken_agents.add(target_id)
                    # 记录唤醒动作
                    audit["notifications"].append(_make_notif(
                        notif_type="断链唤醒", to=target_label,
                        detail=f"{from_label}→{target_label} 断链，已唤醒",
                        task_id=task_id,
                        status="sent" if wake_ok else "failed",
                    ))

                # ── 介入处理：通知上游重新派发 ──
                if parent_id and parent_id not in woken_agents:
                    parent_label = ID_TO_LABEL.get(parent_id, parent_id)
                    if not is_agent_awake(parent_id):
                        wake_ok, wake_detail = wake_agent(parent_id, f"任务 {task_id} 断链，需要你重新派发{target_label}")
                        woken_agents.add(parent_id)
                        # 记录唤醒动作
                        audit["notifications"].append(_make_notif(
                            notif_type="断链唤醒", to=parent_label,
                            detail=f"断链处理：唤醒上级{parent_label}重新派发{target_label}",
                            task_id=task_id,
                            status="sent" if wake_ok else "failed",
                        ))
                    else:
                        wake_ok = True
                        wake_detail = f"{parent_label} 已在线，直接发送重新派发通知"
                    
                    re_dispatch_msg = (
                        f"🔔 流程断链通知 - 需要你重新派发\n"
                        f"任务ID: {task_id}\n"
                        f"标题: {title}\n"
                        f"问题: {from_label} → {target_label} 已等待 {broken['elapsed_sec'] // 60} 分钟无回应\n"
                        f"已唤醒 {target_label}，请重新派发任务给它。\n"
                        f"使用 sessions_spawn 唤醒 {target_label}（如果之前没有 sessionKey）\n"
                        f"或使用 sessions_send 继续已有对话（如果有 sessionKey）。\n"
                        f"⚠️ 禁止使用 sessions_yield，必须使用 sessions_spawn！\n"
                        f"⚠️ 看板已有此任务，请勿重复创建。"
                    )
                    try:
                        # 精准发送到该任务的父级子代理 session，不打 main session
                        _task_session_key = _find_task_session_key_for_agent(task, parent_id)
                        if _task_session_key:
                            subprocess.Popen(
                                ["openclaw", "sessions", "send", "--session-key", _task_session_key, "-m", re_dispatch_msg],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已通知 {parent_label} 重新派发 {target_label} | {task_id} [复用 session]")
                        else:
                            subprocess.Popen(
                                ["openclaw", "sessions", "spawn", "--agent", parent_id, "--task", re_dispatch_msg,
                                 "--mode", "run", "--thread", "false"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已通知 {parent_label} 重新派发 {target_label} | {task_id} [新 subagent]")
                    except Exception as e:
                        log(f"通知 {parent_label} 重新派发失败: {e}")
                        wake_ok = False
                        wake_detail = str(e)
                    
                    # 记录通知动作
                    audit["notifications"].append(_make_notif(
                        notif_type="断链通知", to=parent_label,
                        detail=f"{from_label}→{target_label} 断链，已通知{parent_label}重新派发",
                        task_id=task_id,
                        status="sent" if wake_ok else "failed",
                    ))
                elif parent_id and parent_id in woken_agents:
                    parent_label = ID_TO_LABEL.get(parent_id, parent_id)
                    re_dispatch_msg = (
                        f"🔔 流程断链通知 - 需要你重新派发\n"
                        f"任务ID: {task_id}\n"
                        f"标题: {title}\n"
                        f"问题: {from_label} → {target_label} 已等待 {broken['elapsed_sec'] // 60} 分钟无回应\n"
                        f"请重新派发任务给 {target_label}。\n"
                        f"⚠️ 禁止使用 sessions_yield，必须使用 sessions_spawn！\n"
                        f"⚠️ 看板已有此任务，请勿重复创建。"
                    )
                    try:
                        # 精准发送到该任务的父级子代理 session，不打 main session
                        _task_session_key = _find_task_session_key_for_agent(task, parent_id)
                        if _task_session_key:
                            subprocess.Popen(
                                ["openclaw", "sessions", "send", "--session-key", _task_session_key, "-m", re_dispatch_msg],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已补充通知 {parent_label} 重新派发 {target_label} | {task_id} [复用 session]")
                        else:
                            subprocess.Popen(
                                ["openclaw", "sessions", "spawn", "--agent", parent_id, "--task", re_dispatch_msg,
                                 "--mode", "run", "--thread", "false"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            log(f"已补充通知 {parent_label} 重新派发 {target_label} | {task_id} [新 subagent]")
                    except Exception as e:
                        log(f"补充通知 {parent_label} 失败: {e}")
                    # 记录补充通知动作
                    audit["notifications"].append(_make_notif(
                        notif_type="断链通知", to=parent_label,
                        detail=f"补充通知{parent_label}重新派发{target_label}（已唤醒过）",
                        task_id=task_id,
                    ))

    # ── 严重违规通知太子（越权 + 直接执行越权 + 假派发越权 + 流程跳步）──
    serious = [v for v in new_violations if v["type"] in ("越权调用", "直接执行越权", "假派发越权")]
    skip_violations = [v for v in new_violations if v["type"] == "流程跳步"]

    # ── 自适应降噪：高稳定性时跳过轻微违规的通知 ──
    _minor_types = ("会话未注册", "会话通信过多", "可疑跨部门通信", "子代理越权通信")
    if _adaptive_enabled and _stability_score >= _cfg.get("stability_threshold_high", 0.8):
        _minor_skip = [v for v in new_violations if v["type"] in _minor_types]
        if _minor_skip:
            # 轻微违规仍然记录到 violations，但不通知太子
            audit["notifications"].append(_make_notif(
                notif_type="巡检", to="系统",
                detail=f"高稳定性模式：跳过 {len(_minor_skip)} 项轻微违规通知（{', '.join(set(v['type'] for v in _minor_skip))}）",
            ))
    
    # ── Fix #2 原因B: 跳步通报冷却机制 ──
    # 防止中书↔门下多轮审议期间，每次流转都触发通知太子。
    # 同一任务的跳步违规在 _SKIP_NOTIFY_COOLDOWN 秒内只通知一次。
    _SKIP_NOTIFY_COOLDOWN = 300  # 5 分钟冷却
    _skip_notify_last = audit.get("last_skip_notify_at", "")
    _skip_notify_task = audit.get("last_skip_notify_task", "")
    _skip_should_notify = True
    if skip_violations and not serious:
        # 纯跳步违规（无越权）时启用冷却
        _skip_task_ids = list(set(v["task_id"] for v in skip_violations))
        if _skip_notify_last and _skip_notify_task in _skip_task_ids:
            try:
                _last_dt = datetime.datetime.fromisoformat(_skip_notify_last.replace("Z", "+00:00"))
                if (now - _last_dt).total_seconds() < _SKIP_NOTIFY_COOLDOWN:
                    _skip_should_notify = False
                    log(f"跳步通报冷却中（{_skip_notify_task}，{(now - _last_dt).total_seconds():.0f}s前已通知），跳过本轮")
            except Exception:
                pass

    if serious or (skip_violations and _skip_should_notify):
        lines = []
        if serious:
            for v in serious:
                lines.append(f"  - {v['task_id']}: {v['detail']}")
        if skip_violations:
            for v in skip_violations:
                lines.append(f"  - {v['task_id']}: {v['detail']}")
        summary = "\n".join(lines)
        violation_types = []
        if serious:
            violation_types.append(f"{len(serious)} 项越权/执行违规")
        if skip_violations:
            violation_types.append(f"{len(skip_violations)} 项流程跳步")
        type_summary = "，".join(violation_types)
        
        notify_msg = (
            f"🛡️ 监察通报 — 流程违规\n"
            f"发现 {type_summary}：\n\n"
            f"{summary}\n\n"
            f"请太子核实并纠正。"
        )
        notify_ok, notify_detail = notify_agent("taizi", notify_msg)
        # 记录违规通报动作到通知
        audit["notifications"].append(_make_notif(
            notif_type="越权通报" if serious else "跳步通报", to="太子",
            detail=f"发现{type_summary}",
            task_ids=list(set(v["task_id"] for v in (serious + skip_violations))),
            status="sent" if notify_ok else "failed",
        ))
        # 记录跳步通报时间（用于冷却去重）
        if skip_violations and not serious:
            audit["last_skip_notify_at"] = now_iso
            audit["last_skip_notify_task"] = skip_violations[0]["task_id"]

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

    # 不再对活跃任务的违规记录做硬限制裁剪
    # 违规记录仅在任务被归档时转移到 archived_violations
    # _max_violations 仅用于控制 archived_violations 的大小上限
    audit["last_check"] = now_iso
    audit["watched_tasks"] = watched_tasks
    audit["watched_count"] = len(watched_tasks)
    audit["check_count"] = audit.get("check_count", 0) + 1
    audit["total_violations"] = audit.get("total_violations", 0) + len(new_violations)

    # 记录本轮检查历史（用于自适应行为）
    audit.setdefault("check_history", []).append({
        "at": now_iso,
        "violations": len(new_violations),
        "active_tasks": len(active),
        "stability": round(_stability_score, 2),
    })
    audit["check_history"] = audit["check_history"][-100:]  # 保留最近100轮
    # 不再对活跃任务的通知记录做硬限制裁剪
    # 通知记录仅在任务被归档时转移到 archived_notifications

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
