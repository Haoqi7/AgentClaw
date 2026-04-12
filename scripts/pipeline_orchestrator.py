#!/usr/bin/env python3
"""
pipeline_orchestrator.py — AgentClaw 看板编排引擎

负责扫描看板变化、决定下一步动作、调用openclaw agent派发通知。
这是V8架构的核心组件，替代server.py中的dispatch_for_state。

核心设计原则:
    - 看板是唯一的信息通道（kanban_commands.py 负责所有数据操作）
    - 确定性派发：由程序决定通知谁、何时通知，不依赖 LLM
    - 异步主循环：asyncio + ThreadPoolExecutor，不阻塞扫描
    - 启动恢复：重启后自动恢复未完成的任务
    - 停滞检测：3分钟催办、6分钟上报监察
    - 封驳上限：超过5次自动强制准奏

用法:
    python3 pipeline_orchestrator.py
    python3 pipeline_orchestrator.py --interval 10
    python3 pipeline_orchestrator.py --once  # 单次扫描后退出（调试用）

V8 架构：看板即消息总线，编排引擎是唯一的消息消费者和Agent唤醒者。
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 将 scripts 目录加入 sys.path，确保能导入同目录的模块
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from config import (
    KANBAN_PATH, FLOW_LOG_PATH, POLL_INTERVAL, DEFAULT_AGENT_TIMEOUT,
    MAX_NOTIFY_RETRIES, NOTIFY_RETRY_DELAY, STALE_WARNING_TIMEOUT,
    STALE_ESCALATE_TIMEOUT, DOING_PROGRESS_TIMEOUT, MAX_REJECT_COUNT,
    MAX_CONCURRENT_DISPATCH, ALL_AGENTS, MINISTRY_AGENTS, STATE_AGENT_MAP,
    MESSAGE_TYPES, OPENCLAW_BIN, LOG_LEVEL, LOG_FORMAT, TERMINAL_STATES,
)
from agent_notifier import notify_agent, notify_agent_with_retry, notify_agent_async
from kanban_commands import (
    add_message, get_unread_messages, mark_message_read,
    get_pending_questions, mark_question_answered,
    get_task_state, update_task_state, log_flow, find_task,
)

logger = logging.getLogger("orchestrator")

CST = timezone(timedelta(hours=8))


class Orchestrator:
    """看板编排引擎主类。

    轮询 tasks_source.json，检测状态变化和未读消息，
    根据九状态机和九命令协议执行对应的派发逻辑。
    """

    def __init__(self, poll_interval=None):
        self.poll_interval = poll_interval or POLL_INTERVAL
        self.running = True
        self.last_snapshot = {}  # task_id -> state 快照
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DISPATCH)
        self._kanban_lock = asyncio.Lock()
        self._stale_warned = set()  # 已催办的 task_id 集合（避免重复催办）
        self._stale_escalated = set()  # 已上报的 task_id 集合

    # ─────────────────────────────────────
    # 主循环
    # ─────────────────────────────────────
    async def run(self):
        """编排引擎主入口。"""
        logger.info(f"[Orchestrator] 启动，轮询间隔 {self.poll_interval}s")
        logger.info(f"[Orchestrator] 看板路径: {KANBAN_PATH}")

        # 启动恢复：检查未完成任务
        await self._startup_recovery()

        while self.running:
            try:
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"[Orchestrator] 轮询异常: {e}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def run_once(self):
        """单次轮询（调试用）。"""
        await self._poll_cycle()

    async def _poll_cycle(self):
        """单次轮询周期：检查所有任务的4类事件。"""
        kanban = self._load_kanban()

        tasks = kanban.get("tasks", [])

        for task in tasks:
            task_id = task.get("id")
            current_state = task.get("state", "")

            # 跳过终态
            if current_state in TERMINAL_STATES:
                continue

            prev_state = self.last_snapshot.get(task_id, "")

            # 1. 检测状态变化
            if current_state != prev_state or task_id not in self.last_snapshot:
                logger.info(f"[Orchestrator] 状态变化: {task_id} "
                           f"{prev_state or '(新任务)'} -> {current_state}")
                await self._handle_state_change(task, prev_state, current_state)

            # 2. 检查未读消息
            unread = get_unread_messages(kanban, task_id)
            if unread:
                for msg in unread:
                    await self._route_message(task_id, current_state, msg)
                    mark_message_read(kanban, msg["id"])
                self._save_kanban(kanban)

            # 3. 检查待回答问题
            questions = get_pending_questions(kanban, task_id)
            for q in questions:
                if not q.get("answered", False):
                    await self._handle_pending_question(task_id, q)

            # 4. 检查停滞
            await self._check_staleness(task_id, current_state, task)

            # 5. 更新快照
            self.last_snapshot[task_id] = current_state

    # ─────────────────────────────────────
    # 状态变化处理（核心路由）
    # ─────────────────────────────────────
    async def _handle_state_change(self, task, prev_state, new_state):
        """根据状态变化执行对应的派发动作。

        遵循第九章路由规则详表（section 9.1）。
        """
        task_id = task["id"]

        # 新任务（首次出现）
        if task_id not in self.last_snapshot:
            target = STATE_AGENT_MAP.get(new_state)
            if target:
                msg = self._build_message(new_state, task)
                session = f"{task_id}-{target}"
                await notify_agent_async(target, msg, session)
                log_flow(task_id, "system", target, f"新任务创建，通知{target}")
            return

        # 状态转换分发表（section 9.1）
        dispatch_map = {
            ("Taizi", "Zhongshu"):      ("zhongshu", "太子分拣完成，请起草方案"),
            ("Menxia", "Zhongshu"):     ("zhongshu", "门下省封驳，请修改方案"),
            ("Menxia", "Assigned"):     ("shangshu", "门下省准奏，请派发六部"),
            ("Zhongshu", "Menxia"):     ("menxia", "中书省方案完成，请审议"),
            ("Zhongshu", "Assigned"):   ("shangshu", "中书省转交，请派发六部"),
            ("Assigned", "Doing"):      (task.get("current_handler", "shangshu"), "任务已派发，请执行"),
            ("Doing", "Review"):        ("shangshu", "六部完成，请审查汇总"),
            ("Review", "Zhongshu_Final"): ("zhongshu", "尚书省汇总完成，请撰写回奏"),
            ("Zhongshu_Final", "Done"): ("taizi", "中书省回奏完成，请回奏皇上"),
            ("Zhongshu_Final", "Zhongshu"): ("zhongshu", "回奏需要修改，请重新撰写"),
            ("Blocked", "Doing"):       (task.get("current_handler", "shangshu"), "任务已恢复，请继续执行"),
        }

        key = (prev_state, new_state)
        if key in dispatch_map:
            target_agent, message = dispatch_map[key]
            session = f"{task_id}-{target_agent}"
            await notify_agent_async(target_agent, message, session)
            log_flow(task_id, prev_state, new_state,
                     f"状态变化，派发到{target_agent}")
            logger.info(f"[Orchestrator] 派发: {task_id} {prev_state}->{new_state} -> {target_agent}")
        else:
            logger.warning(f"[Orchestrator] 未注册的状态变化: {task_id} {prev_state}->{new_state}")

    # ─────────────────────────────────────
    # 消息路由（section 9.2-9.3）
    # ─────────────────────────────────────
    async def _route_message(self, task_id, current_state, message):
        """根据消息类型和当前状态执行路由逻辑。

        优先级（section 9.3）:
            1. redirect（监察纠正）
            2. escalate（异常上报）
            3. ask / answer（对话消息）
            4. approve / reject（状态转换）
            5. assign / done / report（任务流转）
        """
        msg_type = message["type"]
        from_agent = message["from_agent"]
        to_agent = message["to_agent"]
        structured = message.get("structured", {})
        action = structured.get("action", "")

        logger.info(f"[Orchestrator] 消息路由: {task_id} | 状态 {current_state} | "
                    f"类型 {msg_type} | {from_agent} -> {to_agent}")

        routed = False

        # 1. redirect（最高优先级 — 监察纠正）
        if msg_type == "redirect" and from_agent == "jiancha":
            await self._handle_redirect(task_id, message)
            routed = True

        # 2. escalate（异常上报）
        elif msg_type == "escalate":
            await self._handle_escalate(task_id, message)
            routed = True

        # 3. ask（请示）
        elif msg_type == "ask":
            await self._handle_ask(task_id, message)
            routed = True

        # 4. answer（回复）
        elif msg_type == "answer":
            await self._handle_answer(task_id, message)
            routed = True

        # 5. approve（准奏）
        elif msg_type == "approve":
            routed = await self._handle_approve(task_id, current_state, message)

        # 6. reject（封驳）
        elif msg_type == "reject":
            routed = await self._handle_reject(task_id, current_state, message)

        # 7. assign（派发）
        elif msg_type == "assign":
            routed = await self._handle_assign(task_id, current_state, message)

        # 8. done（完成）
        elif msg_type == "done":
            routed = await self._handle_done(task_id, current_state, message)

        # 9. report（汇总报告）
        elif msg_type == "report":
            routed = await self._handle_report(task_id, current_state, message)

        if not routed:
            logger.warning(f"[Orchestrator] 未匹配路由: {task_id} {current_state} "
                          f"{msg_type} from {from_agent}")

    # ─────────────────────────────────────
    # 9种消息类型处理器
    # ─────────────────────────────────────

    async def _handle_approve(self, task_id, state, msg):
        """处理门下省准奏。

        条件: 当前状态 == Menxia 且来自 menxia
        动作: 状态 -> Assigned，通知尚书省派发
        """
        if state != "Menxia" or msg["from_agent"] != "menxia":
            return False
        update_task_state(task_id, "Assigned")
        log_flow(task_id, "menxia", "shangshu", "门下省准奏")
        session = f"{task_id}-shangshu"
        await notify_agent_async(
            "shangshu", self._build_message("Assigned", msg), session)
        return True

    async def _handle_reject(self, task_id, state, msg):
        """处理门下省封驳（含封驳上限检查）。

        条件: 当前状态 == Menxia 且来自 menxia
        动作: reviewRound < 5 -> 状态回 Zhongshu；>= 5 -> 强制准奏
        """
        if state != "Menxia" or msg["from_agent"] != "menxia":
            return False
        reject_count = self._get_reject_count(task_id)
        if reject_count >= MAX_REJECT_COUNT:
            logger.warning(
                f"[Orchestrator] 封驳超限({reject_count}>={MAX_REJECT_COUNT})，"
                f"强制准奏")
            await self._force_approve(task_id, msg)
        else:
            self._increment_reject_count(task_id)
            update_task_state(task_id, "Zhongshu")
            log_flow(task_id, "menxia", "zhongshu",
                     f"门下省封驳(第{reject_count + 1}次)")
            session = f"{task_id}-zhongshu"
            await notify_agent_async(
                "zhongshu", self._build_message("Zhongshu", msg), session)
        return True

    async def _handle_assign(self, task_id, state, msg):
        """处理尚书省派发。

        条件: 当前状态 == Assigned 且来自 shangshu
        动作: 状态 -> Doing，设置 org，通知目标六部
        """
        if state != "Assigned" or msg["from_agent"] != "shangshu":
            return False
        dept = msg.get("to_agent") or msg.get("structured", {}).get("dept")
        if not dept or dept not in MINISTRY_AGENTS:
            logger.warning(f"[Orchestrator] 派发目标无效: {dept}")
            return False
        update_task_state(task_id, "Doing")
        log_flow(task_id, "shangshu", dept, f"尚书省派发任务给{dept}")
        session = f"{task_id}-{dept}"
        await notify_agent_async(
            dept, self._build_message("Doing", msg), session)
        return True

    async def _handle_done(self, task_id, state, msg):
        """处理六部完成。

        条件: 当前状态 == Doing 且来自六部 Agent
        动作: 检查是否全部完成；全部完成 -> 状态 Review，通知尚书省
        """
        if state != "Doing" or msg["from_agent"] not in MINISTRY_AGENTS:
            return False
        log_flow(task_id, msg["from_agent"], "shangshu",
                 f"{msg['from_agent']}完成任务")
        if self._all_ministries_done(task_id):
            update_task_state(task_id, "Review")
            log_flow(task_id, "system", "shangshu",
                     "六部全部完成，通知尚书省审查")
            session = f"{task_id}-shangshu"
            await notify_agent_async(
                "shangshu", self._build_message("Review", msg), session)
        return True

    async def _handle_report(self, task_id, state, msg):
        """处理汇总报告（尚书省/中书省/太子/中书省回奏）。

        路由规则（section 9.2）:
            Review + shangshu -> Zhongshu_Final
            Taizi + taizi + forward_edict -> Zhongshu
            Zhongshu + zhongshu + draft_proposal -> Menxia
            Zhongshu + zhongshu + forward_to_shangshu -> Assigned
            Zhongshu + zhongshu + report_to_taizi -> Done
        """
        from_agent = msg["from_agent"]
        action = msg.get("structured", {}).get("action", "")

        # 尚书省汇总 -> Zhongshu_Final
        if state == "Review" and from_agent == "shangshu":
            update_task_state(task_id, "Zhongshu_Final")
            log_flow(task_id, "shangshu", "zhongshu", "尚书省汇总完成")
            session = f"{task_id}-zhongshu"
            await notify_agent_async(
                "zhongshu", self._build_message("Zhongshu_Final", msg), session)
            return True

        # 太子分拣 -> Zhongshu
        if state == "Taizi" and from_agent == "taizi" and action == "forward_edict":
            update_task_state(task_id, "Zhongshu")
            log_flow(task_id, "taizi", "zhongshu", "太子分拣完成，转交中书省")
            session = f"{task_id}-zhongshu"
            await notify_agent_async(
                "zhongshu", self._build_message("Zhongshu", msg), session)
            return True

        # 中书省起草方案 -> Menxia
        if state == "Zhongshu" and from_agent == "zhongshu" and action == "draft_proposal":
            update_task_state(task_id, "Menxia")
            log_flow(task_id, "zhongshu", "menxia", "中书省起草方案完成，提交审议")
            session = f"{task_id}-menxia"
            await notify_agent_async(
                "menxia", self._build_message("Menxia", msg), session)
            return True

        # 中书省直接转交尚书省 -> Assigned
        if state == "Zhongshu" and from_agent == "zhongshu" and action == "forward_to_shangshu":
            update_task_state(task_id, "Assigned")
            log_flow(task_id, "zhongshu", "shangshu", "中书省转交尚书省")
            session = f"{task_id}-shangshu"
            await notify_agent_async(
                "shangshu", self._build_message("Assigned", msg), session)
            return True

        # 中书省回奏太子 -> Done
        if state == "Zhongshu_Final" and from_agent == "zhongshu" and action == "report_to_taizi":
            update_task_state(task_id, "Done")
            log_flow(task_id, "zhongshu", "taizi", "中书省回奏完成")
            session = f"{task_id}-taizi"
            await notify_agent_async(
                "taizi", self._build_message("Done", msg), session)
            return True

        return False

    async def _handle_redirect(self, task_id, msg):
        """处理御史台纠正（redirect命令）。

        优先级最高，任何状态下均可触发。
        动作: 记录 flow_log，通知被纠正的 Agent
        """
        target = msg["to_agent"]
        reason = msg.get("content", "")
        log_flow(task_id, "jiancha", target, f"御史台纠正: {reason}")
        session = f"{task_id}-{target}"
        await notify_agent_async(
            target, f"[监察纠正] {reason}", session)
        logger.info(f"[Orchestrator] redirect: {task_id} jiancha -> {target}")

    async def _handle_escalate(self, task_id, msg):
        """处理异常上报。

        任何状态下均可触发。
        动作: 记录 flow_log，通知目标 Agent
        """
        log_flow(task_id, msg["from_agent"], msg["to_agent"],
                 f"异常上报: {msg['content']}")
        session = f"{task_id}-{msg['to_agent']}"
        await notify_agent_async(
            msg["to_agent"], f"[异常上报] {msg['content']}", session)

    async def _handle_ask(self, task_id, msg):
        """处理请示（ask命令）。

        任何状态下均可触发。
        动作: 记录 flow_log，通知目标 Agent
        """
        log_flow(task_id, msg["from_agent"], msg["to_agent"],
                 f"请示: {msg['content']}")
        session = f"{task_id}-{msg['to_agent']}"
        await notify_agent_async(
            msg["to_agent"], f"[请示] {msg['content']}", session)

    async def _handle_answer(self, task_id, msg):
        """处理回复（answer命令）。

        任何状态下均可触发。
        动作: 记录 flow_log，标记问题已回答，通知提问者
        """
        log_flow(task_id, msg["from_agent"], msg["to_agent"],
                 f"回复: {msg['content']}")
        # 标记问题已回答
        question_id = msg.get("structured", {}).get("question_id")
        if question_id:
            kanban = self._load_kanban()
            mark_question_answered(kanban, task_id, question_id)
            self._save_kanban(kanban)
        session = f"{task_id}-{msg['to_agent']}"
        await notify_agent_async(
            msg["to_agent"], f"[回复] {msg['content']}", session)

    async def _handle_pending_question(self, task_id, question):
        """处理待回答的问题。

        对未回答的问题重新通知目标 Agent。
        """
        to_agent = question.get("to")
        if not to_agent:
            return
        session = f"{task_id}-{to_agent}"
        msg = (f"[待回答问题] 来自{question.get('from')}："
               f"{question.get('msg')}")
        await notify_agent_async(to_agent, msg, session)

    # ─────────────────────────────────────
    # 停滞检测
    # ─────────────────────────────────────
    async def _check_staleness(self, task_id, state, task):
        """检查任务是否停滞。

        两级检测:
            1. 超过 STALE_WARNING_TIMEOUT（3分钟）-> 催办通知
            2. 超过 STALE_ESCALATE_TIMEOUT（6分钟）-> 上报监察
        """
        last_activity = task.get("last_activity")
        if not last_activity:
            return

        try:
            last_time = datetime.fromisoformat(last_activity)
            elapsed = (datetime.now(CST) - last_time).total_seconds()
        except (ValueError, TypeError):
            return

        # 停滞催办（3分钟）
        if (elapsed > STALE_WARNING_TIMEOUT
                and task_id not in self._stale_warned):
            logger.warning(
                f"[Orchestrator] 任务 {task_id} 在状态 {state} "
                f"停滞 {elapsed:.0f}s")
            current_agent = STATE_AGENT_MAP.get(state)
            if current_agent:
                session = f"{task_id}-{current_agent}"
                await notify_agent_async(
                    current_agent,
                    f"催办：任务 {task_id} 已停滞 {elapsed / 60:.1f} 分钟，"
                    f"请尽快处理",
                    session)
            self._stale_warned.add(task_id)

        # 停滞上报（6分钟）
        if (elapsed > STALE_ESCALATE_TIMEOUT
                and task_id not in self._stale_escalated):
            logger.error(
                f"[Orchestrator] 任务 {task_id} 严重停滞，上报监察")
            add_message(
                task_id, "escalate", "system", "jiancha",
                f"任务停滞超时: 状态{state}停滞{elapsed / 60:.1f}分钟",
                {"error": "stall_timeout", "state": state,
                 "elapsed": elapsed})
            self._stale_escalated.add(task_id)

        # 任务状态变化后清除停滞记录
        if task_id in self._stale_warned:
            prev = self.last_snapshot.get(task_id)
            if prev and prev != state:
                self._stale_warned.discard(task_id)
                self._stale_escalated.discard(task_id)

    # ─────────────────────────────────────
    # 启动恢复
    # ─────────────────────────────────────
    async def _startup_recovery(self):
        """启动时恢复未完成的任务（section 7.2）。

        检查所有非终态任务:
            1. 上次派发状态为 queued/failed -> 重新派发
            2. 上次派发时间超过 STALE_WARNING_TIMEOUT -> 催办
            3. 无派发记录的新任务 -> 首次派发
        """
        logger.info("[Orchestrator] 执行启动恢复检查...")
        kanban = self._load_kanban()

        for task in kanban.get("tasks", []):
            task_id = task["id"]
            state = task.get("state", "")

            # 跳过终态
            if state in TERMINAL_STATES:
                continue

            # 检查上次派发状态
            dispatch_status = task.get("lastDispatchStatus")
            last_time = task.get("lastDispatchTime")

            needs_redispatch = False
            if dispatch_status in ("queued", "failed"):
                logger.warning(
                    f"[Orchestrator] 启动恢复: {task_id} "
                    f"上次派发未完成({dispatch_status})")
                needs_redispatch = True
            elif last_time:
                try:
                    elapsed = (datetime.now(CST) -
                               datetime.fromisoformat(last_time)).total_seconds()
                    if elapsed > STALE_WARNING_TIMEOUT:
                        logger.warning(
                            f"[Orchestrator] 启动恢复: {task_id} "
                            f"停滞{elapsed:.0f}s")
                        needs_redispatch = True
                except (ValueError, TypeError):
                    pass
            else:
                # 无派发记录的新任务
                needs_redispatch = True

            if needs_redispatch:
                target = STATE_AGENT_MAP.get(state)
                if target:
                    session = f"{task_id}-{target}"
                    await notify_agent_async(
                        target,
                        f"[启动恢复] 任务 {task_id} 需要处理，"
                        f"当前状态: {state}",
                        session)
                    log_flow(task_id, "system", target,
                             "启动恢复：重新派发")
                    # 记录到快照避免重复处理
                    self.last_snapshot[task_id] = state

    # ─────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────

    def _load_kanban(self):
        """带文件锁的看板读取。

        复用 kanban_commands 的原子读取模式。
        """
        from kanban_commands import _load_kanban
        return _load_kanban()

    def _save_kanban(self, data):
        """带文件锁的看板写入。

        复用 kanban_commands 的原子写入模式。
        """
        from kanban_commands import _save_kanban
        _save_kanban(data)

    def _build_message(self, state, msg_or_task):
        """根据状态构造针对性派发消息。

        每个状态有专属的消息模板，包含该 Agent 需要做的具体步骤指导。
        """
        templates = {
            "Taizi": ("皇上颁布旨意，需要你分拣。任务ID: {id}，"
                       "完成后执行 approve 或 reject。"),
            "Zhongshu": ("请起草执行方案。任务ID: {id}，完成后执行 "
                          "approve 提交门下省审议。"),
            "Menxia": ("请审议方案。任务ID: {id}，执行 approve（准奏）"
                        "或 reject（封驳）。"),
            "Assigned": ("门下省已准奏，请派发六部。使用 assign 命令派发。"),
            "Doing": ("尚书省派发任务，完成后使用 done 命令上报。"),
            "Review": ("六部已完成，请审查并使用 report 命令汇总。"),
            "Zhongshu_Final": ("尚书省汇总完成，请撰写回奏。"),
            "Done": "任务已完成。",
        }
        template = templates.get(state, "请处理任务。")
        task_id = (msg_or_task.get("task_id")
                    if "task_id" in msg_or_task
                    else msg_or_task.get("id", ""))
        return template.format(id=task_id)

    def _find_task(self, kanban, task_id):
        """查找任务（复用 kanban_commands.find_task）。"""
        return find_task(kanban, task_id)

    def _get_reject_count(self, task_id):
        """获取封驳次数。

        从 task.reviewRound 和 task.reject_count 两个字段取最大值。
        """
        kanban = self._load_kanban()
        task = self._find_task(kanban, task_id)
        if not task:
            return 0
        return task.get("reviewRound", task.get("reject_count", 0))

    def _increment_reject_count(self, task_id):
        """递增封驳计数。

        原子操作：同时更新 reviewRound 和 reject_count。
        """
        kanban = self._load_kanban()
        task = self._find_task(kanban, task_id)
        if task:
            task["reviewRound"] = task.get("reviewRound", 0) + 1
            task["reject_count"] = task.get("reject_count", 0) + 1
            self._save_kanban(kanban)

    def _all_ministries_done(self, task_id):
        """检查所有涉及的六部是否都已完成。

        从 task.ministries_involved 获取涉及的六部列表，
        检查看板消息中是否每个六部都有 done 类型的消息。
        """
        kanban = self._load_kanban()
        task = self._find_task(kanban, task_id)
        if not task:
            return False
        involved = task.get("ministries_involved", MINISTRY_AGENTS)
        messages = task.get("kanban_messages", [])
        done_agents = set()
        for msg in messages:
            if (msg.get("type") == "done"
                    and msg.get("from_agent") in involved):
                done_agents.add(msg["from_agent"])
        return all(m in done_agents for m in involved)

    async def _force_approve(self, task_id, msg):
        """强制准奏（封驳超限时）。

        门下省封驳超过 MAX_REJECT_COUNT 次后自动触发。
        动作: 状态 -> Assigned，记录审计标记，通知尚书省
        """
        update_task_state(task_id, "Assigned")
        log_flow(task_id, "menxia", "shangshu", "封驳超限，系统强制准奏")
        # 记录审计标记
        try:
            from kanban_commands import add_audit_flag
            add_audit_flag(
                task_id, "override",
                f"封驳次数达上限({MAX_REJECT_COUNT})，系统强制准奏")
        except Exception as e:
            logger.warning(f"[Orchestrator] 记录审计标记失败: {e}")
        session = f"{task_id}-shangshu"
        await notify_agent_async(
            "shangshu",
            f"[系统强制准奏] 封驳次数已达上限，任务 {task_id} "
            f"自动准奏，请派发。",
            session)

    def stop(self):
        """停止编排引擎。"""
        self.running = False
        logger.info("[Orchestrator] 收到停止信号")


# ─────────────────────────────────────
# main() 入口
# ─────────────────────────────────────

def main():
    """命令行入口。

    用法:
        python3 pipeline_orchestrator.py
        python3 pipeline_orchestrator.py --interval 10
        python3 pipeline_orchestrator.py --once
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="AgentClaw 看板编排引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 pipeline_orchestrator.py                 # 正常启动
  python3 pipeline_orchestrator.py --interval 10   # 10秒轮询
  python3 pipeline_orchestrator.py --once          # 单次扫描（调试）
        """)
    parser.add_argument(
        "--interval", type=int, default=None,
        help=f"轮询间隔（秒），默认 {POLL_INTERVAL}")
    parser.add_argument(
        "--once", action="store_true",
        help="单次扫描后退出（调试模式）")
    parser.add_argument(
        "--log-level", default=None,
        help="日志级别（DEBUG/INFO/WARNING/ERROR），默认环境变量或 INFO")

    args = parser.parse_args()

    # 日志配置
    log_level = args.log_level or LOG_LEVEL
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 创建编排引擎实例
    orchestrator = Orchestrator(poll_interval=args.interval)

    # 信号处理
    loop = asyncio.new_event_loop()

    def _signal_handler(sig, frame):
        logger.info(f"[Orchestrator] 收到信号 {sig}，正在停止...")
        orchestrator.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 启动
    logger.info("[Orchestrator] 正在启动编排引擎...")

    try:
        if args.once:
            loop.run_until_complete(orchestrator.run_once())
            logger.info("[Orchestrator] 单次扫描完成，退出")
        else:
            loop.run_until_complete(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("[Orchestrator] 键盘中断，停止")
    finally:
        orchestrator.executor.shutdown(wait=False)
        loop.close()
        logger.info("[Orchestrator] 已停止")


if __name__ == "__main__":
    main()
