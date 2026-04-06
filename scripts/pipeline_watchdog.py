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

# ── 路径配置 ──────────────────────────────────────────────────────
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
TASKS_FILE = DATA_DIR / "tasks_source.json"
AUDIT_FILE = DATA_DIR / "pipeline_audit.json"
EXCLUDE_FILE = DATA_DIR / "audit_exclude.json"
OCLAW_HOME = pathlib.Path.home() / ".openclaw"

# ── 超时阈值（秒）─────────────────────────────────────────────────
BREAK_TIMEOUT_SEC = 60   # 1 分钟无回应判定为断链
RECENT_DONE_MINUTES = 10  # 最近 N 分钟内完成的任务也需检查（防止速通逃逸）
AUTO_ARCHIVE_MINUTES = 5  # Done 超过 N 分钟自动归档

# ── 日志工具 ──────────────────────────────────────────────────────
def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [监察] {msg}", flush=True)


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
}

# agent_id → 中文显示名
ID_TO_LABEL = {v: k for k, v in NAME_TO_ID.items() if v != "huangshang"}
ID_TO_LABEL.setdefault("huangshang", "皇上")

# 合法流转对（基于标准链 + 实际业务需要）
LEGAL_FLOWS = {
    # ── 上行：皇上→太子→中书→门下→尚书→六部 ──
    ("皇上",   "太子"),
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
    ("尚书省", "太子"),     # 尚书省直接汇报太子

    # ── 六部内部消息（自己给自己发内部处理消息）──
    ("工部",   "工部"),
    ("兵部",   "兵部"),
    ("户部",   "户部"),
    ("礼部",   "礼部"),
    ("刑部",   "刑部"),
    ("吏部",   "吏部"),
    ("吏部_hr", "吏部_hr"),
}

# 部门 → 直接上级（断链时需要通知上级）
PARENT_MAP = {
    "中书省": "太子",
    "门下省": "中书省",
    "尚书省": "中书省",
    "工部":   "尚书省",
    "兵部":   "尚书省",
    "户部":   "尚书省",
    "礼部":   "尚书省",
    "刑部":   "尚书省",
    "吏部":   "尚书省",
    "吏部_hr": "尚书省",
}

# 标准流转链必须经过的环节（按顺序），用于跳步检测
REQUIRED_STEPS = [
    ("太子", "中书省"),
    ("中书省", "门下省"),
    ("门下省", "中书省"),   # 门下省必须返回中书省
    ("中书省", "尚书省"),   # 中书省必须转交尚书省
]


# ═══════════════════════════════════════════════════════════════════
#  数据读写
# ═══════════════════════════════════════════════════════════════════

def load_tasks():
    """安全读取 tasks_source.json"""
    try:
        text = TASKS_FILE.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception as e:
        log(f"读取任务文件失败: {e}")
        return []


def load_audit():
    """读取历史审计日志"""
    try:
        text = AUDIT_FILE.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return {"last_check": "", "violations": [], "notifications": []}


def save_audit(audit):
    """写入审计日志"""
    try:
        AUDIT_FILE.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"写入审计日志失败: {e}")


def load_exclude_list():
    """读取手动排除的任务 ID 列表"""
    try:
        text = EXCLUDE_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
        return set(data.get("excluded_tasks", []))
    except Exception:
        return set()


def save_tasks(tasks):
    """写入任务文件"""
    try:
        TASKS_FILE.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"写入任务文件失败: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Agent 唤醒与通知
# ═══════════════════════════════════════════════════════════════════

def normalize_name(raw):
    """将 flow_log 中的名称统一为 agent_id"""
    if not raw:
        return None
    stripped = raw.strip()
    return NAME_TO_ID.get(stripped, stripped.lower())


