#!/usr/bin/env python3
"""
三省六部 · 监察脚本 (pipeline_watchdog.py)

定期扫描 tasks_source.json，校验每个活跃任务的 flow_log 流程合法性。

检测三类问题：
  1. 越权调用 — from→to 不在合法流转对表内
  2. 流程跳步 — 标准链缺少必要环节
  3. 断链超时 — 最后一条 flow 的目标部门 2 分钟内无回应

处理方式：
  - 越权/跳步 → 写入审计日志 + 通知太子
  - 断链/超时 → 唤醒目标部门 + 检查直接上级 + 通知上级重新派发

用法：由 run_loop.sh 每 2 分钟调用一次，也可手动运行：
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
OCLAW_HOME = pathlib.Path.home() / ".openclaw"

# ── 超时阈值（秒）─────────────────────────────────────────────────
BREAK_TIMEOUT_SEC = 120  # 2 分钟无回应判定为断链

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

# 合法流转对（基于标准链：皇上→太子→中书→门下→中书→尚书→六部→尚书→中书→太子→皇上）
LEGAL_FLOWS = {
    ("皇上",   "太子"),
    ("太子",   "中书省"),
    ("中书省", "门下省"),
    ("门下省", "中书省"),   # 封驳退回
    ("中书省", "尚书省"),
    ("尚书省", "工部"),
    ("尚书省", "兵部"),
    ("尚书省", "户部"),
    ("尚书省", "礼部"),
    ("尚书省", "刑部"),
    ("尚书省", "吏部"),
    ("尚书省", "吏部_hr"),
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
        return {"last_check": "", "violations": []}


def save_audit(audit):
    """写入审计日志"""
    try:
        AUDIT_FILE.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"写入审计日志失败: {e}")


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
    """唤醒指定 Agent（发送心跳消息）。失败不抛异常。"""
    if agent_id in ("huangshang", "皇上"):
        return False
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
        return True
    except Exception as e:
        log(f"唤醒 {label} ({agent_id}) 失败: {e}")
        return False


def notify_agent(agent_id, message):
    """向指定 Agent 发送通知消息。"""
    if agent_id in ("huangshang", "皇上"):
        return False
    label = ID_TO_LABEL.get(agent_id, agent_id)
    try:
        subprocess.Popen(
            ["openclaw", "sessions", "spawn", "--agent", agent_id, "--task", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"已通知 {label} ({agent_id})")
        return True
    except Exception as e:
        log(f"通知 {label} ({agent_id}) 失败: {e}")
        return False


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
    """检查最后一条 flow 是否断链（目标部门 2 分钟内无回应）。返回断链信息或 None。"""
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
    for entry in flow_log[:-1]:  # 排除最后一条（它自己就是发出记录）
        pass
    # 更好的检查：看有没有 from == last_to 的记录且时间在 last_at 之后
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
#  主流程
# ═══════════════════════════════════════════════════════════════════

def main():
    tasks = load_tasks()
    if not tasks:
        return  # 没有任务，直接退出

    # 过滤活跃任务
    active = [t for t in tasks if t.get("state") not in ("Done", "Cancelled")]
    if not active:
        return  # 没有活跃任务，直接退出

    audit = load_audit()
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
                    "detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
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
                "detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
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
                "detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            new_violations.append(violation)

            # ── 介入处理：唤醒目标部门 ──
            if target_id and target_id not in woken_agents:
                wake_agent(target_id, f"任务 {task_id} 流程断链，{from_label}已等你 {broken['elapsed_sec'] // 60} 分钟")
                woken_agents.add(target_id)

            # ── 介入处理：检查直接上级 + 通知 ──
            if parent_id:
                parent_label = ID_TO_LABEL.get(parent_id, parent_id)
                if not is_agent_awake(parent_id) and parent_id not in woken_agents:
                    wake_agent(parent_id, f"任务 {task_id} 流程断链，需要你重新派发给{target_label}")
                    woken_agents.add(parent_id)

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
                notify_agent(parent_id, notify_msg)

    # ── 越权/跳步：统一通知太子 ──
    serious = [v for v in new_violations if v["type"] in ("越权调用", "流程跳步")]
    if serious:
        lines = []
        for v in serious:
            lines.append(f"  - {v['task_id']}: {v['detail']}")
        summary = "\n".join(lines)
        notify_msg = (
            f"🛡️ 监察通报 — 流程违规\n"
            f"发现 {len(serious)} 项违规：\n\n"
            f"{summary}\n\n"
            f"请太子核实并纠正。"
        )
        notify_agent("taizi", notify_msg)

    # ── 写入审计日志 ──
    if new_violations:
        audit["violations"].extend(new_violations)
        # 只保留最近 200 条记录
        audit["violations"] = audit["violations"][-200:]
    audit["last_check"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    save_audit(audit)

    if new_violations:
        log(f"本轮检查完成，发现 {len(new_violations)} 项问题")
    else:
        log(f"本轮检查完成，{len(active)} 个活跃任务均正常")


if __name__ == "__main__":
    main()
