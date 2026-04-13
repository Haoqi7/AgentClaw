#!/usr/bin/env python3
"""
看板任务更新工具 V8 - 供各省部 Agent 调用

本工具操作 data/tasks_source.json（JSON 看板模式）。
如果您已部署 edict/backend（Postgres + Redis 事件总线模式），
请使用 edict/backend API 端点代替本脚本，或运行迁移脚本：
  python3 edict/migration/migrate_json_to_pg.py

两种模式互相独立，数据不会自动同步。

用法:
  # ═══ V8 看板命令（核心通信接口）═══
  python3 kanban_update.py approve   JJC-xxx "准奏，方案可行"
  python3 kanban_update.py reject    JJC-xxx "方案需修改" [review_round]
  python3 kanban_update.py assign    JJC-xxx libu "请撰写文档"
  python3 kanban_update.py done-v2   JJC-xxx "/path/to/output" "任务完成"
  python3 kanban_update.py report    JJC-xxx "/path/to/summary" "汇总完成"
  python3 kanban_update.py ask       JJC-xxx zhongshu "方案是否可行？"
  python3 kanban_update.py answer    JJC-xxx menxia "方案可行" [--question-id q-001]
  python3 kanban_update.py escalate  JJC-xxx "任务异常，需要上级介入"
  python3 kanban_update.py redirect  JJC-xxx menxia "流程错误，应先提交中书省"

  # ═══ 看板管理命令（创建/进度/子任务）═══
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令
  python3 kanban_update.py todo   JJC-20260223-012 1 "实现API接口" in-progress
  python3 kanban_update.py progress JJC-20260223-012 "正在分析需求" "1.调研|2.设计|3.实现"
"""
import datetime
import json, pathlib, sys, subprocess, logging, os, re, threading

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
TASKS_FILE = _BASE / 'data' / 'tasks_source.json'
REFRESH_SCRIPT = _BASE / 'scripts' / 'refresh_live_data.py'

log = logging.getLogger('kanban')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

# 文件锁 —— 防止多 Agent 同时读写 tasks_source.json
# 兼容处理：如果 file_lock 模块不存在，提供降级实现
try:
    from file_lock import atomic_json_read, atomic_json_update  # noqa: E402