def wake_agent(agent_id, reason=""):
    """唤醒指定 Agent（异步，发送心跳消息）。返回 (success, detail)。"""
    if agent_id in ("huangshang", "皇上"):
        return False, "不唤醒皇上"
    label = ID_TO_LABEL.get(agent_id, agent_id)
    msg = (
        f"🔔 监察心跳通知\n"
        f"原因: {reason or '流程断链，需要你恢复在线'}\n"
        f"时间: {datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')}\n"
        f"请确认在线并继续处理待办任务。"
    )
    try:
        subprocess.Popen(
            ["openclaw", "sessions", "spawn", "--agent", agent_id, "--task", msg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"已唤醒 {label} ({agent_id})")
        return True, f"已向 {label} 发送唤醒消息"
    except Exception as e:
        log(f"唤醒 {label} ({agent_id}) 失败: {e}")
        return False, str(e)[:200]


def notify_agent(agent_id, message):
    """向指定 Agent 同步发送通知消息（确保会话创建）。返回 (success, detail)。"""
    if agent_id in ("huangshang", "皇上"):
        return False, "不通知皇上"
    label = ID_TO_LABEL.get(agent_id, agent_id)
    try:
        result = subprocess.run(
            ["openclaw", "sessions", "spawn", "--agent", agent_id, "--task", message],
            capture_output=True, text=True, timeout=30
        )
        success = result.returncode == 0
        detail = (result.stdout + "\n" + result.stderr).strip()[:300]
        log(f"通知 {label} ({'成功' if success else '失败'}): {detail[:100]}")
        return success, detail
    except subprocess.TimeoutExpired:
        log(f"通知 {label} ({agent_id}) 超时(30s)")
        return False, "命令执行超时(30s)"
    except Exception as e:
        log(f"通知 {label} ({agent_id}) 异常: {e}")
        return False, str(e)[:200]


def is_agent_awake(agent_id):
    """检查 Agent 是否醒着（最近 3 分钟内有文件活动）。"""
    if agent_id in ("huangshang", "皇上"):
        return True
    sessions_dir = OCLAW_HOME / "agents" / agent_id / "sessions"
    if not sessions_dir.exists():
        return False
    cutoff = time.time() - 180  # 3 分钟
    try:
        for f in sessions_dir.iterdir():
            if f.is_file() and f.stat().st_mtime > cutoff:
                return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════════════
#  流程校验核心逻辑
# ═══════════════════════════════════════════════════════════════════

def check_illegal_flow(task_id, flow_from, flow_to, index):
    """检查单条 flow 是否合法（越权检测）。返回违规描述或 None。"""
    pair = (flow_from, flow_to)
    if pair in LEGAL_FLOWS:
        return None
    return (
        f"越权调用：{flow_from} → {flow_to}（不在合法流转对表内）。"
        f"合法的上游调用链为：太子→中书省→门下省→中书省→尚书省→六部"
    )


def check_skip_steps(task_id, flow_log):
    """检查整个 flow_log 是否跳步（缺少必要环节）。返回违规列表。"""
    violations = []
    # 从 flow_log 中提取所有 (from, to) 对
    pairs = []
    for entry in flow_log:
        f = normalize_name(entry.get("from", ""))
        t = normalize_name(entry.get("to", ""))
        if f and t:
            pairs.append((f, t))

    # 检查必要步骤是否都出现过
    # 必须有：太子→中书省、中书省→门下省、门下省→中书省、中书省→尚书省
    for req_from, req_to in REQUIRED_STEPS:
        found = False
        for pair in pairs:
            pf = ID_TO_LABEL.get(pair[0], pair[0])
            pt = ID_TO_LABEL.get(pair[1], pair[1])
            if pf == req_from and pt == req_to:
                found = True
                break
        if not found:
            violations.append(f"流程跳步：缺少必要环节 {req_from} → {req_to}")

    return violations


def check_broken_chain(task_id, flow_log):
    """检查最后一条 flow 是否断链（目标部门 1 分钟内无回应）。返回断链信息或 None。"""
    if not flow_log:
        return None

    last = flow_log[-1]
    last_to = normalize_name(last.get("to", ""))
    last_at_str = last.get("at", "")

    # 不检查终点为皇上或太子的（太子以上的不由监察处理）
    if not last_to or last_to in ("huangshang", "皇上", "taizi"):
        # 如果最后流向太子或皇上，说明任务已回到上层，不断链
        # 但如果流向太子且太子还没回复皇上，也不算断链（太子不归监察管）
        return None

    # 解析最后一条 flow 的时间
    try:
        last_at = datetime.datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
    except Exception:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    elapsed = (now - last_at).total_seconds()

    if elapsed < BREAK_TIMEOUT_SEC:
        return None  # 还没超时

    # 检查目标部门是否有后续 flow 记录（作为 from 出现）
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
        return None  # 已经回应了

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


# ═══════════════════════════════════════════════════════════════════
#  自动归档
# ═══════════════════════════════════════════════════════════════════

def auto_archive_done_tasks(tasks, now_iso):
    """自动归档 Done/Cancelled 超过 AUTO_ARCHIVE_MINUTES 分钟且未归档的任务。"""
    archived_count = 0
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
            dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - dt).total_seconds() >= AUTO_ARCHIVE_MINUTES * 60:
                t["archived"] = True
                t["archivedAt"] = now_iso
                archived_count += 1
        except Exception:
            continue
    if archived_count > 0:
        save_tasks(tasks)
        log(f"自动归档 {archived_count} 个已完成任务")
    return archived_count


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def _is_recently_done(task):
    """判断任务是否在最近 RECENT_DONE_MINUTES 分钟内完成（用于速通逃逸检测）"""
    updated_at = task.get("updatedAt", "")
    if not updated_at:
        return False
    try:
        dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - dt).total_seconds() < RECENT_DONE_MINUTES * 60
    except Exception:
        return False


