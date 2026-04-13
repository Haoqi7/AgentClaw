#!/usr/bin/env python3
"""
agent_notifier.py - AgentClaw Agent 通知模块

封装所有 openclaw agent 调用，提供统一的唤醒接口。
V8 架构中，这是唯一的 Agent 唤醒入口。
支持同步、重试、异步三种调用模式。

核心设计原则:
    - Session 隔离: 每个任务使用独立 session（如 JJC-001-zhongshu）
    - 确定性派发: 由程序决定通知谁、何时通知，不依赖 LLM
    - 可靠性保障: 内置重试机制和超时保护
    - 可追溯: 每次派发记录到看板的 lastDispatchStatus 字段

接口:
    notify_agent(agent_id, message, session_id=None, timeout=None)
        -> NotifyResult: 同步唤醒 Agent
    notify_agent_with_retry(agent_id, message, session_id=None, max_retries=None)
        -> NotifyResult: 带重试的唤醒
    notify_agent_async(agent_id, message, session_id=None, timeout=None)
        -> asyncio.Future: 异步唤醒（用于编排引擎 asyncio 循环）
    check_gateway_alive()
        -> bool: 检测 OpenClaw Gateway 是否在线
"""

import asyncio
import logging
import subprocess
import time

from config import (
    OPENCLAW_BIN,
    DEFAULT_AGENT_TIMEOUT,
    MAX_NOTIFY_RETRIES,
    NOTIFY_RETRY_DELAY,
    OPENCLAW_GATEWAY_PORT,
)

logger = logging.getLogger("agent_notifier")


class NotifyResult:
    """通知结果封装类。

    封装 openclaw agent 调用的执行结果，包含成功/失败状态、
    Agent ID、Session ID、标准输出/错误、执行时长等信息。
    """

    def __init__(self, success, agent_id, session_id="",
                 stdout="", stderr="", duration=0.0):
        """
        Args:
            success (bool): 是否成功
            agent_id (str): 目标 Agent ID
            session_id (str): Session 标识
            stdout (str): 标准输出内容
            stderr (str): 标准错误内容
            duration (float): 执行时长（秒）
        """
        self.success = success
        self.agent_id = agent_id
        self.session_id = session_id
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration

    def __repr__(self):
        status = "OK" if self.success else "FAIL"
        return (f"<NotifyResult {status} agent={self.agent_id} "
                f"session={self.session_id} {self.duration:.1f}s>")

    def to_dict(self):
        """序列化为字典（用于日志和调试）"""
        return {
            "success": self.success,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "duration": round(self.duration, 2),
            "stdout_len": len(self.stdout),
            "stderr_len": len(self.stderr),
        }


# ====================================================================
# Gateway 检测
# ====================================================================

def check_gateway_alive(host="127.0.0.1", port=None):
    """检测 OpenClaw Gateway 是否在线。

    通过 TCP 端口探测判断 Gateway 进程是否存活。

    Args:
        host: Gateway 主机地址（默认 127.0.0.1）
        port: Gateway 端口（默认使用配置中的 OPENCLAW_GATEWAY_PORT）

    Returns:
        bool: Gateway 是否在线
    """
    if port is None:
        port = OPENCLAW_GATEWAY_PORT
    import socket
    try:
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


# ====================================================================
# 同步通知
# ====================================================================