except ImportError:
    # 降级实现：使用文件锁（兼容 Windows/Linux/macOS）
    import os as _os
    _IS_WIN = _os.name == 'nt'
    if _IS_WIN:
        import msvcrt as _msvcrt
    else:
        import fcntl as _fcntl_mod
    
    def _acquire_lock(lock_f, exclusive=True):
        """平台无关的文件锁获取"""
        if _IS_WIN:
            _msvcrt.locking(lock_f.fileno(), _msvcrt.LK_LOCK if exclusive else _msvcrt.LK_NBLCK, 1)
        else:
            _fcntl_mod.flock(lock_f.fileno(), _fcntl_mod.LOCK_EX if exclusive else _fcntl_mod.LOCK_SH)
    
    def _release_lock(lock_f):
        """平台无关的文件锁释放"""
        try:
            if _IS_WIN:
                _msvcrt.locking(lock_f.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                _fcntl_mod.flock(lock_f.fileno(), _fcntl_mod.LOCK_UN)
        except Exception:
            pass
    
    def atomic_json_read(path, default):
        """降级读取：使用文件锁"""
        if not path.exists():
            return default
        # 锁文件：原文件名 + .lock
        lock_path = path.with_suffix(path.suffix + '.lock')
        lock_f = open(lock_path, 'a')  # 'a' 模式，不会清空文件
        try:
            _acquire_lock(lock_f, exclusive=False)
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception:
                return default
        finally:
            _release_lock(lock_f)
            lock_f.close()
    
    def atomic_json_update(path, modifier, default):
        """降级更新：使用文件锁 + 临时文件原子写入"""
        import tempfile
        lock_path = path.with_suffix(path.suffix + '.lock')
        lock_f = open(lock_path, 'a')
        try:
            _acquire_lock(lock_f, exclusive=True)
            try:
                if path.exists():
                    with open(path, 'r') as f:
                        data = json.load(f)
                else:
                    data = default
                new_data = modifier(data)
                fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(new_data, f, indent=2, ensure_ascii=False)
                    os.replace(tmp_path, str(path))
                except Exception:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
                return new_data
            except Exception:
                raise
        finally:
            _release_lock(lock_f)
            lock_f.close()
    
    log.warning('⚠️ file_lock 模块未找到，使用降级文件锁实现')

# utils 模块兼容处理
try:
    from utils import now_iso  # noqa: E402
except ImportError:
    def now_iso():
        """降级实现：返回北京时间 ISO 格式时间"""
        _BJT = datetime.timezone(datetime.timedelta(hours=8))
        return datetime.datetime.now(_BJT).isoformat()
    log.info('⚠️ utils 模块未找到，使用降级 now_iso 实现')

STATE_ORG_MAP = {
      'Pending': '待处理', 'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
      'Assigned': '尚书省', 'Next': '尚书省',
      'Review': '尚书省', 'Done': '完成', 'Blocked': '阻塞',
}

# V8: 从 config.py 导入共享映射表（旧版本地定义已迁移）
try:
    from config import STATE_AGENT_MAP as _STATE_AGENT_MAP, ORG_AGENT_MAP as _ORG_AGENT_MAP, AGENT_LABELS as _AGENT_LABELS
except ImportError:
    # 降级：如果 config.py 不可用，使用本地最小定义
    _STATE_AGENT_MAP = {
        'Taizi': 'taizi', 'Zhongshu': 'zhongshu', 'Menxia': 'menxia',
        'Assigned': 'zhongshu', 'Pending': 'zhongshu', 'Done': 'taizi',
    }
    _ORG_AGENT_MAP = {
        '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
        '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
        '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
    }
    _AGENT_LABELS = {
        'main': '太子', 'taizi': '太子', 'zhongshu': '中书省', 'menxia': '门下省',
        'shangshu': '尚书省', 'libu': '礼部', 'hubu': '户部', 'bingbu': '兵部',
        'xingbu': '刑部', 'gongbu': '工部', 'libu_hr': '吏部', 'huangshang': '皇上',
    }
    log.info('⚠️ config.py 未找到，使用本地降级映射表')

MAX_PROGRESS_LOG = 100  # 单任务最大进展日志条数

# ═══════════════════════════════════════════════════════════════════════
# V8 新增：9种看板命令（kanban_commands.py 的 CLI 包装）
#
# 这些命令是 V8 架构的核心通信接口，所有 Agent 间的通信
# 必须通过这些命令写入看板，编排引擎负责读取和派发。
# ═══════════════════════════════════════════════════════════════════════

def cmd_approve(task_id, comment=''):
    """V8 命令：门下省准奏。

    用法: kanban_update.py approve JJC-xxx "准奏，方案可行"
    效果: 写入 approve 消息到看板，编排引擎读取后状态 -> Assigned
    """
    from kanban_commands import add_message as _add_msg
    try:
        msg_id = _add_msg(task_id, 'approve', 'menxia', 'shangshu', comment or '准奏', {})
        print(f'[approve] 已写入准奏消息: {msg_id} | task={task_id}', flush=True)
        log.info(f'[approve] 门下省准奏: {task_id} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[approve] 失败: {e}', flush=True)
        log.error(f'[approve] 失败: {e}')


def cmd_reject(task_id, comment='', review_round=None):
    """V8 命令：门下省封驳。

    用法: kanban_update.py reject JJC-xxx "方案需修改" [review_round]
    效果: 写入 reject 消息到看板，编排引擎读取后状态 -> Zhongshu
    """
    from kanban_commands import add_message as _add_msg
    try:
        structured = {}
        if review_round:
            structured['review_round'] = int(review_round)
        msg_id = _add_msg(task_id, 'reject', 'menxia', 'zhongshu', comment or '封驳', structured)
        print(f'[reject] 已写入封驳消息: {msg_id} | task={task_id} | round={review_round or "auto"}', flush=True)
        log.info(f'[reject] 门下省封驳: {task_id} | msg={msg_id} | round={review_round or "auto"}')
        _trigger_refresh()
    except Exception as e:
        print(f'[reject] 失败: {e}', flush=True)
        log.error(f'[reject] 失败: {e}')


def cmd_assign(task_id, dept, comment=''):
    """V8 命令：尚书省派发六部。

    用法: kanban_update.py assign JJC-xxx libu "请撰写文档"
    效果: 写入 assign 消息到看板，编排引擎读取后状态 -> Doing
    """
    from kanban_commands import add_message as _add_msg
    try:
        structured = {'dept': dept}
        msg_id = _add_msg(task_id, 'assign', 'shangshu', dept, comment or f'派发任务给{dept}', structured)
        print(f'[assign] 已写入派发消息: {msg_id} | task={task_id} -> {dept}', flush=True)
        log.info(f'[assign] 尚书省派发: {task_id} -> {dept} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[assign] 失败: {e}', flush=True)
        log.error(f'[assign] 失败: {e}')


def cmd_done_v2(task_id, output='', comment=''):
    """V8 命令：六部/尚书省完成（写入看板消息版本）。

    用法: kanban_update.py done-v2 JJC-xxx "/path/to/output" "任务完成"
    效果: 写入 done 消息到看板，编排引擎检查是否全部完成

    注意: V8 done 命令，写入看板消息由编排引擎处理。
    """
    from kanban_commands import add_message as _add_msg
    try:
        # 推断当前 Agent
        agent_id = _infer_agent_id_from_runtime()
        if not agent_id:
            agent_id = 'unknown'
        structured = {'output': output}
        # 六部完成后发给尚书省
        to_agent = 'shangshu'
        msg_id = _add_msg(task_id, 'done', agent_id, to_agent, comment or f'{agent_id}完成', structured)
        print(f'[done-v2] 已写入完成消息: {msg_id} | task={task_id} | from={agent_id}', flush=True)
        log.info(f'[done-v2] 完成: {task_id} | from={agent_id} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[done-v2] 失败: {e}', flush=True)
        log.error(f'[done-v2] 失败: {e}')


def cmd_report(task_id, output='', comment=''):
    """V8 命令：尚书省/中书省汇总报告。

    用法: kanban_update.py report JJC-xxx "/path/to/summary" "汇总完成"
    效果: 写入 report 消息到看板，编排引擎读取后状态流转

    支持的 action:
        - draft_proposal: 中书省 -> 门下省
        - forward_to_shangshu: 中书省 -> 尚书省
        - report_to_taizi: 中书省回奏 -> Done
        - forward_edict: 太子分拣 -> 中书省
        - (默认): 尚书省汇总 -> Zhongshu_Final
    """
    from kanban_commands import add_message as _add_msg
    try:
        agent_id = _infer_agent_id_from_runtime()
        if not agent_id:
            agent_id = 'unknown'

        # 根据 Agent 确定目标和 action
        if agent_id == 'shangshu':
            to_agent = 'zhongshu'
            action = ''
        elif agent_id == 'zhongshu':
            # 中书省根据 action 决定目标
            to_agent = 'menxia'  # 默认提交审议
            action = 'draft_proposal'
        elif agent_id == 'taizi':
            to_agent = 'zhongshu'
            action = 'forward_edict'
        else:
            to_agent = 'shangshu'
            action = ''

        structured = {'output': output}
        if action:
            structured['action'] = action

        msg_id = _add_msg(task_id, 'report', agent_id, to_agent, comment or '汇总报告', structured)
        print(f'[report] 已写入报告消息: {msg_id} | task={task_id} | from={agent_id} -> {to_agent}', flush=True)
        log.info(f'[report] 汇总: {task_id} | from={agent_id} -> {to_agent} | action={action or "default"} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[report] 失败: {e}', flush=True)
        log.error(f'[report] 失败: {e}')


def cmd_ask(task_id, to_agent, msg_text):
    """V8 命令：向其他 Agent 请示。

    用法: kanban_update.py ask JJC-xxx zhongshu "这个方案是否可行？"
    效果: 写入 ask 消息到看板，编排引擎通知目标 Agent
    """
    from kanban_commands import add_message as _add_msg
    try:
        agent_id = _infer_agent_id_from_runtime()
        if not agent_id:
            agent_id = 'unknown'
        msg_id = _add_msg(task_id, 'ask', agent_id, to_agent, msg_text, {})
        print(f'[ask] 已写入请示消息: {msg_id} | task={task_id} | {agent_id} -> {to_agent}', flush=True)
        log.info(f'[ask] 请示: {task_id} | {agent_id} -> {to_agent} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[ask] 失败: {e}', flush=True)
        log.error(f'[ask] 失败: {e}')


def cmd_answer(task_id, to_agent, msg_text, question_id=None):
    """V8 命令：回复其他 Agent 的请示。

    用法: kanban_update.py answer JJC-xxx menxia "方案可行，请继续"
    用法: kanban_update.py answer JJC-xxx menxia "方案可行" --question-id q-001
    效果: 写入 answer 消息到看板，编排引擎通知目标 Agent 并标记问题已回答
    """
    from kanban_commands import add_message as _add_msg
    try:
        agent_id = _infer_agent_id_from_runtime()
        if not agent_id:
            agent_id = 'unknown'
        structured = {}
        if question_id:
            structured['question_id'] = question_id
        msg_id = _add_msg(task_id, 'answer', agent_id, to_agent, msg_text, structured)
        print(f'[answer] 已写入回复消息: {msg_id} | task={task_id} | {agent_id} -> {to_agent}', flush=True)
        log.info(f'[answer] 回复: {task_id} | {agent_id} -> {to_agent} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[answer] 失败: {e}', flush=True)
        log.error(f'[answer] 失败: {e}')


def cmd_escalate(task_id, reason):
    """V8 命令：异常上报。

    用法: kanban_update.py escalate JJC-xxx "任务执行遇到异常，需要上级介入"
    效果: 写入 escalate 消息到看板，编排引擎通知目标 Agent
    """
    from kanban_commands import add_message as _add_msg
    try:
        agent_id = _infer_agent_id_from_runtime()
        if not agent_id:
            agent_id = 'unknown'
        # 上报目标默认为尚书省（或太子）
        to_agent = 'shangshu'
        structured = {'error': 'agent_escalate', 'reason': reason}
        msg_id = _add_msg(task_id, 'escalate', agent_id, to_agent, reason, structured)
        print(f'[escalate] 已写入上报消息: {msg_id} | task={task_id} | {agent_id} -> {to_agent}', flush=True)
        log.info(f'[escalate] 上报: {task_id} | {agent_id} -> {to_agent} | reason={reason[:50]} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[escalate] 失败: {e}', flush=True)
        log.error(f'[escalate] 失败: {e}')


def cmd_redirect(task_id, to_agent, reason):
    """V8 命令：监察纠正（御史台专用）。

    用法: kanban_update.py redirect JJC-xxx menxia "流程错误，应先提交中书省"
    效果: 写入 redirect 消息到看板，编排引擎通知被纠正 Agent
    """
    from kanban_commands import add_message as _add_msg
    try:
        structured = {'correction': reason}
        msg_id = _add_msg(task_id, 'redirect', 'jiancha', to_agent, reason, structured)
        print(f'[redirect] 已写入纠正消息: {msg_id} | task={task_id} | jiancha -> {to_agent}', flush=True)
        log.info(f'[redirect] 监察纠正: {task_id} | jiancha -> {to_agent} | reason={reason[:50]} | msg={msg_id}')
        _trigger_refresh()
    except Exception as e:
        print(f'[redirect] 失败: {e}', flush=True)
        log.error(f'[redirect] 失败: {e}')


def cmd_show(task_id=None):
    """查看看板状态（供 Agent 查询任务列表和详情）。
    
    用法: 
      python3 kanban_update.py show              # 列出所有任务概要
      python3 kanban_update.py show JJC-xxx      # 查看指定任务详情
    """
    tasks = load()
    if not tasks:
        print('[show] 看板为空', flush=True)
        return
    
    if task_id:
        t = find_task(tasks, task_id)
        if not t:
            print(f'[show] 任务 {task_id} 不存在', flush=True)
            return
        # Print task details
        import json as _json
        print(f'[show] 任务详情: {task_id}', flush=True)
        print(f'  标题: {t.get("title", "")}', flush=True)
        print(f'  状态: {t.get("state", "")}', flush=True)
        print(f'  部门: {t.get("org", "")}', flush=True)
        print(f'  负责人: {t.get("official", "")}', flush=True)
        print(f'  当前: {t.get("now", "")}', flush=True)
        print(f'  阻塞: {t.get("block", "")}', flush=True)
        print(f'  更新: {t.get("updatedAt", "")}', flush=True)
        # flow_log
        fl = t.get('flow_log', [])
        if fl:
            print(f'  流转记录({len(fl)}条):', flush=True)
            for f_entry in fl[-5:]:
                print(f'    {f_entry.get("at","")} | {f_entry.get("from","")} → {f_entry.get("to","")} | {f_entry.get("remark","")}', flush=True)
        # kanban_messages (last 5)
        msgs = t.get('kanban_messages', [])
        if msgs:
            print(f'  未读消息({len([m for m in msgs if not m.get("read")])}条未读/共{len(msgs)}条):', flush=True)
            for m in msgs[-5:]:
                read_mark = '' if m.get('read') else '[未读]'
                print(f'    {read_mark} {m.get("timestamp","")} | {m.get("type","")} | {m.get("from_agent","")}→{m.get("to_agent","")} | {m.get("content","")[:60]}', flush=True)
    else:
        # List all tasks summary
        print(f'[show] 看板共 {len(tasks)} 个任务:', flush=True)
        for t in tasks:
            tid = t.get('id', '?')
            state = t.get('state', '?')
            org = t.get('org', '')
            title = t.get('title', '')[:40]
            updated = t.get('updatedAt', '')[:16]
            print(f'  {tid} | {state:<12} | {org:<6} | {title} | {updated}', flush=True)


def load():
    """读取看板数据，返回任务列表（兼容新旧两种文件格式）。

    新格式: {"tasks": [...], "global_counters": {...}}
    旧格式: [...]
    """
    data = atomic_json_read(TASKS_FILE, {"tasks": [], "global_counters": {}})
    if isinstance(data, list):
        return data  # 兼容旧格式（纯列表）
    return data.get("tasks", [])


def _trigger_refresh():
    """异步触发 live_status 刷新，不阻塞调用方。"""
    try:
        subprocess.Popen(['python3', str(REFRESH_SCRIPT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def find_task(tasks, task_id):
    return next((t for t in tasks if t.get('id') == task_id), None)


# 旨意标题最低要求
_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def _sanitize_text(raw, max_len=80):
    """清洗文本：剥离文件路径、URL、Conversation 元数据、传旨前缀、截断过长内容。"""
    t = (raw or '').strip()
    # 1) 剥离 Conversation info / Conversation 后面的所有内容
    t = re.split(r'\n*Conversation\b', t, maxsplit=1)[0].strip()
    # 2) 剥离 ```json 代码块
    t = re.split(r'\n*```', t, maxsplit=1)[0].strip()
    # 3) 剥离 Unix/Mac 文件路径 (/Users/xxx, /home/xxx, /opt/xxx, ./xxx)
    t = re.sub(r'[/\\.~][A-Za-z0-9_\-./]+(?:\.(?:py|js|ts|json|md|sh|yaml|yml|txt|csv|html|css|log))?', '', t)
    # 4) 剥离 URL
    t = re.sub(r'https?://\S+', '', t)
    # 5) 清理常见前缀: "传旨:" "下旨:" "下旨（xxx）:" 等
    t = re.sub(r'^(传旨|下旨)([（(][^)）]*[)）])?[：:\uff1a]\s*', '', t)
    # 6) 剥离系统元数据关键词
    t = re.sub(r'(message_id|session_id|chat_id|open_id|user_id|tenant_key)\s*[:=]\s*\S+', '', t)
    # 7) 合并多余空白
    t = re.sub(r'\s+', ' ', t).strip()
    # 8) 截断过长内容
    if len(t) > max_len:
        t = t[:max_len] + '…'
    return t


def _sanitize_title(raw):
    """清洗标题（最长 80 字符）。"""
    return _sanitize_text(raw, 80)


def _sanitize_remark(raw):
    """清洗流转备注（最长 120 字符）。"""
    return _sanitize_text(raw, 120)


def _infer_agent_id_from_runtime(task=None):
    """尽量推断当前执行该命令的 Agent。"""
    for k in ('OPENCLAW_AGENT_ID', 'OPENCLAW_AGENT', 'AGENT_ID'):
        v = (os.environ.get(k) or '').strip()
        if v:
            return v

    cwd = str(pathlib.Path.cwd())
    m = re.search(r'workspace-([a-zA-Z0-9_\-]+)', cwd)
    if m:
        return m.group(1)

    fpath = str(pathlib.Path(__file__).resolve())
    m2 = re.search(r'workspace-([a-zA-Z0-9_\-]+)', fpath)
    if m2:
        return m2.group(1)

    if task:
        state = task.get('state', '')
        org = task.get('org', '')
        aid = _STATE_AGENT_MAP.get(state)
        if aid is None and state in ('Doing', 'Next'):
            aid = _ORG_AGENT_MAP.get(org)
        if aid:
            return aid
    return ''


def _is_valid_task_title(title):
    """校验标题是否足够作为一个旨意任务。"""
    t = (title or '').strip()
    if len(t) < _MIN_TITLE_LEN:
        return False, f'标题过短（{len(t)}<{_MIN_TITLE_LEN}字），疑似非旨意'
    if t.lower() in _JUNK_TITLES:
        return False, f'标题 "{t}" 不是有效旨意'
    # 纯标点或问号
    if re.fullmatch(r'[\s?？!！.。,，…·\-—~]+', t):
        return False, '标题只有标点符号'
    # 看起来像文件路径
    if re.match(r'^[/\\~.]', t) or re.search(r'/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+', t):
        return False, f'标题看起来像文件路径，请用中文概括任务'
    # 只剩标点和空白（清洗后可能变空）
    if re.fullmatch(r'[\s\W]*', t):
        return False, '标题清洗后为空'
    return True, ''


def cmd_create(task_id, title, state, org, official, remark=None, current_session_key=None, huangshang_chat_id=None):
    """新建任务（收旨时立即调用）
    
    Args:
        task_id: 任务ID
        title: 任务标题
        state: 初始状态
        org: 初始部门
        official: 负责人
        remark: 备注
        current_session_key: 当前会话的sessionKey，如果有则使用该会话发送通知
        huangshang_chat_id: 皇上的chat_id（如 "user:ou_xxx"），用于太子回奏时发送消息
    """
    # 清洗标题（剥离元数据）
    title = _sanitize_title(title)
    # 旨意标题校验
    valid, reason = _is_valid_task_title(title)
    if not valid:
        log.warning(f'⚠️ 拒绝创建 {task_id}：{reason}')
        print(f'[看板] 拒绝创建：{reason}', flush=True)
        return
    actual_org = STATE_ORG_MAP.get(state, org)
    clean_remark = _sanitize_remark(remark) if remark else f"下旨：{title}"
    
    def modifier(data):
        # 兼容新旧格式：如果是旧列表格式，包装为字典
        if isinstance(data, list):
            data = {"tasks": data, "global_counters": {}}
        tasks = data.get("tasks", [])
        existing = next((t for t in tasks if t.get('id') == task_id), None)
        if existing:
            if existing.get('state') in ('Done', 'Cancelled'):
                log.warning(f'⚠️ 任务 {task_id} 已完结 (state={existing["state"]})，不可覆盖')
                return data
            if existing.get('state') not in (None, '', 'Inbox', 'Pending'):
                log.warning(f'任务 {task_id} 已存在 (state={existing["state"]})，将被覆盖')
        tasks = [t for t in tasks if t.get('id') != task_id]
        # 构建初始 flow_log：皇上→太子（旨意到达太子）
        init_flow = [{"at": now_iso(), "from": "皇上", "to": "太子", "remark": clean_remark}]
        # 如果 state 不是 Pending/Taizi，说明太子已经转交，追加太子→目标部门
        if state not in ('Pending', 'Taizi'):
            init_flow.append({
                "at": now_iso(), "from": "太子", "to": actual_org,
                "remark": f"太子转交旨意至{actual_org}",
            })
        # 构建任务对象
        task_obj = {
            "id": task_id, "title": title, "official": official,
            "org": actual_org, "state": state,
            "now": clean_remark[:60] if remark else f"已下旨，等待{actual_org}接旨",
            "eta": "-", "block": "无", "output": "", "ac": "",
            "flow_log": init_flow,
            "updatedAt": now_iso()
        }
        # 保存皇上的 chat_id（用于太子回奏）
        if huangshang_chat_id:
            task_obj['huangshang_chat_id'] = huangshang_chat_id
        tasks.insert(0, task_obj)
        data["tasks"] = tasks
        return data
    
    atomic_json_update(TASKS_FILE, modifier, {"tasks": [], "global_counters": {}})
    _trigger_refresh()

    log.info(f'✅ 创建 {task_id} | {title[:30]} | state={state}')


def cmd_progress(task_id, now_text, todos_pipe='', tokens=0, cost=0.0, elapsed=0):
    """🔥 实时进展汇报 — Agent 主动调用，不改变状态，只更新 now + todos

    now_text: 当前正在做什么的一句话描述（必填）
    todos_pipe: 可选，用 | 分隔的 todo 列表，格式：
        "已完成的事项✅|正在做的事项🔄|计划做的事项"
        - 以 ✅ 结尾 → completed
        - 以 🔄 结尾 → in-progress
        - 其他 → not-started
    tokens: 可选，本次消耗的 token 数
    cost: 可选，本次成本（美元）
    elapsed: 可选，本次耗时（秒）
    """
    clean = _sanitize_remark(now_text)
    # 解析 todos_pipe
    parsed_todos = None
    if todos_pipe:
        new_todos = []
        for i, item in enumerate(todos_pipe.split('|'), 1):
            item = item.strip()
            if not item:
                continue
            if item.endswith('✅'):
                status = 'completed'
                title = item[:-1].strip()
            elif item.endswith('🔄'):
                status = 'in-progress'
                title = item[:-1].strip()
            else:
                status = 'not-started'
                title = item
            new_todos.append({'id': str(i), 'title': title, 'status': status})
        if new_todos:
            parsed_todos = new_todos

    # 解析资源消耗参数
    try:
        tokens = int(tokens) if tokens else 0
    except (ValueError, TypeError):
        tokens = 0
    try:
        cost = float(cost) if cost else 0.0
    except (ValueError, TypeError):
        cost = 0.0
    try:
        elapsed = int(elapsed) if elapsed else 0
    except (ValueError, TypeError):
        elapsed = 0

    done_cnt = [0]
    total_cnt = [0]
    
    def modifier(data):
        if isinstance(data, list):
            data = {"tasks": data, "global_counters": {}}
        tasks = data.get("tasks", [])
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return data
        t['now'] = clean
        if parsed_todos is not None:
            t['todos'] = parsed_todos
        # 多 Agent 并行进展日志
        at = now_iso()
        agent_id = _infer_agent_id_from_runtime(t)
        agent_label = _AGENT_LABELS.get(agent_id, agent_id)
        log_todos = parsed_todos if parsed_todos is not None else t.get('todos', [])
        log_entry = {
            'at': at, 'agent': agent_id, 'agentLabel': agent_label,
            'text': clean, 'todos': log_todos,
            'state': t.get('state', ''), 'org': t.get('org', ''),
        }
        # 资源消耗（可选字段，有值才写入）
        if tokens > 0:
            log_entry['tokens'] = tokens
        if cost > 0:
            log_entry['cost'] = cost
        if elapsed > 0:
            log_entry['elapsed'] = elapsed
        t.setdefault('progress_log', []).append(log_entry)
        # 限制 progress_log 大小，防止无限增长
        if len(t['progress_log']) > MAX_PROGRESS_LOG:
            t['progress_log'] = t['progress_log'][-MAX_PROGRESS_LOG:]
        t['updatedAt'] = at
        done_cnt[0] = sum(1 for td in t.get('todos', []) if td.get('status') == 'completed')
        total_cnt[0] = len(t.get('todos', []))
        data["tasks"] = tasks
        return data
    
    atomic_json_update(TASKS_FILE, modifier, {"tasks": [], "global_counters": {}})
    _trigger_refresh()
    res_info = ''
    if tokens or cost or elapsed:
        res_info = f' [res: {tokens}tok/${cost:.4f}/{elapsed}s]'
    log.info(f'📡 {task_id} 进展: {clean[:40]}... [{done_cnt[0]}/{total_cnt[0]}]{res_info}')


def cmd_todo(task_id, todo_id, title, status='not-started', detail=''):
    """添加或更新子任务 todo（原子操作）

    status: not-started / in-progress / completed
    detail: 可选，该子任务的详细产出/说明（Markdown 格式）
    """
    # 校验 status 值
    if status not in ('not-started', 'in-progress', 'completed'):
        status = 'not-started'
    result_info = [0, 0]
    
    def modifier(data):
        if isinstance(data, list):
            data = {"tasks": data, "global_counters": {}}
        tasks = data.get("tasks", [])
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return data
        if 'todos' not in t:
            t['todos'] = []
        existing = next((td for td in t['todos'] if str(td.get('id')) == str(todo_id)), None)
        if existing:
            existing['status'] = status
            if title:
                existing['title'] = title
            if detail:
                existing['detail'] = detail
        else:
            item = {'id': todo_id, 'title': title, 'status': status}
            if detail:
                item['detail'] = detail
            t['todos'].append(item)
        t['updatedAt'] = now_iso()
        result_info[0] = sum(1 for td in t['todos'] if td.get('status') == 'completed')
        result_info[1] = len(t['todos'])
        data["tasks"] = tasks
        return data
    
    atomic_json_update(TASKS_FILE, modifier, {"tasks": [], "global_counters": {}})
    _trigger_refresh()
    log.info(f'✅ {task_id} todo [{result_info[0]}/{result_info[1]}]: {todo_id} → {status}')


_CMD_MIN_ARGS = {
    'create': 6, 'todo': 4, 'progress': 3,
    # V8 看板命令
    'approve': 2, 'reject': 2, 'assign': 3, 'done-v2': 2, 'report': 2,
    'ask': 4, 'answer': 4, 'escalate': 3, 'redirect': 4,
    # 查看命令
    'show': 1,
}

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd in _CMD_MIN_ARGS and len(args) < _CMD_MIN_ARGS[cmd]:
        print(f'错误："{cmd}" 命令至少需要 {_CMD_MIN_ARGS[cmd]} 个参数，实际 {len(args)} 个')
        print(__doc__)
        sys.exit(1)
    if cmd == 'create':
        # 解析可选 --current-session-key 和 --huangshang-chat-id 参数
        create_pos = []
        current_session_key = None
        huangshang_chat_id = None
        i = 1
        while i < len(args):
            if args[i] == '--current-session-key' and i + 1 < len(args):
                current_session_key = args[i + 1]
                i += 2
            elif args[i] == '--huangshang-chat-id' and i + 1 < len(args):
                huangshang_chat_id = args[i + 1]
                i += 2
            else:
                create_pos.append(args[i])
                i += 1
        cmd_create(
            create_pos[0] if len(create_pos) > 0 else '',
            create_pos[1] if len(create_pos) > 1 else '',
            create_pos[2] if len(create_pos) > 2 else '',
            create_pos[3] if len(create_pos) > 3 else '',
            create_pos[4] if len(create_pos) > 4 else '',
            create_pos[5] if len(create_pos) > 5 else None,
            current_session_key=current_session_key,
            huangshang_chat_id=huangshang_chat_id
        )
    elif cmd == 'todo':
        # 解析可选 --detail 参数
        todo_pos = []
        todo_detail = ''
        ti = 1
        while ti < len(args):
            if args[ti] == '--detail' and ti + 1 < len(args):
                todo_detail = args[ti + 1]; ti += 2
            else:
                todo_pos.append(args[ti]); ti += 1
        cmd_todo(
            todo_pos[0] if len(todo_pos) > 0 else '',
            todo_pos[1] if len(todo_pos) > 1 else '',
            todo_pos[2] if len(todo_pos) > 2 else '',
            todo_pos[3] if len(todo_pos) > 3 else 'not-started',
            detail=todo_detail,
        )
    elif cmd == 'progress':
        # 解析可选 --tokens/--cost/--elapsed 参数
        pos_args = []
        kw = {}
        i = 1
        while i < len(args):
            if args[i] == '--tokens' and i + 1 < len(args):
                kw['tokens'] = args[i + 1]; i += 2
            elif args[i] == '--cost' and i + 1 < len(args):
                kw['cost'] = args[i + 1]; i += 2
            elif args[i] == '--elapsed' and i + 1 < len(args):
                kw['elapsed'] = args[i + 1]; i += 2
            else:
                pos_args.append(args[i]); i += 1
        cmd_progress(
            pos_args[0] if len(pos_args) > 0 else '',
            pos_args[1] if len(pos_args) > 1 else '',
            pos_args[2] if len(pos_args) > 2 else '',
            tokens=kw.get('tokens', 0),
            cost=kw.get('cost', 0.0),
            elapsed=kw.get('elapsed', 0),
        )
    # ═══════════════════════════════════════════════════════════════
    # V8 看板命令（CLI 分发）
    # ═══════════════════════════════════════════════════════════════
    elif cmd == 'approve':
        cmd_approve(args[1], args[2] if len(args) > 2 else '')
    elif cmd == 'reject':
        cmd_reject(args[1],
                    args[2] if len(args) > 2 else '',
                    args[3] if len(args) > 3 else None)
    elif cmd == 'assign':
        cmd_assign(args[1], args[2], args[3] if len(args) > 3 else '')
    elif cmd == 'done-v2':
        cmd_done_v2(args[1],
                     args[2] if len(args) > 2 else '',
                     args[3] if len(args) > 3 else '')
    elif cmd == 'report':
        cmd_report(args[1],
                   args[2] if len(args) > 2 else '',
                   args[3] if len(args) > 3 else '')
    elif cmd == 'ask':
        cmd_ask(args[1], args[2], args[3] if len(args) > 3 else '')
    elif cmd == 'answer':
        # 支持 --question-id 可选参数
        ans_pos = []
        ans_qid = None
        ai = 1
        while ai < len(args):
            if args[ai] == '--question-id' and ai + 1 < len(args):
                ans_qid = args[ai + 1]; ai += 2
            else:
                ans_pos.append(args[ai]); ai += 1
        cmd_answer(
            ans_pos[0] if len(ans_pos) > 0 else '',
            ans_pos[1] if len(ans_pos) > 1 else '',
            ans_pos[2] if len(ans_pos) > 2 else '',
            question_id=ans_qid,
        )
    elif cmd == 'escalate':
        cmd_escalate(args[1], args[2] if len(args) > 2 else '')
    elif cmd == 'redirect':
        cmd_redirect(args[1], args[2], args[3] if len(args) > 3 else '')
    elif cmd == 'show':
        cmd_show(args[1] if len(args) > 1 else None)
    else:
        print(__doc__)
        sys.exit(1)