def _is_edict_task(task):
    """判断是否为旨意任务（JJC- 开头）"""
    task_id = task.get("id", "")
    return task_id.upper().startswith("JJC-")


def main():
    tasks = load_tasks()
    if not tasks:
        # 即使没有任务也写入审计日志（标记监察在运行）
        audit = load_audit()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        audit["last_check"] = now_iso
        audit["watched_tasks"] = []
        audit["watched_count"] = 0
        audit.setdefault("notifications", [])
        save_audit(audit)
        log("本轮检查完成，无任务")
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")

    # 加载排除列表
    exclude_list = load_exclude_list()

    # ── 自动归档 Done 超过 5 分钟的任务 ──
    auto_archive_done_tasks(tasks, now_iso)

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
    ]
    watched_tasks = []
    for t in truly_active:
        watched_tasks.append({
            "task_id": t.get("id", ""),
            "title": t.get("title", ""),
            "state": t.get("state", ""),
            "org": t.get("org", ""),
            "flow_count": len(t.get("flow_log", [])),
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

    for task in active:
        task_id = task.get("id", "?")
        title = task.get("title", "")
        flow_log = task.get("flow_log", [])

        if not flow_log:
            continue

        # ── 检查 1：越权调用（逐条检查）──
        for i, entry in enumerate(flow_log):
            f = normalize_name(entry.get("from", ""))
            t = normalize_name(entry.get("to", ""))
            if not f or not t:
                continue
            illegal = check_illegal_flow(task_id, f, t, i)
            if illegal:
                label_f = ID_TO_LABEL.get(f, f)
                label_t = ID_TO_LABEL.get(t, t)
                violation = {
                    "task_id": task_id,
                    "title": title,
                    "type": "越权调用",
                    "detail": f"{label_f} → {label_t}：{illegal}",
                    "flow_index": i,
                    "detected_at": now_iso,
                }
                new_violations.append(violation)

        # ── 检查 2：流程跳步 ──
        skips = check_skip_steps(task_id, flow_log)
        for skip_detail in skips:
            violation = {
                "task_id": task_id,
                "title": title,
                "type": "流程跳步",
                "detail": skip_detail,
                "detected_at": now_iso,
            }
            new_violations.append(violation)

        # ── 检查 3：断链超时 ──
        broken = check_broken_chain(task_id, flow_log)
        if broken:
            target_id = broken["target_agent_id"]
            target_label = broken["target_label"]
            from_label = broken["from_label"]
            parent_id = PARENT_MAP.get(target_label, PARENT_MAP.get(target_id))

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
                # 记录通知
                audit["notifications"].append({
                    "type": "断链唤醒",
                    "to": target_label,
                    "task_id": task_id,
                    "summary": f"{from_label}→{target_label} 断链，已唤醒",
                    "sent_at": now_iso,
                    "status": "sent" if wake_ok else "failed",
                    "detail": wake_detail,
                })

            # ── 介入处理：检查直接上级 + 通知 ──
            if parent_id:
                parent_label = ID_TO_LABEL.get(parent_id, parent_id)
                if not is_agent_awake(parent_id) and parent_id not in woken_agents:
                    wake_ok, wake_detail = wake_agent(parent_id, f"任务 {task_id} 流程断链，需要你重新派发给{target_label}")
                    woken_agents.add(parent_id)
                    audit["notifications"].append({
                        "type": "断链唤醒",
                        "to": parent_label,
                        "task_id": task_id,
                        "summary": f"唤醒上级{parent_label}重新派发",
                        "sent_at": now_iso,
                        "status": "sent" if wake_ok else "failed",
                        "detail": wake_detail,
                    })

                # 通知上级重新派发（无论上级是否刚被唤醒，都发通知）
                notify_msg = (
                    f"🛡️ 监察通知\n"
                    f"任务ID: {task_id}\n"
                    f"任务标题: {title}\n"
                    f"问题: {target_label} 已超时未接旨（{broken['elapsed_sec'] // 60} 分钟无回应）\n"
                    f"处理: 监察已将 {target_label} 唤醒\n"
                    f"行动: 请 {parent_label} 重新通过 sessions_spawn 向 {target_label} 派发任务\n"
                    f"⚠️ 监察不会派发任务，请 {parent_label} 执行派发。"
                )
                notify_ok, notify_detail = notify_agent(parent_id, notify_msg)
                audit["notifications"].append({
                    "type": "断链通知",
                    "to": parent_label,
                    "task_id": task_id,
                    "summary": f"{target_label}超时未接旨，通知{parent_label}重新派发",
                    "sent_at": now_iso,
                    "status": "sent" if notify_ok else "failed",
                    "detail": notify_detail,
                })

    # ── 越权违规：同步通知太子（只在会话中发送越权通报，不发跳步）──
    serious = [v for v in new_violations if v["type"] == "越权调用"]
    if serious:
        lines = []
        for v in serious:
            lines.append(f"  - {v['task_id']}: {v['detail']}")
        summary = "\n".join(lines)
        notify_msg = (
            f"🛡️ 监察通报 — 越权违规\n"
            f"发现 {len(serious)} 项越权违规：\n\n"
            f"{summary}\n\n"
            f"请太子核实并纠正。"
        )
        notify_ok, notify_detail = notify_agent("taizi", notify_msg)
        audit["notifications"].append({
            "type": "越权通报",
            "to": "太子",
            "task_ids": [v["task_id"] for v in serious],
            "summary": f"发现{len(serious)}项越权违规",
            "sent_at": now_iso,
            "status": "sent" if notify_ok else "failed",
            "detail": notify_detail,
        })

    # ── 写入审计日志 ──
    if new_violations:
        audit["violations"].extend(new_violations)
        # 只保留最近 200 条记录
        audit["violations"] = audit["violations"][-200:]
    audit["last_check"] = now_iso
    audit["watched_tasks"] = watched_tasks
    audit["watched_count"] = len(watched_tasks)
    audit["check_count"] = audit.get("check_count", 0) + 1
    audit["total_violations"] = audit.get("total_violations", 0) + len(new_violations)
    # 只保留最近 100 条通知记录
    audit["notifications"] = audit["notifications"][-100:]

    save_audit(audit)

    if new_violations:
        log(f"本轮检查完成，检查 {len(active)} 个任务（{len(watched_tasks)} 活跃），发现 {len(new_violations)} 项问题")
    else:
        log(f"本轮检查完成，{len(watched_tasks)} 个活跃任务均正常")


if __name__ == "__main__":
    main()