def notify_agent(agent_id, message, session_id=None, timeout=None):
    """唤醒 Agent 并发送消息（同步版本）。

    通过 openclaw agent CLI 命令唤醒指定 Agent。
    这是最底层的通知函数，其他高阶接口都基于此实现。

    Args:
        agent_id (str): Agent 标识（如 "zhongshu", "menxia"）
        message (str): 发送给 Agent 的消息内容
        session_id (str): Session 标识（如 "JJC-001-zhongshu"），用于并行隔离。
            如果不传，则由 openclaw 使用默认 session。
        timeout (int): 超时秒数（默认使用配置中的 DEFAULT_AGENT_TIMEOUT）

    Returns:
        NotifyResult: 包含执行结果的封装对象
    """
    if timeout is None:
        timeout = DEFAULT_AGENT_TIMEOUT

    # 构造 openclaw agent 命令
    cmd = [
        OPENCLAW_BIN, "agent",
        "--agent", agent_id,
        "-m", message,
        "--timeout", str(timeout),
    ]

    # Session 隔离：通过 --session-id 参数实现
    if session_id:
        cmd.extend(["--session-id", session_id])

    logger.info(f"[notify] 唤醒 Agent: {agent_id} | Session: {session_id} | "
                f"消息: {message[:80]}...")
    logger.debug(f"[notify] 完整命令: {' '.join(cmd)}")

    start_time = time.time()

    try:
        # 执行命令，额外 30 秒 buffer 给进程启动开销
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
        duration = time.time() - start_time
        success = result.returncode == 0

        if success:
            logger.info(f"[notify] Agent {agent_id} 唤醒成功 ({duration:.1f}s) "
                        f"| session={session_id}")
        else:
            logger.warning(
                f"[notify] Agent {agent_id} 唤醒失败 (rc={result.returncode}): "
                f"{(result.stderr or '')[:200]}"
            )

        return NotifyResult(
            success=success,
            agent_id=agent_id,
            session_id=session_id or "",
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            duration=duration,
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        logger.error(f"[notify] Agent {agent_id} 唤醒超时 ({duration:.1f}s) "
                     f"| timeout={timeout}s")
        return NotifyResult(
            success=False,
            agent_id=agent_id,
            session_id=session_id or "",
            stderr=f"Timeout after {timeout}s",
            duration=duration,
        )

    except FileNotFoundError:
        duration = time.time() - start_time
        logger.error(f"[notify] openclaw 命令不存在: {OPENCLAW_BIN}，"
                     f"请确认 OpenClaw 已正确安装")
        return NotifyResult(
            success=False,
            agent_id=agent_id,
            session_id=session_id or "",
            stderr=f"Command not found: {OPENCLAW_BIN}",
            duration=duration,
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"[notify] Agent {agent_id} 唤醒异常: {e}")
        return NotifyResult(
            success=False,
            agent_id=agent_id,
            session_id=session_id or "",
            stderr=str(e),
            duration=duration,
        )


# ====================================================================
# 带重试的通知
# ====================================================================

def notify_agent_with_retry(agent_id, message, session_id=None,
                             max_retries=None, timeout=None):
    """带重试的 Agent 通知。

    在 notify_agent 基础上增加自动重试逻辑。每次失败后等待
    NOTIFY_RETRY_DELAY 秒再重试，最多重试 max_retries 次。

    Args:
        agent_id (str): Agent 标识
        message (str): 消息内容
        session_id (str): Session 标识
        max_retries (int): 最大重试次数（默认使用配置中的 MAX_NOTIFY_RETRIES）
        timeout (int): 单次超时秒数

    Returns:
        NotifyResult: 最后一次执行的结果
    """
    if max_retries is None:
        max_retries = MAX_NOTIFY_RETRIES

    last_result = None

    for attempt in range(1, max_retries + 1):
        result = notify_agent(agent_id, message, session_id, timeout)
        last_result = result

        if result.success:
            return result

        if attempt < max_retries:
            logger.warning(
                f"[notify] Agent {agent_id} 第 {attempt}/{max_retries} 次唤醒失败，"
                f"{NOTIFY_RETRY_DELAY}s 后重试..."
            )
            time.sleep(NOTIFY_RETRY_DELAY)

    logger.error(f"[notify] Agent {agent_id} 经 {max_retries} 次重试仍失败 "
                 f"| session={session_id}")
    return last_result


# ====================================================================
# 异步通知（用于编排引擎 asyncio 循环）
# ====================================================================

def notify_agent_async(agent_id, message, session_id=None, timeout=None):
    """异步版本的 Agent 通知。

    使用线程池执行同步的 notify_agent_with_retry，
    返回可 await 的 asyncio.Future 对象。
    专为编排引擎的 asyncio 主循环设计。

    Args:
        agent_id (str): Agent 标识
        message (str): 消息内容
        session_id (str): Session 标识
        timeout (int): 超时秒数

    Returns:
        asyncio.Future: 可 await 的 Future，结果为 NotifyResult
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    return loop.run_in_executor(
        None,  # 使用默认线程池
        notify_agent_with_retry,
        agent_id, message, session_id, None, timeout,
    )


# ====================================================================
# 诊断日志
# ====================================================================

def notify_agent_with_logging(task_id, agent_id, message, session_id=None):
    """带诊断日志的 Agent 通知。

    除了基本的通知功能外，还将完整的 openclaw 输出写入日志文件，
    便于事后诊断通知失败的原因。

    Args:
        task_id (str): 任务ID（用于日志文件命名）
        agent_id (str): Agent 标识
        message (str): 消息内容
        session_id (str): Session 标识

    Returns:
        NotifyResult: 执行结果
    """
    from config import BASE_DIR
    log_dir = BASE_DIR / "data" / "notify_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    ts_tag = time.strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{task_id}_{agent_id}_{ts_tag}.log"

    try:
        lf = open(str(log_file), 'w')
    except Exception as e:
        logger.warning(f"[notify] 无法创建日志文件 {log_file}: {e}")
        lf = None

    try:
        result = notify_agent_with_retry(agent_id, message, session_id)
    finally:
        if lf:
            try:
                lf.write(f"Agent: {agent_id}\n")
                lf.write(f"Session: {session_id}\n")
                lf.write(f"Message: {message[:500]}\n")
                lf.write(f"Result: {result.to_dict()}\n")
                lf.write(f"Stdout: {result.stdout[:2000]}\n")
                lf.write(f"Stderr: {result.stderr[:2000]}\n")
                lf.close()
            except Exception:
                pass

    return result
