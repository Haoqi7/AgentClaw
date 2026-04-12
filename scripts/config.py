#!/usr/bin/env python3
"""
config.py - AgentClaw 全局配置中心
所有常量、路径、超时、端口集中管理。
V8 架构：将散落在 server.py / kanban_update.py / pipeline_watchdog.py 中的
配置项统一收归本文件，消除魔法数字和硬编码路径。

用法:
    from config import KANBAN_PATH, POLL_INTERVAL, STATE_AGENT_MAP, ...
"""

import os
from pathlib import Path

# ==================== 路径配置 ====================

BASE_DIR = Path(__file__).resolve().parent.parent       # AgentClaw 项目根目录
DASHBOARD_DIR = BASE_DIR / "dashboard"
SCRIPTS_DIR = BASE_DIR / "scripts"
AGENTS_DIR = BASE_DIR / "agents"
DATA_DIR = BASE_DIR / "data"

# 看板主数据文件（与现有 kanban_update.py / server.py 保持一致）
KANBAN_PATH = DATA_DIR / "tasks_source.json"
# FLOW_LOG_PATH 已移除（V8 流程日志统一写入 tasks_source.json 的 flow_log 字段）

# ==================== 端口配置 ====================

DASHBOARD_PORT = 7891             # Dashboard HTTP 服务器（不动）
OPENCLAW_GATEWAY_PORT = 18789     # OpenClaw Gateway（框架自有，不动）

# ==================== 编排引擎配置 ====================

POLL_INTERVAL = 5                 # 看板轮询间隔（秒）
DEFAULT_AGENT_TIMEOUT = 300       # Agent 唤醒超时（秒），5 分钟
MAX_NOTIFY_RETRIES = 2            # 唤醒失败最大重试次数
NOTIFY_RETRY_DELAY = 5            # 重试间隔（秒）
MAX_CONCURRENT_DISPATCH = 10      # 线程池最大并发派发数

# ==================== 停滞检测配置 ====================

STALE_WARNING_TIMEOUT = 180       # 停滞催办阈值（秒），3 分钟
STALE_ESCALATE_TIMEOUT = 360      # 停滞上报监察阈值（秒），6 分钟
DOING_PROGRESS_TIMEOUT = 45       # Doing 状态六部无活动阈值（秒）

# ==================== 封驳配置 ====================

MAX_REJECT_COUNT = 2              # 门下省最大封驳次数（第3次系统强制准奏）

# ==================== 通知冷却配置（兼容旧逻辑） ====================

# 旧架构的 90 秒冷却窗口（V8 中编排引擎不再依赖冷却去重，
# 此值保留仅用于向后兼容旧版 kanban_update.py 的 hook 逻辑）
NOTIFY_COOLDOWN_SEC = 90
NOTIFY_COOLDOWN_ASYNC_SEC = 30

# ==================== Agent 列表 ====================

ALL_AGENTS = [
    "taizi", "zhongshu", "menxia", "shangshu",
    "libu", "hubu", "bingbu", "xingbu", "gongbu", "libu_hr",
    "jiancha", "zaochao",
]

MINISTRY_AGENTS = ["libu", "hubu", "bingbu", "xingbu", "gongbu", "libu_hr"]

# Agent ID -> 中文名映射
AGENT_LABELS = {
    "taizi": "太子",
    "zhongshu": "中书省",
    "menxia": "门下省",
    "shangshu": "尚书省",
    "libu": "礼部",
    "hubu": "户部",
    "bingbu": "兵部",
    "xingbu": "刑部",
    "gongbu": "工部",
    "libu_hr": "吏部",
    "jiancha": "御史台",
    "zaochao": "钦天监",
    "huangshang": "皇上",
}

# 部门中文名 -> Agent ID 映射
ORG_AGENT_MAP = {
    "礼部": "libu", "户部": "hubu", "兵部": "bingbu",
    "刑部": "xingbu", "工部": "gongbu", "吏部": "libu_hr",
    "中书省": "zhongshu", "门下省": "menxia", "尚书省": "shangshu",
    "太子": "taizi", "御史台": "jiancha", "钦天监": "zaochao",
}

# 状态名 -> 负责部门中文映射（保留兼容）
STATE_ORG_MAP = {
    "Pending": "待处理", "Taizi": "太子", "Zhongshu": "中书省", "Menxia": "门下省",
    "Assigned": "尚书省", "Next": "尚书省",
    "Review": "尚书省", "Done": "完成", "Blocked": "阻塞",
}

# ==================== 九状态机定义 ====================

# 状态 -> 负责Agent映射
STATE_AGENT_MAP = {
    "Taizi": "taizi",
    "Zhongshu": "zhongshu",
    "Menxia": "menxia",
    "Assigned": "shangshu",
    "Doing": None,               # 由 ministries_involved 决定具体六部
    "Review": "shangshu",
    "Zhongshu_Final": "zhongshu",
    "Done": None,
    "Blocked": None,
    "Cancelled": None,
}

# 合法状态转换表
VALID_TRANSITIONS = {
    None:              ["Taizi"],                    # 外部创建
    "Taizi":           ["Zhongshu"],
    "Zhongshu":        ["Menxia", "Assigned"],
    "Menxia":          ["Zhongshu", "Assigned"],      # 封驳或准奏
    "Assigned":        ["Doing"],
    "Doing":           ["Review", "Blocked"],
    "Review":          ["Zhongshu_Final"],
    "Zhongshu_Final":  ["Done", "Zhongshu"],          # 完成或需修改
    "Done":            [],                            # 终态
    "Blocked":         ["Doing", "Cancelled"],
    "Cancelled":       [],                            # 终态
}

# 终态集合
TERMINAL_STATES = {"Done", "Cancelled"}

# ==================== 外部命令 ====================

OPENCLAW_BIN = "openclaw"         # openclaw CLI 命令路径

# ==================== 消息类型（9种 kanban 命令） ====================

MESSAGE_TYPES = [
    "approve", "reject", "assign", "done", "report",
    "ask", "answer", "escalate", "redirect",
]

# ==================== 日志配置 ====================

LOG_LEVEL = os.environ.get("AGENTCLAW_LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# ==================== 时区 ====================

CST_TZ_OFFSET = 8  # 东八区 (UTC+8)
