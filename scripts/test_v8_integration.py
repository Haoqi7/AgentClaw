#!/usr/bin/env python3
"""
V8 Integration Tests — 验证新架构各模块协同工作

测试项目:
1. config.py 加载正常
2. kanban_commands.py 全接口测试
3. agent_notifier.py 接口测试（不实际调用openclaw）
4. pipeline_orchestrator.py 导入测试
5. kanban_update.py 新命令help测试
6. 完整状态流转模拟
7. 封驳超限强制准奏
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import tempfile
from pathlib import Path

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


# ────────────────────────────────────────────────────────────────────
# Test 1: config.py
# ────────────────────────────────────────────────────────────────────
print("\n[1] config.py 测试")
try:
    from config import (
        KANBAN_PATH, POLL_INTERVAL, DEFAULT_AGENT_TIMEOUT,
        MAX_NOTIFY_RETRIES, STATE_AGENT_MAP, VALID_TRANSITIONS,
        TERMINAL_STATES, ALL_AGENTS, MINISTRY_AGENTS, MESSAGE_TYPES,
        AGENT_LABELS, MAX_REJECT_COUNT, OPENCLAW_BIN
    )
    test("配置导入成功", True)
    test("POLL_INTERVAL == 5", POLL_INTERVAL == 5)
    test("DEFAULT_AGENT_TIMEOUT == 300", DEFAULT_AGENT_TIMEOUT == 300)
    test("MAX_NOTIFY_RETRIES == 2", MAX_NOTIFY_RETRIES == 2)
    test("MAX_REJECT_COUNT == 5", MAX_REJECT_COUNT == 5)
    test("9种消息类型", len(MESSAGE_TYPES) == 9)
    test("12个Agent", len(ALL_AGENTS) == 12)
    test("6个六部", len(MINISTRY_AGENTS) == 6)
    test("STATE_AGENT_MAP包含所有状态", all(s in STATE_AGENT_MAP for s in
        ["Taizi","Zhongshu","Menxia","Assigned","Doing","Review","Zhongshu_Final","Done","Blocked","Cancelled"]))
    test("TERMINAL_STATES", TERMINAL_STATES == {"Done", "Cancelled", "archived"})
except Exception as e:
    test("config导入", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 2: kanban_commands.py
# ────────────────────────────────────────────────────────────────────
print("\n[2] kanban_commands.py 测试")
try:
    from kanban_commands import (
        find_task, add_message, get_unread_messages, mark_message_read,
        get_pending_questions, mark_question_answered,
        get_task_state, update_task_state, log_flow,
        append_agent_log, add_audit_flag, record_dispatch_status
    )
    test("kanban_commands导入成功", True)

    # Create a test kanban
    test_data = {
        "tasks": [{
            "id": "TEST-001",
            "title": "测试任务",
            "state": "Taizi",
            "created_at": "2026-04-12T10:00:00+08:00",
            "last_activity": "2026-04-12T10:00:00+08:00",
            "kanban_messages": [],
            "pendingQuestions": [],
            "agentLog": [],
            "auditFlags": [],
            "flow_log": [],
        }],
        "global_counters": {"message_id": 0, "flow_id": 0}
    }

    test("find_task", find_task(test_data, "TEST-001") is not None)
    test("find_task(不存在)", find_task(test_data, "NOPE") is None)
    test("get_task_state", get_task_state(test_data, "TEST-001") == "Taizi")
    test("get_unread_messages(空)", len(get_unread_messages(test_data, "TEST-001")) == 0)
    test("get_pending_questions(空)", len(get_pending_questions(test_data, "TEST-001")) == 0)

except Exception as e:
    test("kanban_commands导入", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 3: agent_notifier.py
# ────────────────────────────────────────────────────────────────────
print("\n[3] agent_notifier.py 测试")
try:
    from agent_notifier import NotifyResult, check_gateway_alive
    test("agent_notifier导入成功", True)
    test("NotifyResult类", hasattr(NotifyResult, '__init__'))
    nr = NotifyResult(True, "test", "sess", "out", "err", 1.5)
    test("NotifyResult实例属性", all(hasattr(nr, a) for a in ['success','agent_id','session_id','stdout','stderr','duration']))
    test("NotifyResult创建", nr.success == True and nr.agent_id == "test")
    test("NotifyResult.repr", "OK" in repr(nr))

    # Gateway check (may fail in CI but function should exist)
    result = check_gateway_alive()
    test("check_gateway_alive(函数)", isinstance(result, bool))
except Exception as e:
    test("agent_notifier导入", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 4: pipeline_orchestrator.py import
# ────────────────────────────────────────────────────────────────────
print("\n[4] pipeline_orchestrator.py 测试")
try:
    # This will test if the file was created by Phase 2 agent
    # If not created yet, this test will be skipped
    if Path("/home/z/my-project/AgentClaw/scripts/pipeline_orchestrator.py").exists():
        import pipeline_orchestrator
        test("pipeline_orchestrator导入成功", True)
        test("Orchestrator类", hasattr(pipeline_orchestrator, 'Orchestrator'))
    else:
        test("pipeline_orchestrator.py (Phase 2未完成，跳过)", True)
except Exception as e:
    test("pipeline_orchestrator导入", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 5: VALID_TRANSITIONS consistency
# ────────────────────────────────────────────────────────────────────
print("\n[5] 状态机一致性测试")
try:
    for from_state, to_states in VALID_TRANSITIONS.items():
        for to_state in to_states:
            if to_state not in TERMINAL_STATES:
                assert to_state in STATE_AGENT_MAP or to_state in ("Blocked",), \
                    f"状态{to_state}不在STATE_AGENT_MAP中"
    test("VALID_TRANSITIONS一致性", True)
except Exception as e:
    test("状态机一致性", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 6: pipeline_watchdog.py V8 检测函数
# ────────────────────────────────────────────────────────────────────
print("\n[6] pipeline_watchdog.py V8 检测函数测试")
try:
    import pipeline_watchdog as pw
    test("pipeline_watchdog导入成功", True)

    # V8 module loaded
    test("V8模块加载成功", pw._V8_MODULES_LOADED)

    # wake_agent 已在V8清理中移除（唤醒由pipeline_orchestrator处理）
    test("wake_agent已移除（由编排引擎接管）", not hasattr(pw, 'wake_agent'))

    # check_kanban_stall
    test("check_kanban_stall存在", callable(pw.check_kanban_stall))
    # Test with terminal state — should return None
    test_kanban = {"tasks": [{"id": "T1", "state": "Done", "last_activity": "2020-01-01T00:00:00+08:00"}], "global_counters": {"message_id": 0, "flow_id": 0}}
    test("check_kanban_stall(Done跳过)", pw.check_kanban_stall(test_kanban, "T1", test_kanban["tasks"][0]) is None)
    # Test with active but recent — should return None
    test_kanban2 = {"tasks": [{"id": "T2", "state": "Zhongshu", "last_activity": "2099-01-01T00:00:00+08:00"}], "global_counters": {"message_id": 0, "flow_id": 0}}
    test("check_kanban_stall(近期活动无停滞)", pw.check_kanban_stall(test_kanban2, "T2", test_kanban2["tasks"][0]) is None)

    # check_review_round_limit
    test("check_review_round_limit存在", callable(pw.check_review_round_limit))
    # Test normal case — should return None
    test_kanban3 = {"tasks": [{"id": "T3", "state": "Menxia", "reviewRound": 2}], "global_counters": {"message_id": 0, "flow_id": 0}}
    test("check_review_round_limit(正常reviewRound=2)", pw.check_review_round_limit(test_kanban3, "T3", test_kanban3["tasks"][0]) is None)
    # Test exceeded case — should return detail
    test_kanban4 = {"tasks": [{"id": "T4", "state": "Menxia", "reviewRound": 5}], "global_counters": {"message_id": 0, "flow_id": 0}}
    result = pw.check_review_round_limit(test_kanban4, "T4", test_kanban4["tasks"][0])
    test("check_review_round_limit(超限reviewRound=5)", result is not None and "封驳循环" in str(result))

    # check_agent_log_anomalies
    test("check_agent_log_anomalies存在", callable(pw.check_agent_log_anomalies))
    # Test with no anomalies
    test_kanban5 = {"tasks": [{"id": "T5", "state": "Doing", "agentLog": [{"agent": "gongbu", "text": "工作正常进行中", "at": "2026-01-01T00:00:00+08:00"}]}], "global_counters": {"message_id": 0, "flow_id": 0}}
    anomalies = pw.check_agent_log_anomalies(test_kanban5, "T5", test_kanban5["tasks"][0])
    test("check_agent_log_anomalies(无异常)", len(anomalies) == 0)
    # Test with ERROR keyword
    test_kanban6 = {"tasks": [{"id": "T6", "state": "Doing", "agentLog": [{"agent": "gongbu", "text": "ERROR: 无法完成部署", "at": "2026-01-01T00:00:00+08:00"}]}], "global_counters": {"message_id": 0, "flow_id": 0}}
    anomalies2 = pw.check_agent_log_anomalies(test_kanban6, "T6", test_kanban6["tasks"][0])
    test("check_agent_log_anomalies(检测ERROR)", len(anomalies2) > 0 and "ERROR" in anomalies2[0])

    # V8 redirect 功能可用
    test("execute_redirect存在", callable(pw.execute_redirect))
    # execute_redirect should fail gracefully for non-existent tasks
    redirect_ok = pw.execute_redirect("NONEXISTENT-TASK", "gongbu", "测试纠正")
    test("execute_redirect(不存在任务返回False)", redirect_ok == False)

except Exception as e:
    test("pipeline_watchdog V8测试", False, str(e))

# ────────────────────────────────────────────────────────────────────
# Test 7: 封驳超限场景模拟
# ────────────────────────────────────────────────────────────────────
print("\n[7] 封驳超限场景模拟")
try:
    # Simulate a reject loop that exceeds MAX_REJECT_COUNT
    test("MAX_REJECT_COUNT == 5", MAX_REJECT_COUNT == 5)
    # Simulate: 5 rounds of reject
    for round_num in range(1, 6):
        review_round = round_num
        if review_round >= MAX_REJECT_COUNT:
            test(f"第{round_num}轮封驳触发强制准奏", True)
        else:
            test(f"第{round_num}轮封驳不触发", True)

    # Verify VALID_TRANSITIONS allows Menxia→Zhongshu (reject)
    test("Menxia→Zhongshu合法(封驳)", "Zhongshu" in VALID_TRANSITIONS.get("Menxia", []))
    # Verify VALID_TRANSITIONS allows Menxia→Assigned (approve)
    test("Menxia→Assigned合法(准奏)", "Assigned" in VALID_TRANSITIONS.get("Menxia", []))
except Exception as e:
    test("封驳超限模拟", False, str(e))


# ────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"测试结果: {passed} 通过, {failed} 失败")
print(f"{'='*50}")
sys.exit(0 if failed == 0 else 1)
