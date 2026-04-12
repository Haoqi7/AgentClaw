#!/usr/bin/env python3
"""
kanban_commands.py - AgentClaw 看板命令协议处理模块

提供看板消息的增删改查接口，是 V8 架构"看板即总线"的核心组件。
所有跨 Agent 通信都必须通过本模块写入看板，编排引擎再从看板读取并派发。

接口函数:
    add_message(task_id, msg_type, from_agent, to_agent, content, structured=None)
        -> str: 向看板添加一条消息，返回消息ID
    get_unread_messages(kanban_data, task_id)
        -> list: 获取指定任务的未读消息
    mark_message_read(kanban_data, msg_id)
        -> bool: 标记消息为已读
    get_messages_for_agent(kanban_data, task_id, agent_id)
        -> list: 获取指定 Agent 的未读消息
    get_pending_questions(kanban_data, task_id)
        -> list: 获取待回答的问题
    mark_question_answered(kanban_data, task_id, question_id=None)
        -> bool: 标记问题为已回答
    get_task_state(kanban_data, task_id)
        -> str|None: 获取任务当前状态
    update_task_state(task_id, new_state)
        -> None: 更新任务状态
    log_flow(task_id, from_agent, to_agent, description)
        -> None: 写入 flow_log 流转记录
    find_task(kanban_data, task_id)
        -> dict|None: 在看板数据中查找指定任务

V8 架构原则:
    - 看板是唯一的信息通道
    - 所有消息通过本模块写入，编排引擎负责读取和派发
    - 消息持久化到文件，不怕丢失
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 尝试导入项目内部的 file_lock 模块（跨平台文件锁）
try:
    from file_lock import atomic_json_read, atomic_json_update
    _USE_FILE_LOCK = True
except ImportError:
    _USE_FILE_LOCK = False

# 导入配置中心
from config import KANBAN_PATH, MESSAGE_TYPES

logger = logging.getLogger("kanban_commands")

CST = timezone(timedelta(hours=8))


# ====================================================================
# 内部辅助函数
# ====================================================================

def _now_iso():
    """返回北京时间 ISO 8601 时间字符串"""
    return datetime.now(CST).isoformat()


def _load_kanban():
    """带文件锁的看板读取。

    优先使用 file_lock.atomic_json_read（跨平台安全），
    降级使用 Linux fcntl 锁。
    兼容新旧两种文件格式。
    """
    if _USE_FILE_LOCK:
        data = atomic_json_read(KANBAN_PATH, {"tasks": [], "global_counters": {}})
        if isinstance(data, list):
            return {"tasks": data, "global_counters": {}}
        return data

    # 降级实现：Linux fcntl
    if not KANBAN_PATH.exists():
        return {"tasks": [], "global_counters": {}}
    import fcntl
    with open(KANBAN_PATH, 'r', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            data = json.load(f)
        except Exception:
            data = {"tasks": [], "global_counters": {}}
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    # 兼容旧格式
    if isinstance(data, list):
        return {"tasks": data, "global_counters": {}}
    return data


def _save_kanban(data):
    """带文件锁的看板写入。

    优先使用 file_lock.atomic_json_write（跨平台安全），
    降级使用 Linux fcntl + 临时文件原子写入。
    """
    if _USE_FILE_LOCK:
        atomic_json_write(KANBAN_PATH, data)
        return

    # 降级实现：Linux fcntl + tmpfile
    import fcntl
    KANBAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(KANBAN_PATH.parent), suffix='.tmp', prefix='kanban_'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 持排他锁后原子替换
        lock_path = KANBAN_PATH.parent / (KANBAN_PATH.name + '.lock')
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            os.replace(tmp_path, str(KANBAN_PATH))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _next_message_id(kanban_data):
    """生成下一个全局唯一消息ID。

    从 global_counters.message_id 读取当前计数器并递增。
    返回格式: "msg-0001"
    """
    counter = kanban_data.get("global_counters", {}).get("message_id", 0) + 1
    kanban_data.setdefault("global_counters", {})["message_id"] = counter
    return f"msg-{counter:04d}"


def find_task(kanban_data, task_id):
    """在看板数据中查找指定任务。

    Args:
        kanban_data: 看板数据字典（由 _load_kanban 返回）
        task_id: 任务ID（如 "JJC-20260412-001"）

    Returns:
        dict | None: 任务对象，不存在时返回 None
    """
    if not kanban_data:
        return None
    for task in kanban_data.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


# ====================================================================
# 消息操作接口
# ====================================================================

def add_message(task_id, msg_type, from_agent, to_agent, content, structured=None):
    """向看板添加一条消息。

    这是 Agent 跨部门通信的唯一入口。消息写入看板的 kanban_messages 数组，
    编排引擎在轮询时读取未读消息并根据类型执行路由。

    Args:
        task_id: 任务ID（如 "JJC-20260412-001"）
        msg_type: 消息类型，必须是 MESSAGE_TYPES 之一
            approve/reject/assign/done/report/ask/answer/escalate/redirect
        from_agent: 发送者 Agent ID（如 "menxia"）
        to_agent: 接收者 Agent ID（如 "shangshu"）
        content: 人类可读的消息内容（如 "准奏，方案可行"）
        structured: 结构化数据字典（可选，编排引擎主要解析此字段）

    Returns:
        str: 消息ID（如 "msg-0001"）

    Raises:
        ValueError: 消息类型无效或任务不存在
    """
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"无效的消息类型: {msg_type}，必须是 {MESSAGE_TYPES}")

    if structured is None:
        structured = {}

    # 原子读取 -> 修改 -> 写回
    _created_msg_id = [None]  # 闭包捕获 msg_id

    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        msg_id = _next_message_id(data)
        _created_msg_id[0] = msg_id
        now = _now_iso()

        message = {
            "id": msg_id,
            "type": msg_type,
            "task_id": task_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "structured": structured,
            "timestamp": now,
            "read": False,
            "retry_count": 0,
        }

        task.setdefault("kanban_messages", []).append(message)
        task["last_activity"] = now

        logger.info(f"[kanban] 消息已写入: {msg_id} | {msg_type} | "
                     f"{from_agent} -> {to_agent} | task={task_id}")
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})

    # 直接返回闭包中捕获的 msg_id，无需重新读取文件
    return _created_msg_id[0]


def get_unread_messages(kanban_data, task_id):
    """获取指定任务的未读消息列表。

    Args:
        kanban_data: 看板数据字典
        task_id: 任务ID

    Returns:
        list: 未读消息列表（每项为一个消息字典）
    """
    task = find_task(kanban_data, task_id)
    if not task:
        return []
    return [m for m in task.get("kanban_messages", []) if not m.get("read", False)]


def mark_message_read(kanban_data, msg_id):
    """标记消息为已读。

    直接修改传入的 kanban_data 对象（内存中的副本），
    调用方需要在后续自行持久化。

    Args:
        kanban_data: 看板数据字典（会被就地修改）
        msg_id: 消息ID（如 "msg-0001"）

    Returns:
        bool: 是否成功标记
    """
    if not kanban_data:
        return False
    for task in kanban_data.get("tasks", []):
        for msg in task.get("kanban_messages", []):
            if msg.get("id") == msg_id:
                msg["read"] = True
                return True
    return False


def get_messages_for_agent(kanban_data, task_id, agent_id):
    """获取指定 Agent 的未读消息（to_agent 匹配）。

    Args:
        kanban_data: 看板数据字典
        task_id: 任务ID
        agent_id: 目标 Agent ID

    Returns:
        list: 该 Agent 的未读消息列表
    """
    task = find_task(kanban_data, task_id)
    if not task:
        return []
    return [
        m for m in task.get("kanban_messages", [])
        if not m.get("read", False) and m.get("to_agent") == agent_id
    ]


# ====================================================================
# 问题操作接口
# ====================================================================

def get_pending_questions(kanban_data, task_id):
    """获取待回答的问题列表。

    Args:
        kanban_data: 看板数据字典
        task_id: 任务ID

    Returns:
        list: 未回答的问题列表
    """
    task = find_task(kanban_data, task_id)
    if not task:
        return []
    return [q for q in task.get("pendingQuestions", []) if not q.get("answered", False)]


def mark_question_answered(kanban_data, task_id, question_id=None):
    """标记问题为已回答。

    直接修改传入的 kanban_data 对象（内存中的副本）。

    Args:
        kanban_data: 看板数据字典（会被就地修改）
        task_id: 任务ID
        question_id: 问题ID（可选，不传则标记第一个未回答的问题）

    Returns:
        bool: 是否成功标记
    """
    task = find_task(kanban_data, task_id)
    if not task:
        return False
    for q in task.get("pendingQuestions", []):
        if question_id and q.get("id") == question_id:
            q["answered"] = True
            return True
        elif not question_id and not q.get("answered", False):
            q["answered"] = True
            return True
    return False


# ====================================================================
# 任务状态操作接口
# ====================================================================

def get_task_state(kanban_data, task_id):
    """获取任务当前状态。

    Args:
        kanban_data: 看板数据字典
        task_id: 任务ID

    Returns:
        str | None: 任务状态名称（如 "Zhongshu"），不存在时返回 None
    """
    task = find_task(kanban_data, task_id)
    return task.get("state") if task else None


def update_task_state(task_id, new_state):
    """更新任务状态（原子操作）。

    直接修改看板文件中的任务状态字段，同时更新 last_activity 时间戳。
    此函数由编排引擎调用，用于状态机驱动的状态变更。

    Args:
        task_id: 任务ID
        new_state: 新状态名称（如 "Zhongshu", "Menxia"）

    Raises:
        ValueError: 任务不存在
    """
    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        old_state = task.get("state", "")
        task["state"] = new_state
        task["last_activity"] = _now_iso()
        task["updatedAt"] = _now_iso()
        logger.info(f"[kanban] 状态更新: {task_id} {old_state} -> {new_state}")
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})


# ====================================================================
# 流转日志接口
# ====================================================================

def log_flow(task_id, from_agent, to_agent, description):
    """写入 flow_log 流转记录。

    每次任务状态变化或消息路由都会写入一条 flow_log，
    供看板前端展示和御史台审计使用。

    Args:
        task_id: 任务ID
        from_agent: 来源标识（Agent ID 或 "system"）
        to_agent: 目标标识（Agent ID 或状态名）
        description: 流转描述（人类可读）
    """
    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            logger.warning(f"[kanban] log_flow: 任务不存在 {task_id}")
            return data

        flow_counter = data.get("global_counters", {}).get("flow_id", 0) + 1
        data.setdefault("global_counters", {})["flow_id"] = flow_counter

        flow_entry = {
            "id": f"flow-{flow_counter:04d}",
            "task_id": task_id,
            "from": from_agent,
            "to": to_agent,
            "remark": description,
            "at": _now_iso(),
        }

        task.setdefault("flow_log", []).append(flow_entry)
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})
    logger.info(f"[kanban] flow_log: {task_id} | {from_agent} -> {to_agent} | {description}")


# ====================================================================
# Agent 日志接口
# ====================================================================

def append_agent_log(task_id, agent_id, text):
    """向任务的 agentLog 数组追加一条日志记录。

    Agent 的自由文本输出（方案B）通过此接口写入看板，
    供御史台关键词扫描和全流程审计使用。

    Args:
        task_id: 任务ID
        agent_id: Agent ID
        text: 日志文本内容
    """
    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            return data

        log_entry = {
            "agent": agent_id,
            "text": text,
            "at": _now_iso(),
        }
        task.setdefault("agentLog", []).append(log_entry)
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})


# ====================================================================
# 审计标记接口
# ====================================================================

def add_audit_flag(task_id, flag_type, message):
    """向任务添加一条审计标记。

    御史台检测到异常时调用此接口写入 auditFlags 数组。

    Args:
        task_id: 任务ID
        flag_type: 标记类型（如 "override", "stall", "loop"）
        message: 审计描述
    """
    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            return data

        flag_entry = {
            "type": flag_type,
            "msg": message,
            "at": _now_iso(),
        }
        task.setdefault("auditFlags", []).append(flag_entry)
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})
    logger.info(f"[kanban] audit_flag: {task_id} | {flag_type} | {message}")


# ====================================================================
# 派发状态记录接口
# ====================================================================

def record_dispatch_status(task_id, agent_id, status):
    """记录编排引擎的派发状态。

    Args:
        task_id: 任务ID
        agent_id: 目标 Agent ID
        status: 派发状态（"success" / "failed" / "queued"）
    """
    def _modifier(data):
        task = find_task(data, task_id)
        if not task:
            return data
        task["lastDispatchStatus"] = status
        task["lastDispatchTime"] = _now_iso()
        task["lastDispatchAgent"] = agent_id
        return data

    atomic_json_update(KANBAN_PATH, _modifier, {"tasks": [], "global_counters": {}})
