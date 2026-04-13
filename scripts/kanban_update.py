#!/usr/bin/env python3
"""
看板任务更新工具 - 供各省部 Agent 调用

本工具操作 data/tasks_source.json（JSON 看板模式）。
如果您已部署 edict/backend（Postgres + Redis 事件总线模式），
请使用 edict/backend API 端点代替本脚本，或运行迁移脚本：
  python3 edict/migration/migrate_json_to_pg.py

两种模式互相独立，数据不会自动同步。

用法:
  # 新建任务（收旨时）
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令
  # 新建任务并使用当前会话发送通知（避免创建新会话）
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令 "备注" --current-session-key "agent:taizi:feishu:direct:xxx"

  # 更新状态
  python3 kanban_update.py state JJC-20260223-012 Menxia "规划方案已提交门下省"

  # 添加流转记录
  python3 kanban_update.py flow JJC-20260223-012 "中书省" "门下省" "规划方案提交审核"

  # 完成任务
  python3 kanban_update.py done JJC-20260223-012 "/path/to/output" "任务完成摘要"

  # 添加/更新子任务 todo
  python3 kanban_update.py todo JJC-20260223-012 1 "实现API接口" in-progress
  python3 kanban_update.py todo JJC-20260223-012 1 "" completed

  # 🔥 实时进展汇报（Agent 主动调用，频率不限）
  python3 kanban_update.py progress JJC-20260223-012 "正在分析需求，拟定3个子方案" "1.调研技术选型|2.撰写设计文档|3.实现原型"
  # 🔑 Session Key 注册表（解决会话爆炸问题）
  # python3 kanban_update.py session-keys save   JJC-xxx zhongshu menxia "agent:menxia:subagent:abc"
  # python3 kanban_update.py session-keys lookup JJC-xxx zhongshu menxia
  # python3 kanban_update.py session-keys list   JJC-xxx
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

_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    # V3 修复：Assigned → zhongshu（不是 shangshu！）
    # 根因：旧代码将 Assigned 从映射表移除，导致门下省准奏后 state→Assigned 时
    # _resolve_agent_id('Assigned') 返回空 → _notify_agent() 直接退出 → 无人被唤醒
    # 中书省此时已休眠，不会主动醒来转交尚书省 → 流程卡死！
    # 修复后：门下省准奏 → state=Assigned → 程序唤醒中书省 → 中书省转交尚书省
    # 注意：映射到 zhongshu 而非 shangshu，因为按三省六部流程，
    # 门下省准奏后应先回到中书省，由中书省审查后转交尚书省派发。
    #尚书省由中书省通过 LLM 层 sessions_spawn 唤醒（唯一的 LLM 层通知环节）。
    'Assigned': 'zhongshu',
    # 'Review': 'shangshu',  # 已移除：Review 状态由尚书省自行触发，无需程序通知
    'Pending': 'zhongshu',
    # V4 修复：Done → taizi（任务完成时程序自动通知太子，避免中书省 LLM 手动发 message
    # 导致飞书 "Unknown target taizi" 报错。飞书渠道需要真实 chatId，不认识 agent_id。
    # 修复后：中书省标记 Done → 程序唤醒太子 → 太子回奏皇上，中书省无需手动通知。）
    'Done': 'taizi',
}

_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}

_AGENT_LABELS = {
    'main': '太子', 'taizi': '太子',
    'zhongshu': '中书省', 'menxia': '门下省', 'shangshu': '尚书省',
    'libu': '礼部', 'hubu': '户部', 'bingbu': '兵部', 'xingbu': '刑部',
    'gongbu': '工部', 'libu_hr': '吏部', 'zaochao': '钦天监',
    'huangshang': '皇上',
    '太子调度': '太子调度',
}

# ═══════════════════════════════════════════════════════════════════════
# 🎯 针对性通知配置（每个部门独立定制，共性格式+专属内容）
#
# 和 SOUL.md 一样可以随时修改、扩展每个部门的通知模板。
# 修改后立即生效，无需重启服务。
#
# 字段说明：
#   role_hint   : 该部门的核心职责提醒（一句话）
#   action_items: 收到任务后应执行的具体步骤（换行分隔）
#   confirm_fmt : 确认回执的格式模板（{} 会被替换为 task_id + title）
#   deadline    : 确认回执的时间限制描述
# ═══════════════════════════════════════════════════════════════════════
_AGENT_NOTIFY_PROFILES = {
    # ── 太子：皇上代理，总揽全局 ──
    'taizi': {
        'role_hint': '你是太子，皇上在飞书消息的第一接收人和分拣者',
        'action_items': (
            '1. 判断消息类型：闲聊/问答 vs 正式旨意\n'
            '2. 如是旨意 → 整理需求、创建JJC任务、转交中书省\n'
            '3. 如是简单消息 → 直接回复皇上'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，太子正在处理',
        'deadline': '10分钟内确认并开始处理',
    },
    # ── 中书省：规划决策，方案起草 ──
    'zhongshu': {
        'role_hint': '你是中书省，负责接收太子转交的皇上旨意，起草执行方案',
        'action_items': (
            '1. 接旨 → 分析需求，起草执行方案\n'
            '2. 提交门下省审议（必须！）→ 等待准奏/封驳\n'
            '3. 门下省准奏后 → 立即转尚书省执行（必须，最易遗漏！）\n'
            '4. 尚书省返回结果 → 更新看板done → 通过太子回奏皇上'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，中书省开始分析旨意起草方案',
        'deadline': '10分钟内确认并开始分析',
    },
    # ── 门下省：审议把关，方案审核 ──
    'menxia': {
        'role_hint': '你是门下省，三省制的审查核心，负责方案审议',
        'action_items': (
            '1. 直接开始审议方案（无需回复"已收到"）\n'
            '2. 从可行性/完整性/风险/资源四维度审核方案\n'
            '3. 给出「准奏」或「封驳」结论（附修改建议）\n'
            '4. 最多3轮，第3轮强制准奏'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，门下省开始审议方案',
        'deadline': '10分钟内确认并开始审议',
    },
    # ── 尚书省：执行调度，六部协调 ──
    'shangshu': {
        'role_hint': '你是尚书省，负责接收准奏方案后派发六部执行并汇总结果',
        'action_items': (
            '1. 直接开始分析方案（无需回复"已收到"）\n'
            '2. 分析方案 → 确定派发对象（工部/兵部/户部/礼部/刑部/吏部）\n'
            '3. 派发六部并等待各部执行结果\n'
            '4. 汇总六部成果 → 返回中书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，尚书省开始分析方案确定派发对象',
        'deadline': '10分钟内确认并开始分析',
    },
    # ── 六部：各司其职，专业执行 ──
    'libu': {
        'role_hint': '你是礼部，负责文档、规范、用户界面与对外沟通',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求撰写文档/UI文案/对外沟通材料\n'
            '3. 更新看板进展，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，礼部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'hubu': {
        'role_hint': '你是户部，负责数据分析、统计、资源管理与成本分析',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求进行数据收集/清洗/统计/可视化\n'
            '3. 产出必附量化指标或统计摘要，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，户部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'bingbu': {
        'role_hint': '你是兵部，负责工程实现、架构设计与功能开发',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求进行需求分析/方案设计/代码实现\n'
            '3. 确保代码可运行，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，兵部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'xingbu': {
        'role_hint': '你是刑部，负责质量保障、测试验收与合规审计',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求进行代码审查/测试/合规审计\n'
            '3. 产出必附测试结果或审计清单，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，刑部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'gongbu': {
        'role_hint': '你是工部，负责基础设施、部署运维与性能监控',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求进行部署/运维/监控\n'
            '3. 产出必附回滚方案，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，工部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'libu_hr': {
        'role_hint': '你是吏部，负责人事管理、Agent管理与能力培训',
        'action_items': (
            '1. 直接开始执行（无需回复"已收到"）\n'
            '2. 按要求进行Agent管理/Skill优化/培训评估\n'
            '3. 完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，吏部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
}

# 默认通知模板（未在 _AGENT_NOTIFY_PROFILES 中配置的 agent 使用此模板）
_DEFAULT_NOTIFY_PROFILE = {
    'role_hint': '你有新任务需要处理',
    'action_items': '1. 回复确认收到\n2. 按要求执行\n3. 完成后上报',
    'confirm_fmt': '已收到 {task_id} {title}',
    'deadline': '10分钟内确认',
}

MAX_PROGRESS_LOG = 100  # 单任务最大进展日志条数

# ═══════════════════════════════════════════════════════════════════════
# 🪝 状态变更钩子（事件驱动：状态变化 → 自动触发回调）
#
# 钩子注册表：key = 目标状态，value = 回调函数列表
# 状态变更成功后自动触发对应的钩子函数。
# 扩展方法：只需在此注册新函数，无需改动 cmd_state() 主逻辑。
# 
# 注意：钩子函数必须在注册前定义，因此将钩子函数定义移到注册之前
# ═══════════════════════════════════════════════════════════════════════


def hook_notify_taizi(task_id, old_state, new_state, task):
    """钩子：状态变化时异步通知太子（非阻塞，含冷却去重）。

    用于 Menxia / Assigned / Done 等关键节点，
    让太子主动收到进展推送，无需轮询看板。
    包含冷却去重：同一任务90秒内不重复通知太子。
    """
    # 🔒 冷却去重：检查是否刚通知过太子
    try:
        last_notify = task.get('_lastNotify', {})
        taizi_info = last_notify.get('taizi', {})
        if taizi_info.get('done'):
            last_at = taizi_info.get('at', '')
            if last_at:
                try:
                    last_dt = datetime.datetime.fromisoformat(last_at.replace('Z', '+00:00'))
                    now_dt = datetime.datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.datetime.now()
                    elapsed = (now_dt - last_dt).total_seconds()
                    if elapsed < _NOTIFY_COOLDOWN_SEC:
                        log.info(f'🪝 钩子冷却跳过：{task_id} → taizi，{elapsed:.0f}s前已通知')
                        return
                except Exception:
                    pass
    except Exception:
        pass

    title = task.get('title', '')
    old_label = STATE_ORG_MAP.get(old_state, old_state or '未知')
    new_label = STATE_ORG_MAP.get(new_state, new_state)
    msg = (
        f"📋 任务状态变更通知\n"
        f"任务ID: {task_id}\n"
        f"任务标题: {title}\n"
        f"状态变化: {old_label} → {new_label}\n"
        f"变更时间: {now_iso()}\n"
        f"请太子知悉。\n"
        f"⚠️ 看板已有此任务，请勿重复创建。"
    )
    try:
        subprocess.Popen(
            ['openclaw', 'agent', '--agent', 'taizi', '-m', msg, '--timeout', '120'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.info(f'🪝 钩子触发：已通知太子 | {task_id} {old_state}→{new_state}')
    except Exception as e:
        log.warning(f'🪝 钩子执行失败 (notify_taizi): {e}')

    # 🔒 记录本次通知（写入 _lastNotify 以便冷却去重）
    def _record_taizi_notify(tasks):
        t = find_task(tasks, task_id)
        if not t:
            return tasks
        t.setdefault('_lastNotify', {})['taizi'] = {'at': now_iso(), 'remark': f'hook:{old_state}→{new_state}', 'done': True}
        return tasks
    try:
        atomic_json_update(TASKS_FILE, _record_taizi_notify, [])
    except Exception:
        pass


# 状态变更钩子注册表（钩子函数已定义）
_STATE_CHANGE_HOOKS = {
    'Menxia':   [hook_notify_taizi],   # 任务到门下省 → 通知太子
    'Assigned': [hook_notify_taizi],   # 任务到尚书省 → 通知太子
    'Done':     [hook_notify_taizi],   # 任务完成 → 通知太子
}

# Doing 状态停滞自动催办配置
_DOING_STALL_SEC = 720  # 12分钟（720秒）无进展则自动催办


def _fire_state_hooks(task_id, old_state, new_state, task):
    """触发注册的状态变更钩子（容错：单个钩子失败不影响其他）"""
    hooks = _STATE_CHANGE_HOOKS.get(new_state, [])
    for hook in hooks:
        try:
            hook(task_id, old_state, new_state, task)
        except Exception as e:
            log.warning(f'🪝 钩子执行失败 ({new_state}): {e}')


def _start_doing_stall_watchdog(task_id, task):
    """钩子：进入 Doing 状态时，启动后台停滞检测（12分钟无进展 → 自动催办）。

    实现原理：
    1. 记录当前 progress_log 条数快照
    2. 启动一个后台进程，等待 12 分钟后检查
    3. 如果 progress_log 没有新增条目，说明 12 分钟内无任何进展
    4. 向负责该任务的部门发送催办通知
    """
    org = task.get('org', '')
    agent_id = _ORG_AGENT_MAP.get(org, '')
    if not agent_id:
        return
    agent_label = _AGENT_LABELS.get(agent_id, agent_id)
    title = task.get('title', '')
    progress_count = len(task.get('progress_log', []))

    # 转义路径和 task_id，防止注入
    tasks_file_escaped = json.dumps(str(TASKS_FILE))
    task_id_escaped = json.dumps(task_id)
    title_escaped = json.dumps(title)
    agent_id_escaped = json.dumps(agent_id)
    agent_label_escaped = json.dumps(agent_label)
    
    # 后台检测脚本（作为独立进程运行，不阻塞主流程）
    watchdog_script = f'''#!/usr/bin/env python3
import json, pathlib, subprocess, sys, time
time.sleep({_DOING_STALL_SEC})
try:
    tasks_file = {tasks_file_escaped}
    tasks = json.loads(pathlib.Path(tasks_file).read_text())
    t = next((x for x in tasks if x.get("id") == {task_id_escaped}), None)
    if not t:
        sys.exit(0)
    if t.get("state") != "Doing":
        sys.exit(0)  # 已不在 Doing 状态，无需催办
    current_count = len(t.get("progress_log", []))
    if current_count <= {progress_count}:
        # 12分钟内无任何进展 → 发送催办
        msg = (
            "⏰ 自动催办通知\\n"
            "任务ID: " + {task_id_escaped} + "\\n"
            "任务标题: " + {title_escaped} + "\\n"
            f"已等待: {_DOING_STALL_SEC // 60} 分钟\\n\\n"
            "系统检测到该任务进入 Doing 状态后 12 分钟内无任何进展更新。\\n"
            "请立即确认任务状态并更新进展（progress 命令）。\\n\\n"
            "⚠️ 看板已有此任务，请勿重复创建。"
        )
        # 查找该任务对应的 session key（精准发送到子代理，不打 main session）
        existing_key = None
        try:
            tasks_data = json.loads(pathlib.Path({tasks_file_escaped}).read_text())
            task_obj = next((x for x in tasks_data if x.get("id") == {task_id_escaped}), None)
            if task_obj:
                target = {agent_id_escaped}
                for _pair, _entry in task_obj.get("session_keys", {{}}).items():
                    _agents = _entry.get("agents", [])
                    if target in _agents and _entry.get("sessionKey"):
                        existing_key = _entry["sessionKey"]
                        break
        except Exception:
            pass

        if existing_key:
            subprocess.Popen(
                ["openclaw", "sessions", "send", "--session-key", existing_key, "-m", msg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"[stall-watchdog] {task_id_escaped} 已通过 session 催办 {agent_label_escaped}", flush=True)
        else:
            # 【关键修复】使用 openclaw agent（非 sessions spawn）确保催办消息被 Agent 接收
            subprocess.Popen(
                ["openclaw", "agent", "--agent", {agent_id_escaped}, "-m", msg, "--timeout", "120"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"[stall-watchdog] {task_id_escaped} 已 openclaw agent 催办 {agent_label_escaped}", flush=True)
except Exception as e:
    print(f"[stall-watchdog] {task_id_escaped} 检查失败: {e}", flush=True)
'''
    try:
        subprocess.Popen(
            ['python3', '-c', watchdog_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.info(f'⏰ 停滞看门狗已启动 | {task_id} → {agent_label} | {_DOING_STALL_SEC//60}分钟后检查')
    except Exception as e:
        log.warning(f'⏰ 停滞看门狗启动失败 ({task_id}): {e}')


def load():
    return atomic_json_read(TASKS_FILE, [])


def _trigger_refresh():
    """异步触发 live_status 刷新，不阻塞调用方。"""
    try:
        subprocess.Popen(['python3', str(REFRESH_SCRIPT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _start_liubu_alive_check(task_id, task):
    """【V5 修复】程序级兜底：进入 Doing 状态后 60 秒检查六部是否被唤醒。

    根因：尚书省→六部 的通知完全依赖尚书省 LLM 层 sessions_spawn。
    如果尚书省用了 sessions_yield（不触发目标Agent推理），
    或者 LLM 推理失败，六部永远不会收到消息。
    pipeline_watchdog 的断链检测需要 3.5 分钟才能触发，太慢了。

    此函数作为最后一道防线：
    1. 后台等待 60 秒
    2. 检查六部是否有任何活动迹象（progress_log 新增、flow_log 回复）
    3. 如果六部零活动 → 程序级 openclaw agent 直接唤醒六部
    4. 同时通知尚书省"六部未响应，已程序级兜底唤醒"
    """
    org = task.get('org', '')
    agent_id = _ORG_AGENT_MAP.get(org, '')
    if not agent_id or agent_id not in ('libu', 'hubu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr'):
        return  # 只对六部生效

    agent_label = _AGENT_LABELS.get(agent_id, agent_id)
    title = task.get('title', '')
    progress_count = len(task.get('progress_log', []))
    flow_count = len(task.get('flow_log', []))

    tasks_file_escaped = json.dumps(str(TASKS_FILE))
    task_id_escaped = json.dumps(task_id)
    title_escaped = json.dumps(title)
    agent_id_escaped = json.dumps(agent_id)
    agent_label_escaped = json.dumps(agent_label)
    progress_count_escaped = str(progress_count)
    flow_count_escaped = str(flow_count)

    # 【V7 修复】将等待时间从60秒缩短到45秒
    # 根因：60秒等待太长，六部在空转浪费近1分钟。45秒足够覆盖
    # openclaw agent 启动延迟（~15-20秒），同时不会因过快误判。
    watchdog_script = f'''#!/usr/bin/env python3
import json, pathlib, subprocess, sys, time
time.sleep(45)
TASKS_FILE = pathlib.Path({tasks_file_escaped})
try:
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        tasks = json.load(f)
except Exception:
    sys.exit(0)
task = next((t for t in tasks if t.get("id") == {task_id_escaped}), None)
if not task or task.get("state") != "Doing":
    sys.exit(0)
new_progress = len(task.get("progress_log", []))
new_flow = len(task.get("flow_log", []))
# 六部有新增进展或回复 → 正常
if new_progress > {progress_count_escaped} or new_flow > {flow_count_escaped}:
    sys.exit(0)
# 六部零活动 → 程序级兜底唤醒
agent_id = {agent_id_escaped}
agent_label = {agent_label_escaped}
msg = (
    "🔔 程序级兜底唤醒\\n"
    "任务ID: {task_id_escaped}\\n"
    "标题: {title_escaped}\\n"
    "原因: 进入 Doing 状态 45 秒后六部无任何活动，"
    "尚书省可能未正确使用 sessions_spawn 通知六部（或使用了 sessions_yield）。\\n"
    "请立即执行任务，完成后使用 kanban 流转记录回复尚书省。\\n"
    "⚠️ 禁止使用 sessions_yield！必须用 sessions_spawn 派发！"
)
try:
    result = subprocess.run(
        ["openclaw", "agent", "--agent", agent_id, "-m", msg, "--timeout", "120"],
        capture_output=True, text=True, timeout=130,
    )
    if result.returncode == 0:
        print(f"[兜底唤醒] 已程序级唤醒 {{agent_label}} ({{agent_id}}) | 任务 {{task_id}}")
    else:
        print(f"[兜底唤醒] 唤醒 {{agent_label}} 失败: rc={{result.returncode}}")
except Exception as e:
    print(f"[兜底唤醒] 唤醒 {{agent_label}} 异常: {{e}}")
'''
    try:
        subprocess.Popen(['python3', '-c', watchdog_script],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        log.info(f'🛡️ 已启动六部兜底检查 | {task_id} → {agent_label} ({agent_id}) | 45秒后检查')
    except Exception as e:
        log.warning(f'🛡️ 启动六部兜底检查失败: {e}')


def _resolve_agent_id(target):
    """根据目标名称（部门中文名或状态英文名）解析 agent_id。

    优先按部门名查 _ORG_AGENT_MAP，其次按状态名查 _STATE_AGENT_MAP。
    """
    if not target:
        return ''
    aid = _ORG_AGENT_MAP.get(target)
    if aid:
        return aid
    return _STATE_AGENT_MAP.get(target, '')


def _async_spawn_and_save_key(agent_id, message, task_id, from_id, to_label):
    """异步唤醒 Agent 并标记通知完成（完全不阻塞调用方）。

    【V3 关键修复】直接调用 openclaw agent，消除双层子进程嵌套。

    根因分析（用户反馈部署后中书省仍无法唤醒）:
    - 旧方案(V2): python3 -c script → subprocess.run(["openclaw", "agent", ...])
      双层子进程嵌套导致 openclaw agent 运行环境异常（信号处理、
      进程组、文件描述符传递等问题），消息能送达 Gateway 但 Agent
      实际不处理。只有心跳路径（pipeline_watchdog/server.py 的单层调用）能成功。
    - 新方案(V3): subprocess.Popen(["openclaw", "agent", ...]) + daemon thread
      与 dashboard/server.py wake_agent() 完全一致的单层直接调用。
      消除 python3 -c 中间层，确保 openclaw agent 与心跳路径环境一致。

    sessionKey 管理变更：
    - openclaw agent 使用 Agent 的 main session，不返回独立 sessionKey
    - 后续 Agent 自身的 sessions_spawn（在 SOUL.md 流程中）会创建子会话
    - 程序层不再依赖 sessions spawn 返回的 sessionKey
    """
    # ── 日志文件（记录 openclaw agent 的完整输出，用于诊断）──
    _log_dir = _BASE / 'data' / 'async_spawn_logs'
    _log_dir.mkdir(parents=True, exist_ok=True)
    _ts_tag = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_file = _log_dir / f'{task_id}_{agent_id}_{_ts_tag}.log'

    # ── V3: 直接启动 openclaw agent 进程（单层调用，与心跳路径一致）──
    try:
        _lf = open(str(_log_file), 'w')
        proc = subprocess.Popen(
            ["openclaw", "agent", "--agent", agent_id, "-m", message, "--timeout", "120"],
            stdout=_lf, stderr=_lf,
        )
        log.info(f'🚀 异步唤醒: {to_label} ({agent_id}) | 任务 {task_id} | pid={proc.pid} | 日志: {_log_file.name}')
    except Exception as e:
        log.warning(f'🚀 异步唤醒启动失败: {to_label} ({agent_id}): {e}')
        return

    # ── 异步等待线程：完成后更新 _lastNotify + 写入诊断日志 ──
    def _wait_and_record():
        import time as _t
        try:
            # 等待进程完成（最多140秒）
            _rc = proc.wait(timeout=140)

            # 写入诊断信息到日志文件
            try:
                _lf.write(f'\n[async-wake] process exited with rc={_rc}\n')
            except Exception:
                pass
            finally:
                try:
                    _lf.close()
                except Exception:
                    pass

            if _rc == 0:
                # 成功：标记 _lastNotify done=True
                def _mark_done(tasks):
                    t = find_task(tasks, task_id)
                    if t:
                        t.setdefault("_lastNotify", {}).setdefault(agent_id, {})["done"] = True
                    return tasks
                try:
                    atomic_json_update(TASKS_FILE, _mark_done, [])
                except Exception:
                    pass
                log.info(f'🚀 异步唤醒成功: {to_label} ({agent_id}) | task={task_id} | rc=0 | 日志: {_log_file.name}')
            else:
                # 失败：读取日志文件最后500字符写入 warning
                try:
                    _diag = _log_file.read_text()[-500:] if _log_file.exists() else '(日志文件不存在)'
                except Exception:
                    _diag = '(无法读取日志)'
                log.warning(f'🚀 异步唤醒失败: {to_label} ({agent_id}) | task={task_id} | rc={_rc} | 诊断: {_diag}')
                # 5秒后重试一次（与 pipeline_watchdog.py wake_agent 一致）
                _t.sleep(5)
                try:
                    _lf2 = open(str(_log_dir / f'{task_id}_{agent_id}_{_ts_tag}_retry.log'), 'w')
                    retry_proc = subprocess.Popen(
                        ["openclaw", "agent", "--agent", agent_id, "-m", message, "--timeout", "120"],
                        stdout=_lf2, stderr=_lf2,
                    )
                    retry_rc = retry_proc.wait(timeout=140)
                    try:
                        _lf2.close()
                    except Exception:
                        pass
                    if retry_rc == 0:
                        def _mark_done2(tasks):
                            t = find_task(tasks, task_id)
                            if t:
                                t.setdefault("_lastNotify", {}).setdefault(agent_id, {})["done"] = True
                            return tasks
                        try:
                            atomic_json_update(TASKS_FILE, _mark_done2, [])
                        except Exception:
                            pass
                        log.info(f'🚀 异步唤醒重试成功: {to_label} ({agent_id}) | task={task_id}')
                    else:
                        log.warning(f'🚀 异步唤醒重试仍失败: {to_label} ({agent_id}) | task={task_id} | rc={retry_rc}')
                except Exception as _retry_e:
                    log.warning(f'🚀 异步唤醒重试异常: {to_label} ({agent_id}): {_retry_e}')
        except subprocess.TimeoutExpired:
            try:
                _lf.write(f'\n[async-wake] timeout (140s)\n')
                _lf.close()
            except Exception:
                pass
            log.warning(f'🚀 异步唤醒超时: {to_label} ({agent_id}) | task={task_id} | 140s')
        except Exception as _e:
            try:
                _lf.close()
            except Exception:
                pass
            log.warning(f'🚀 异步等待异常: {to_label} ({agent_id}): {_e}')

    threading.Thread(target=_wait_and_record, daemon=True).start()


def _notify_agent(agent_id, task_id, from_org, to_org, title='', remark='', current_session_key=None, _retry=0):
    """通知目标 Agent 有新任务/流转（含 Session Key 复用机制 — 程序层核心保障）。

    修改说明（发完即走）：首次通知改为全异步，不再阻塞等待 Agent 回复确认。
    sessionKey 由后台脚本异步提取并保存到注册表。

    - 🔑 Session Key 机制（核心保障）：优先复用已有会话（sessions_send），避免会话膨胀。
      首次通知时使用 --json 获取 sessionKey 并自动保存到任务的 session_keys 注册表。
      后续同一 from→to 对的通知将自动复用已保存的 sessionKey。
    - 非阻塞：有 sessionKey 时通过 Popen 异步执行（sessions_send），不延迟主流程。
    - 无 sessionKey 时通过 subprocess.run 同步获取（--json 模式），确保能提取 sessionKey。
    - 容错：通知失败仅记录日志，不影响看板写入结果。
    - 🎯 针对性通知：根据 _AGENT_NOTIFY_PROFILES 为每个部门生成专属通知内容，
      包含该部门的核心职责提醒和具体执行步骤指导。
    - 唤醒重试：首次失败后 3 秒重试一次。
    - 🔒 会话去重：同一任务 + 同一目标 Agent 在短时间内不重复唤醒。
    - 📨 当前会话支持：如果提供了 current_session_key，则直接发送到该会话，
      而不是创建新会话，确保消息回到原始对话。
    """
    if not agent_id:
        return

    # ═══════════════════════════════════════════════════════════════════════
    # 🔒 会话去重检查：防止同一任务重复唤醒同一 Agent
    # 改进：同一任务 + 同一 Agent + 同一目标部门(remark) 只通知一次
    # ═══════════════════════════════════════════════════════════════════════
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if t:
            # 🔒 全局限制：单任务通知次数上限
            notify_count = len(t.get('_lastNotify', {}))
            if notify_count >= _MAX_NOTIFY_PER_TASK:
                log.warning(f'🔒 全局去重：{task_id} 已通知 {notify_count} 次，达到上限 {_MAX_NOTIFY_PER_TASK}，停止通知')
                return
            # 🔒 冷却去重：同一任务+同一Agent需等待冷却时间才能再次通知
            # 修复：已确认成功的通知用 90s 冷却，异步待确认的用 30s 冷却（允许快速重试）
            last_notify = t.get('_lastNotify', {})
            agent_notify_info = last_notify.get(agent_id, {})
            if agent_notify_info.get('done'):
                last_at = agent_notify_info.get('at', '')
                if last_at:
                    try:
                        last_dt = datetime.datetime.fromisoformat(last_at.replace('Z', '+00:00'))
                        now_dt = datetime.datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.datetime.now()
                        elapsed = (now_dt - last_dt).total_seconds()
                        if elapsed < _NOTIFY_COOLDOWN_SEC:
                            log.info(f'🔒 冷却去重：{task_id} → {agent_id}，{elapsed:.0f}s前已通知，需等待 {_NOTIFY_COOLDOWN_SEC}s')
                            return
                    except Exception:
                        pass
            else:
                # 异步待确认：使用更短的冷却时间，允许快速重试
                last_at = agent_notify_info.get('at', '')
                if last_at:
                    try:
                        last_dt = datetime.datetime.fromisoformat(last_at.replace('Z', '+00:00'))
                        now_dt = datetime.datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.datetime.now()
                        elapsed = (now_dt - last_dt).total_seconds()
                        if elapsed < _NOTIFY_COOLDOWN_ASYNC_SEC:
                            log.info(f'🔒 异步冷却去重：{task_id} → {agent_id}，{elapsed:.0f}s前已发送（未确认），需等待 {_NOTIFY_COOLDOWN_ASYNC_SEC}s')
                            return
                    except Exception:
                        pass
    except Exception:
        pass
    to_label = _AGENT_LABELS.get(agent_id, agent_id)

    # 🎯 查找该部门的针对性通知配置（共性格式 + 专属内容）
    profile = _AGENT_NOTIFY_PROFILES.get(agent_id, _DEFAULT_NOTIFY_PROFILE)
    role_hint = profile.get('role_hint', '')
    action_items = profile.get('action_items', '')
    confirm_fmt = profile.get('confirm_fmt', '已收到 {task_id} {title}').format(task_id=task_id, title=title)
    deadline = profile.get('deadline', '10分钟内确认')

    # 组装针对性通知消息
    parts = [
        f"📢 任务通知 → {to_label} - {task_id}",
        f"",
        f"📌 {role_hint}",
        f"",
        f"📋 任务信息：",
        f"  · 任务ID：{task_id}",
        f"  · 任务标题：{title}",
        f"  · 流转路径：{from_org} → {to_org}",
        f"  · 说明：{remark}",
    ]
    # 【V5 修复】通知中附带产出路径和进展摘要，解决门下省二次审核收不到文档内容的问题
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if t:
            _output = (t.get('output', '') or '').strip()
            if _output:
                parts.append(f"  · 产出路径：{_output}")
            # 附带最近一条进展（让接收方快速了解上下文）
            _progress = t.get('progress_log', [])
            if _progress:
                _last_p = _progress[-1] if _progress else {}
                _last_text = (_last_p.get('text', '') or _last_p.get('now', '') or '').strip()
                if _last_text:
                    parts.append(f"  · 最新进展：{_last_text[:100]}")
            # 附带六部执行摘要（flow_log 中六部的最近回复）
            _flow = t.get('flow_log', [])
            _liubu_agent_ids = ('libu', 'hubu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr')
            _liubu_replies = [e for e in _flow if (e.get('from', '') or '').strip().lower() in _liubu_agent_ids]
            if _liubu_replies:
                _last_reply = _liubu_replies[-1]
                parts.append(f"  · 六部最新回复：{_last_reply.get('to', '')}←{_last_reply.get('from', '')} - {(_last_reply.get('remark', '') or '')[:80]}")
    except Exception:
        pass

    if action_items:
        parts.append(f"")
        parts.append(f"🚀 你需要做的：")
        parts.append(action_items)
    parts.append(f"")
    parts.append(f"请立即开始处理，无需回复确认！")

    message = '\n'.join(parts)

    # ── 🔑 Session Key 复用机制：优先 send，首次 spawn ──
    # 解析发送方 agent_id（用于 session_keys pair 查找）
    from_id = _resolve_agent_id(from_org) or (from_org.strip().lower() if from_org else '')

    # 查找已有 sessionKey（按目标 Agent 查找，忽略方向性，支持封驳回传场景）
    existing_key = None
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if t:
            existing_key = _find_session_key_for_agent(t, agent_id)
    except Exception:
        pass

    # ── 修复：标记通知路径类型（同步/异步），用于去重记录 ──
    _notify_sync_success = False  # 异步路径默认 False，同步路径设为 True

    try:
        _session_send_failed = False  # V4：标记 sessions_send 是否失败（用于降级判断）
        if current_session_key:
            # 有当前会话key → 直接发送到当前会话
            result = subprocess.run(
                ['openclaw', 'sessions', 'send', '--session-key', current_session_key, '-m', message],
                capture_output=True, text=True, timeout=60,
            )
            _notify_sync_success = result.returncode == 0
            if _notify_sync_success:
                log.info(f'📨 已发送【当前会话】通知给 {to_label} ({agent_id}) | 任务 {task_id} | 使用当前会话key')
            else:
                _session_send_failed = True
                log.warning(f'📨 【当前会话】通知 {to_label} 失败: rc={result.returncode} | stderr: {(result.stderr or "")[:200]} → V4降级: 清除过期key，改用 openclaw agent 唤醒')
        elif existing_key:
            # 有 key → sessions_send 复用会话
            result = subprocess.run(
                ['openclaw', 'sessions', 'send', '--session-key', existing_key, '-m', message],
                capture_output=True, text=True, timeout=60,
            )
            _notify_sync_success = result.returncode == 0
            if _notify_sync_success:
                log.info(f'🔑 已发送【复用会话】通知给 {to_label} ({agent_id}) | 任务 {task_id} | key={existing_key[:30]}...')
            else:
                _session_send_failed = True
                log.warning(f'🔑 【复用会话】通知 {to_label} 失败: rc={result.returncode} | stderr: {(result.stderr or "")[:200]} → V4降级: 清除过期key，改用 openclaw agent 唤醒')

        # ── V4 修复：sessions_send 失败 → 清除过期 key → 降级到 openclaw agent 唤醒 ──
        # 根因：session 过期后 sessions_send 返回非零，但原代码只记 warning 不降级，
        # 导致通知丢失。礼部5分钟收不到通知就是这个原因。
        if _session_send_failed:
            # 清除过期的 session_key（防止后续继续尝试已失效的 key）
            def _clear_stale_key(tasks):
                t = find_task(tasks, task_id)
                if not t:
                    return tasks
                keys = t.get('session_keys', {})
                for pair_key in list(keys.keys()):
                    if agent_id in pair_key:
                        stale_key = keys.pop(pair_key, None)
                        if stale_key:
                            log.info(f'🗑️ 已清除过期 session_key: {pair_key} = {stale_key[:30]}...')
                return tasks
            try:
                atomic_json_update(TASKS_FILE, _clear_stale_key, [])
            except Exception as _e:
                log.warning(f'🗑️ 清除过期 key 失败: {_e}')

            # 降级到 openclaw agent 直接唤醒（与无 key 路径一致）
            log.info(f'🔄 V4降级: sessions_send 失败，改用 openclaw agent 唤醒 {to_label} ({agent_id}) | 任务 {task_id}')
            _notify_sync_success = False  # 降级后走异步路径
            _async_spawn_and_save_key(
                agent_id=agent_id, message=message, task_id=task_id,
                from_id=from_id, to_label=to_label,
            )
        elif not current_session_key and not existing_key:
            # 无 key → 异步唤醒 Agent + 异步保存 sessionKey（完全不阻塞主流程）
            # _notify_sync_success 保持 False，由后台脚本成功后标记 True
            _async_spawn_and_save_key(
                agent_id=agent_id, message=message, task_id=task_id,
                from_id=from_id, to_label=to_label,
            )
    except Exception as e:
        log.warning(f'⚠️ 通知 {to_label} ({agent_id}) 失败 (第{_retry+1}次): {e}')
        if _retry < 1:
            import time as _time
            _time.sleep(3)
            _notify_agent(agent_id, task_id, from_org, to_org, title, remark, _retry=_retry + 1)
        return

    # ═══════════════════════════════════════════════════════════════════════
    # 🔒 会话去重：记录本次通知
    #
    # 修复（解决中书永远收不到通知的 Bug）：
    # 原代码在异步 spawn 完成之前就标记 done=True，导致：
    #   1. 如果异步 spawn 失败，去重记录仍标记为 done
    #   2. 后续 90 秒内所有重试通知被冷却机制拦截
    #   3. 中书永远不会收到通知
    #
    # 修复方案：
    #   - 当走异步路径（_async_spawn_and_save_key）时，先标记 done=False（待确认）
    #   - 后台脚本成功时将 done 改为 True
    #   - 后台脚本失败时不修改，让冷却机制在 30 秒后自动放行重试
    #   - 当走同步路径（sessions_send 复用已有会话）时，直接标记 done=True
    # ═══════════════════════════════════════════════════════════════════════
    def _record_notify(tasks):
        t = find_task(tasks, task_id)
        if not t:
            return tasks
        t.setdefault('_lastNotify', {})[agent_id] = {
            'at': now_iso(),
            'remark': remark[:60] if remark else '',
            'done': _notify_sync_success,  # 同步路径=True，异步路径=False
        }
        return tasks
    try:
        atomic_json_update(TASKS_FILE, _record_notify, [])
    except Exception:
        pass  # 去重记录写入失败不影响主流程


def agent_communicate(task_id, from_agent, to_agent, message, timeout=120):
    """统一通信入口：自动判断 spawn/send，强制复用 session（程序层核心保障）。

    调用方只需提供 task_id + from/to + message，无需关心底层 spawn/send。
    - 有 sessionKey → sessions_send 复用会话（防止会话膨胀）
    - 无 sessionKey → openclaw agent --json → 提取 sessionKey → 自动保存

    Args:
        task_id: 任务 ID（如 JJC-20260223-012）
        from_agent: 发送方 agent_id 或部门名（如 'zhongshu' 或 '中书省'）
        to_agent: 接收方 agent_id 或部门名（如 'menxia' 或 '门下省'）
        message: 消息内容
        timeout: 命令超时秒数

    Returns:
        sessionKey str 或 None
    """
    # 解析 agent_id
    from_id = _resolve_agent_id(from_agent) or (from_agent.strip().lower() if from_agent else '')
    to_id = _resolve_agent_id(to_agent) or (to_agent.strip().lower() if to_agent else '')

    if not from_id or not to_id:
        log.warning(f'agent_communicate: 无法解析 agent_id (from={from_agent}, to={to_agent})')
        return None

    pair = _normalize_pair(from_id, to_id)
    to_label = _AGENT_LABELS.get(to_id, to_id)

    # ── 1. 查找已有 sessionKey（按目标 Agent 查找，支持封驳回传） ──
    existing_key = None
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if t:
            existing_key = _find_session_key_for_agent(t, to_id)
    except Exception as e:
        log.warning(f'agent_communicate: 查找 session_key 失败: {e}')

    # ── 2a. 有 key → sessions_send（复用会话）──
    # V4 修复：Popen（不等结果、吞输出）→ subprocess.run() + returncode 检查 + 失败降级
    if existing_key:
        try:
            send_result = subprocess.run(
                ['openclaw', 'sessions', 'send', '--session-key', existing_key, '-m', message],
                capture_output=True, text=True, timeout=60,
            )
            if send_result.returncode == 0:
                log.info(f'🔑 复用会话成功: {task_id} | {pair} → sessions_send')
                return existing_key
            else:
                # V4: sessions_send 失败 → 清除过期 key → 降级到 openclaw agent
                send_err = (send_result.stderr or '')[:200]
                log.warning(f'🔑 sessions_send 失败: {pair} | rc={send_result.returncode} | {send_err} → V4降级: 清除过期key，改用 openclaw agent')
                # 清除过期的 session_key
                def _clear_stale_key_comm(tasks):
                    t = find_task(tasks, task_id)
                    if not t:
                        return tasks
                    keys = t.get('session_keys', {})
                    for pk in list(keys.keys()):
                        if to_id in pk:
                            stale = keys.pop(pk, None)
                            if stale:
                                log.info(f'🗑️ [communicate] 已清除过期 session_key: {pk}')
                    return tasks
                try:
                    atomic_json_update(TASKS_FILE, _clear_stale_key_comm, [])
                except Exception:
                    pass
                # 不 return，继续走下方的 openclaw agent 路径
        except Exception as e:
            log.warning(f'🔑 sessions_send 异常: {pair}: {e} → V4降级: 改用 openclaw agent')

    # ── 2b. 无 key → openclaw agent 唤醒（确保消息被 Agent LLM 处理）──
    try:
        result = subprocess.run(
            ["openclaw", "agent", "--agent", to_id, "-m", message, "--timeout", "120"],
            capture_output=True, text=True, timeout=timeout + 30,
        )
        output = (result.stdout or '') + (result.stderr or '')

        if result.returncode == 0:
            log.info(f'📨 通信完成 [openclaw agent]: {task_id} | {pair} → {to_label}')
            return None  # openclaw agent 不返回独立 sessionKey
        else:
            log.warning(f'📨 通信失败: {task_id} | {pair} → {to_label} | rc={result.returncode} | {output[:200]}')
            return None
    except subprocess.TimeoutExpired:
        log.warning(f'agent_communicate 超时: {task_id} | {pair} → {to_label} ({timeout}s)')
        return None
    except Exception as e:
        log.error(f'agent_communicate 异常: {task_id} | {pair} → {e}')
        return None


def find_task(tasks, task_id):
    return next((t for t in tasks if t.get('id') == task_id), None)


# ═══════════════════════════════════════════════════════════════════════
# 🔑 Session Key 注册表（解决会话爆炸问题）
#
# 每个任务维护一个 session_keys 字典，记录 agent 对之间的 sessionKey。
# Agent 跨部门通信时，先 lookup 已有 key → 有则用 sessions_send 复用会话，
# 没有则用 sessions_spawn 创建 → 保存返回的 sessionKey 供后续复用。
#
# 用法:
#   kanban_update.py session-keys save   JJC-xxx zhongshu menxia "agent:menxia:subagent:abc-123"
#   kanban_update.py session-keys lookup JJC-xxx zhongshu menxia
#   kanban_update.py session-keys list   JJC-xxx
# ═══════════════════════════════════════════════════════════════════════

def _find_session_key_for_agent(task_data, target_agent_id):
    """按目标 Agent 查找该任务中已存在的 session key（忽略方向性）。
    
    封驳场景：from=menxia, agent=zhongshu
    中书省的 key 存在 "taizi:zhongshu" 下，方向性查找 "menxia:zhongshu" 会找不到
    因此遍历所有 pair，找包含 target_agent_id 的条目
    
    Args:
        task_data: 任务 dict（来自 tasks_source.json）
        target_agent_id: 目标 agent_id（如 'zhongshu'）
    
    Returns:
        sessionKey 字符串，或 None
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


def _normalize_pair(agent_a, agent_b):
    """将两个 agent_id 规范化为有序 pair key（字母序排列，确保双向查找一致）。
    
    例如: _normalize_pair('zhongshu', 'menxia') → 'menxia:zhongshu'
          _normalize_pair('menxia', 'zhongshu') → 'menxia:zhongshu'  (相同结果)
    """
    a = agent_a.strip().lower()
    b = agent_b.strip().lower()
    return f'{a}:{b}'


def _extract_session_key_from_json(output_text):
    """从 openclaw CLI 的 --json 输出中提取 sessionKey。
    
    OpenClaw CLI 在 --json 模式下返回的 JSON 结构包含 sessionKey 和 sessionId 字段。
    若未指定 --json、输出为空、非 JSON 或 sessionKey 为 null，返回 None。
    """
    if not output_text:
        return None
    # 尝试直接解析完整 JSON
    try:
        data = json.loads(output_text.strip())
        key = data.get('sessionKey') or data.get('session_key')
        if key and str(key).strip().lower() not in ('null', 'none', ''):
            return str(key).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    # 正则兜底：从混合输出中提取 JSON 块（CLI 可能输出进度信息+JSON）
    try:
        json_match = re.search(r'\{[^{}]*"sessionKey"\s*:\s*"([^"]+)"[^{}]*\}', output_text)
        if json_match:
            key = json_match.group(1)
            if key.strip().lower() not in ('null', 'none', ''):
                return key.strip()
    except Exception:
        pass
    return None


def _lookup_session_key(task_id, agent_a, agent_b):
    """查找任务中两个 agent 之间的 sessionKey（兼容新旧 pair 格式）。
    
    优先查方向性 pair（新），再查 sorted pair（旧），最后按目标 Agent 遍历。
    返回 sessionKey 字符串或 None。
    """
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if not t:
            return None
        # 优先：方向性 pair（新格式）
        dir_pair = _normalize_pair(agent_a, agent_b)
        entry = t.get('session_keys', {}).get(dir_pair)
        if entry and entry.get('sessionKey'):
            return entry.get('sessionKey')
        # 兼容：旧的 sorted pair
        old_pair = ':'.join(sorted([agent_a.strip().lower(), agent_b.strip().lower()]))
        entry = t.get('session_keys', {}).get(old_pair)
        if entry and entry.get('sessionKey'):
            return entry.get('sessionKey')
        # 兜底：按目标 Agent 遍历（与 _find_session_key_for_agent 一致）
        return _find_session_key_for_agent(t, agent_b)
    except Exception:
        return None


def cmd_session_keys_save(task_id, agent_a, agent_b, session_key):
    """保存一个 sessionKey 到任务的 session_keys 注册表。
    ...
    """
    if not task_id or not agent_a or not agent_b or not session_key:
        log.warning('session-keys save: 缺少必要参数 (task_id, agent_a, agent_b, session_key)')
        print('[session-keys] 用法: session-keys save {task_id} {agent_a} {agent_b} {sessionKey}', flush=True)
        return
    
    pair = _normalize_pair(agent_a, agent_b)
    
    # 🔧 修复：在 modifier 外部定义 is_update 标志
    is_update_flag = [False]  # 使用列表以便在 modifier 内部修改
    
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.warning(f'session-keys save: 任务 {task_id} 不存在')
            return tasks
        keys = t.setdefault('session_keys', {})
        is_update_flag[0] = pair in keys  # 修改外部变量
        keys[pair] = {
            'sessionKey': session_key.strip(),
            'savedAt': now_iso(),
            'agents': [agent_a.strip(), agent_b.strip()],
        }
        return tasks
    
    try:
        atomic_json_update(TASKS_FILE, modifier, [])
        is_update = is_update_flag[0]  # 从标志中获取结果
        action = '更新' if is_update else '保存'
        log.info(f'🔑 session-key {action}: {task_id} | {pair} = {session_key[:40]}...')
        print(f'[session-keys] ✅ 已{"更新" if is_update else "保存"} {pair} 的 sessionKey', flush=True)
    except Exception as e:
        log.error(f'session-keys save 失败: {e}')
        print(f'[session-keys] ❌ 保存失败: {e}', flush=True)


def cmd_session_keys_lookup(task_id, agent_a, agent_b):
    """查找任务中两个 agent 之间已保存的 sessionKey。
    
    返回格式（JSON）:
      - 找到: {"ok": true, "pair": "menxia:zhongshu", "sessionKey": "agent:xxx", "savedAt": "..."}
      - 未找到: {"ok": false, "pair": "menxia:zhongshu", "sessionKey": null}
    
    Agent 调用 sessions_send 之前应先 lookup，有 key 则用 sessions_send，没有则 sessions_spawn。
    """
    if not task_id or not agent_a or not agent_b:
        log.warning('session-keys lookup: 缺少必要参数')
        print(json.dumps({'ok': False, 'error': '缺少参数: task_id, agent_a, agent_b'}, ensure_ascii=False), flush=True)
        return
    
    pair = _normalize_pair(agent_a, agent_b)
    
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if not t:
            print(json.dumps({'ok': False, 'pair': pair, 'sessionKey': None, 'error': f'任务 {task_id} 不存在'}, ensure_ascii=False), flush=True)
            return
        
        keys = t.get('session_keys', {})
        entry = keys.get(pair)
        if entry and entry.get('sessionKey'):
            result = {
                'ok': True,
                'pair': pair,
                'sessionKey': entry['sessionKey'],
                'savedAt': entry.get('savedAt', ''),
                'agents': entry.get('agents', []),
            }
            log.info(f'🔑 session-key lookup: {task_id} | {pair} → 找到 {entry["sessionKey"][:40]}...')
            print(json.dumps(result, ensure_ascii=False), flush=True)
        else:
            log.info(f'🔑 session-key lookup: {task_id} | {pair} → 未找到')
            print(json.dumps({'ok': False, 'pair': pair, 'sessionKey': None}, ensure_ascii=False), flush=True)
    except Exception as e:
        log.error(f'session-keys lookup 失败: {e}')
        print(json.dumps({'ok': False, 'pair': pair, 'sessionKey': None, 'error': str(e)}, ensure_ascii=False), flush=True)


def cmd_session_keys_list(task_id):
    """列出任务中所有已保存的 sessionKey。
    
    返回格式（JSON）:
      {"ok": true, "task_id": "JJC-xxx", "keys": {"menxia:zhongshu": {"sessionKey": "...", ...}, ...}}
    """
    if not task_id:
        log.warning('session-keys list: 缺少 task_id')
        print(json.dumps({'ok': False, 'error': '缺少 task_id'}, ensure_ascii=False), flush=True)
        return
    
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if not t:
            print(json.dumps({'ok': False, 'error': f'任务 {task_id} 不存在'}, ensure_ascii=False), flush=True)
            return
        
        keys = t.get('session_keys', {})
        print(json.dumps({'ok': True, 'task_id': task_id, 'keys': keys, 'count': len(keys)}, ensure_ascii=False, indent=2), flush=True)
        log.info(f'🔑 session-keys list: {task_id} | 共 {len(keys)} 个 key')
    except Exception as e:
        log.error(f'session-keys list 失败: {e}')
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), flush=True)


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
    
    def modifier(tasks):
        existing = next((t for t in tasks if t.get('id') == task_id), None)
        if existing:
            if existing.get('state') in ('Done', 'Cancelled'):
                log.warning(f'⚠️ 任务 {task_id} 已完结 (state={existing["state"]})，不可覆盖')
                return tasks
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
        # 🔑 保存皇上的 chat_id（用于太子回奏）
        if huangshang_chat_id:
            task_obj['huangshang_chat_id'] = huangshang_chat_id
        tasks.insert(0, task_obj)
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()

    # 📨 通知初始状态的负责 Agent
    notify_agent_id = _resolve_agent_id(state) or _resolve_agent_id(actual_org)
    _notify_agent(
        agent_id=notify_agent_id,
        task_id=task_id,
        from_org='皇上',
        to_org=actual_org,
        title=title,
        remark=clean_remark,
        current_session_key=current_session_key,
    )

    log.info(f'✅ 创建 {task_id} | {title[:30]} | state={state}')


# ── 状态流转合法性校验 ──
# 只允许文档定义的状态路径:
# Pending→Taizi→Zhongshu→Menxia→Assigned→Doing→Review→Done
# 额外: Blocked 可双向切换, Cancelled 从任意非终态可达, Next→Doing
_VALID_TRANSITIONS = {
    'Pending':   {'Taizi', 'Cancelled'},
    'Taizi':     {'Zhongshu', 'Cancelled'},
    'Zhongshu':  {'Menxia', 'Cancelled'},
    'Menxia':    {'Assigned', 'Zhongshu', 'Cancelled'},   # 封驳可回中书
    'Assigned':  {'Doing', 'Next', 'Blocked', 'Cancelled', 'Zhongshu'},  # 尚书可退回中书
    'Next':      {'Assigned', 'Doing', 'Blocked', 'Cancelled', 'Zhongshu'},  # 也可退回中书
    # 【V7 修复】增加 Assigned 允许六部退回尚书省重派发
    'Doing':     {'Review', 'Next', 'Blocked', 'Cancelled', 'Zhongshu', 'Assigned'},  # 六部可退回中书/汇总/重派
    'Review':    {'Done', 'Menxia', 'Doing', 'Zhongshu', 'Cancelled'},  # 可打回重审/重做/退回中书
    'Blocked':   {'Doing', 'Next', 'Assigned', 'Review', 'Cancelled', 'Zhongshu'},  # 解除后回原位或退回中书
    'Done':      set(),       # 终态
    'Cancelled': set(),       # 终态
}

# 不需要通知 Agent 的状态转换集合（终态或内部状态）
_NO_NOTIFY_STATES = {'Done', 'Cancelled'}

# ═══════════════════════════════════════════════════════════════════════
# 🔒 会话去重：冷却时间 + 最大通知次数
# 防止 LLM 反复 spawn subagent 导致会话爆炸
# ═══════════════════════════════════════════════════════════════════════
_MAX_NOTIFY_PER_TASK = 16       # 单个任务最多通知（唤醒）16 次
_NOTIFY_COOLDOWN_SEC = 90       # 同一任务+同一Agent冷却时间（秒）- 同步成功后
_NOTIFY_COOLDOWN_ASYNC_SEC = 30  # 异步待确认的冷却时间（秒）- 异步路径更短，允许快速重试


def cmd_state(task_id, new_state, now_text=None):
    """更新任务状态（原子操作，含流转合法性校验 + 会话去重）"""
    old_state = [None]
    rejected = [False]
    skipped = [False]
    
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state[0] = t['state']
        # 自转换快速路径：相同状态不拒绝，直接跳过（避免 Zhongshu→Zhongshu 等噪声）
        if old_state[0] == new_state:
            log.info(f'✅ {task_id} 状态不变: {new_state}（自转换跳过）')
            skipped[0] = True
            return tasks

        # ═══════════════════════════════════════════════════════════════════════
        # 🔒 会话去重：Doing/Next 状态下，同一 activeAgent 不允许重复派发
        # ═══════════════════════════════════════════════════════════════════════
        if new_state in ('Doing', 'Next', 'Assigned'):
            current_active = t.get('activeAgent', '')
            current_org = t.get('org', '')
            # 推断目标 Agent
            target_agent = _resolve_agent_id(new_state) or _ORG_AGENT_MAP.get(current_org, '')
            if current_active and current_active == target_agent and old_state[0] == new_state:
                log.info(f'🔒 去重跳过：{task_id} 状态 {new_state} 已由 {target_agent} 处理中，不重复派发')
                skipped[0] = True
                return tasks

        allowed = _VALID_TRANSITIONS.get(old_state[0])
        if allowed is not None and new_state not in allowed:
            log.warning(f'⚠️ 非法状态转换 {task_id}: {old_state[0]} → {new_state}（允许: {allowed}）')
            rejected[0] = True
            return tasks
        t['state'] = new_state
        if new_state in STATE_ORG_MAP:
            t['org'] = STATE_ORG_MAP[new_state]
        if now_text:
            t['now'] = now_text
        t['updatedAt'] = now_iso()
        
        # ═══════════════════════════════════════════════════════════════
        # ⚠️ 不再清除 _lastNotify[target_agent]！
        # 旧逻辑：每次状态转换都清去重 → 导致去重完全失效 → 无限循环
        # 新逻辑：依赖 _notify_agent 的冷却时间去重（90秒窗口）
        # ═══════════════════════════════════════════════════════════════
        target_agent = _resolve_agent_id(new_state)

        # 🔒 记录当前活跃 Agent，用于后续去重
        if new_state in ('Doing', 'Next'):
            t['activeAgent'] = target_agent or t.get('org', '')
        elif new_state == 'Done':
            t.pop('activeAgent', None)  # 完成时清除
            # 不清除 _lastNotify：任务已完成，不会再触发通知
        elif new_state == 'Review':
            t['activeAgent'] = 'shangshu'
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    
    if rejected[0]:
        log.info(f'❌ {task_id} 状态转换被拒: {old_state[0]} → {new_state}')
    elif skipped[0]:
        log.info(f'⏭️ {task_id} 状态转换跳过（去重或自转换）: {old_state[0]} → {new_state}')
    else:
        log.info(f'✅ {task_id} 状态更新: {old_state[0]} → {new_state}')

        # 📨 状态转换成功后，通知新状态的负责 Agent
        if new_state not in _NO_NOTIFY_STATES:
            notify_agent_id = _resolve_agent_id(new_state)
            # 读取任务标题用于通知内容
            tasks = load()
            t = find_task(tasks, task_id)
            task_title = t.get('title', '') if t else ''
            old_org_label = STATE_ORG_MAP.get(old_state[0], old_state[0] or '未知')
            new_org_label = STATE_ORG_MAP.get(new_state, new_state)
            # ═══════════════════════════════════════════════════════════════
            # 【V6 关键修复】Doing/Next 状态通知修复（断链点①+②根因）
            # 
            # 根因：_STATE_AGENT_MAP 不包含 'Doing'/'Next'，
            # _resolve_agent_id('Doing') 返回空字符串，
            # 导致 _notify_agent('') 直接 return，六部永远收不到通知。
            #
            # 修复：与 server.py dispatch_for_state() 保持一致，
            # Doing/Next 状态从 task.org 字段解析 agent_id。
            # 
            # 前提：尚书省须先调 flow（设置 org='礼部'等六部名），
            # 再调 state Doing。若 flow 在 state 之后，
            # cmd_flow() 中的六部通知兜底会补发。
            # ═══════════════════════════════════════════════════════════════
            if not notify_agent_id and new_state in ('Doing', 'Next') and t:
                notify_agent_id = _ORG_AGENT_MAP.get(t.get('org', ''), '')
                if notify_agent_id:
                    new_org_label = t.get('org', new_org_label)
                    log.info(f'🔗 V6修复: {task_id} {new_state}状态从org字段解析agent={notify_agent_id} (org={t.get("org","")})')
            # ═══════════════════════════════════════════════════════════════
            # 【V7 关键修复】Doing 状态通知冷却降级（断裂点②加强）
            # 
            # 根因：_NOTIFY_COOLDOWN_SEC=90 秒的冷却期会阻止断裂点②的兜底通知。
            # 场景：尚书省用 yield 派发 → 六部收不到 → 程序45秒后兜底唤醒 → 
            # 但如果90秒内已经对同一Agent发过一次通知（比如初始化时），
            # 兜底唤醒会被冷却去重拦截，六部仍然收不到消息。
            # 
            # 修复：Doing 状态的通知使用更短的冷却期（30秒），
            # 确保兜底通知能突破冷却去重。同时清除该Agent的 _lastNotify
            # 中的 done 标记，强制允许重新通知。
            # ═══════════════════════════════════════════════════════════════
            if new_state == 'Doing' and notify_agent_id and t:
                # 清除该 Agent 的 lastNotify done 标记，允许重新通知
                try:
                    def _clear_notify_for_critical(tasks):
                        tt = find_task(tasks, task_id)
                        if tt and notify_agent_id in tt.get('_lastNotify', {}):
                            tt['_lastNotify'][notify_agent_id]['done'] = False
                            log.info(f'🔓 V7修复: {task_id} 清除 {notify_agent_id} 的通知完成标记，允许重新通知（Doing状态关键通知）')
                        return tasks
                    atomic_json_update(TASKS_FILE, _clear_notify_for_critical, [])
                except Exception as e:
                    log.warning(f'🔓 V7修复: 清除通知标记失败: {e}')
            _notify_agent(
                agent_id=notify_agent_id,
                task_id=task_id,
                from_org=old_org_label,
                to_org=new_org_label,
                title=task_title,
                remark=now_text or f"状态已变更为 {new_org_label}",
            )

        # 📨 状态回退时，不再额外通知原部门
        # 旧逻辑：封驳时同时通知中书省和门下省 → 门下省收到"退回"通知后再次处理 → 循环
        # 新逻辑：只通知新状态的负责 Agent（上方已处理），不额外通知回退源
        
        # 🪝 触发状态变更钩子（通知太子等）
        # 注意：需要在 modifier 外部获取更新后的 task
        tasks = load()
        t = find_task(tasks, task_id)
        if t:
            _fire_state_hooks(task_id, old_state[0], new_state, t)
        
        # ⏰ 进入 Doing 状态 → 启动12分钟停滞看门狗
        if new_state == 'Doing' and t:
            _start_doing_stall_watchdog(task_id, t)

        # 【V5 修复】程序级兜底：Doing 状态时，延迟检查六部是否被唤醒
        # 根因：尚书省→六部 完全依赖 LLM 层 sessions_spawn，如果尚书省用了
        # sessions_yield 或 LLM 推理失败，六部永远不会收到消息。
        # 此处作为最后一道防线：60秒后检查六部是否有活动，若无则程序级唤醒。
        if new_state == 'Doing' and t:
            _start_liubu_alive_check(task_id, t)


def cmd_flow(task_id, from_dept, to_dept, remark):
    """添加流转记录（原子操作，含去重）"""
    clean_remark = _sanitize_remark(remark)
    agent_id = _infer_agent_id_from_runtime()
    agent_label = _AGENT_LABELS.get(agent_id, agent_id)
    
    # ── 校验：拒绝「六部」泛称 ──
    is_valid_from, err_from = _validate_flow_dept(from_dept)
    if not is_valid_from:
        log.warning(f'⚠️ {task_id} 流转校验失败 (from={from_dept}): {err_from}')
        print(f'[看板] 流转被拒绝: {err_from}', flush=True)
        return
    is_valid_to, err_to = _validate_flow_dept(to_dept)
    if not is_valid_to:
        log.warning(f'⚠️ {task_id} 流转校验失败 (to={to_dept}): {err_to}')
        print(f'[看板] 流转被拒绝: {err_to}', flush=True)
        return
    
    # 🔒 流转去重检测
    DEDUP_FLOW_SEC = 60
    try:
        existing_tasks = load()
        existing_task = find_task(existing_tasks, task_id)
        if existing_task:
            for entry in reversed(existing_task.get('flow_log', [])):
                if entry.get('from') == from_dept and entry.get('to') == to_dept:
                    # ── 新增：检测是否为任务创建时自动生成的流转记录 ──
                    # 自动生成的记录特征：remark 包含 "太子转交旨意至" 或 "下旨"
                    auto_remark_patterns = ['太子转交旨意至', '下旨：', '太子整理旨意']
                    entry_remark = entry.get('remark', '')
                    is_auto_generated = any(p in entry_remark for p in auto_remark_patterns)
                    
                    if is_auto_generated:
                        # 自动生成的记录，跳过并提示
                        log.info(f'🔒 流转去重跳过：{task_id} {from_dept}→{to_dept} 已由 create 命令自动生成')
                        print(f'[看板] ⏭️ 流转记录已存在（create 自动生成），无需重复添加', flush=True)
                        return
                    
                    # 60 秒内的重复记录
                    try:
                        dt = datetime.datetime.fromisoformat((entry.get('at', '') or '').replace('Z', '+00:00'))
                        now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
                        if (now - dt).total_seconds() < DEDUP_FLOW_SEC:
                            log.info(f'🔒 流转去重跳过：{task_id} {from_dept}→{to_dept} 在 {DEDUP_FLOW_SEC}s 内已记录')
                            print(f'[看板] ⏭️ 流转记录在 {DEDUP_FLOW_SEC}s 内已存在，跳过', flush=True)
                            return
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    
    # 【V7 修复】flow_log 自环检测
    # 场景：礼部调 flow JJC-xxx 礼部 礼部 → 产生 "礼部→礼部" 的自环记录
    # 这会导致监察系统误判为正常流转（因为 LEGAL_FLOWS 包含自环对），
    # 同时导致流程日志混乱、无法正确追踪任务进展。
    if from_dept and to_dept and from_dept == to_dept:
        log.warning(f'⚠️ {task_id} 流转自环检测: {from_dept}→{to_dept}（from与to相同）')
        print(f'[看板] ⚠️ 警告：流转记录 from={from_dept} 与 to={to_dept} 相同（自环），请检查是否正确', flush=True)
        # 自环仅警告不阻止：某些内部处理场景可能需要自己给自己发消息
        # 但仍记录警告以便排查

    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        t.setdefault('flow_log', []).append({
            "at": now_iso(), "from": from_dept, "to": to_dept, "remark": clean_remark,
            "agent": agent_id, "agentLabel": agent_label,
        })
        # 同步更新 org，使看板能正确显示当前所属部门
        t['org'] = to_dept
        t['updatedAt'] = now_iso()
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'✅ {task_id} 流转记录: {from_dept} → {to_dept}')

    # ═══════════════════════════════════════════════════════════════════════
    # 【V6 关键修复】流转目标为六部时，触发程序级通知（断链点②兜底）
    #
    # 根因：尚书省可能先调 state Doing 再调 flow（SOUL.md旧顺序），
    # 导致 state 触发通知时 org 还是'尚书省'，六部收不到通知。
    #
    # 兜底：当 flow 目标是六部且当前状态为 Doing/Next 时，
    # 程序级直接唤醒目标六部 Agent。
    #
    # 去重保护：_notify_agent 内置冷却机制，
    # 若 cmd_state 已成功通知过同一 agent（90秒内），此处自动跳过。
    # ═══════════════════════════════════════════════════════════════════════
    _LIU_BU_AGENT_SET = {'libu', 'hubu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr'}
    to_agent_id = _ORG_AGENT_MAP.get(to_dept.strip(), '')
    if to_agent_id in _LIU_BU_AGENT_SET:
        try:
            _flow_tasks = load()
            _flow_task = find_task(_flow_tasks, task_id)
            if _flow_task and _flow_task.get('state') in ('Doing', 'Next'):
                log.info(f'🔗 V6流转通知: {task_id} flow目标为六部({to_dept}/{to_agent_id})，状态={_flow_task.get("state")}，触发程序级唤醒')
                _notify_agent(
                    agent_id=to_agent_id,
                    task_id=task_id,
                    from_org=from_dept,
                    to_org=to_dept,
                    title=_flow_task.get('title', ''),
                    remark=clean_remark or f'{from_dept}派发任务至{to_dept}',
                )
        except Exception as _flow_notify_err:
            log.warning(f'🔗 V6流转通知异常: {_flow_notify_err}')

    # ⚠️ 基础通知仍由 cmd_state 触发，此处仅为六部目标兜底
    # 两个路径都有 _notify_agent 冷却去重保护，不会重复唤醒


# ── 六部名称集合（用于 cmd_flow 校验：拒绝「六部」泛称）──
_LIU_BU_NAMES = {'工部', '兵部', '户部', '礼部', '刑部', '吏部', '吏部_hr'}


def _validate_flow_dept(dept_name):
    """校验流转目标部门名称是否合法。
    
    规则：
    1. 「六部」不是有效部门名称，必须使用具体部名
    2. 三省（中书省/门下省/尚书省）和太子是合法流转目标
    3. 皇上是合法流转目标（回奏场景）
    
    返回 (is_valid, error_msg)。
    """
    if not dept_name or not dept_name.strip():
        return False, '部门名称不能为空'
    dept = dept_name.strip()
    # 拒绝「六部」泛称及其变体
    if '六部' in dept:
        return False, (
            f'「六部」不是有效的部门名称，越权行为！'
            f'必须使用具体部名：工部、兵部、户部、礼部、刑部、吏部之一。'
        )
    # 检查是否为合法的流转目标
    valid_targets = {
        '皇上', '太子', '太子殿下',
        '中书省', '中书', '中书令',
        '门下省', '门下', '门下侍中',
        '尚书省', '尚书', '尚书令',
    } | _LIU_BU_NAMES
    if dept not in valid_targets:
        return False, f'「{dept}」不是已知的部门名称，请检查拼写'
    return True, ''


def cmd_done(task_id, output_path='', summary=''):
    """标记任务完成（原子操作，含状态校验 + 流程完整性校验）
    
    旨意任务（JJC-开头）必须走完整回传链：
      六部→尚书省→中书省→太子→皇上
    flow_log 中必须包含完整的回传链才能标记 Done。
    非旨意任务不受此限制。
    """
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state = t.get('state', '')
        if old_state in ('Done', 'Cancelled'):
            log.warning(f'⚠️ 任务 {task_id} 已处于终态 ({old_state})，不可重复完成')
            return tasks
        allowed = _VALID_TRANSITIONS.get(old_state, set())
        if 'Done' not in allowed and old_state not in ('Doing', 'Review'):
            log.warning(f'⚠️ 非法完成 {task_id}: {old_state} → Done（允许: {allowed}）')
            return tasks
        # ── 旨意任务流程完整性校验：必须走完整回传链 ──
        if task_id.upper().startswith('JJC-'):
            flow_log = t.get('flow_log', [])
            # 收集所有流转对的部门名称
            flow_pairs = set()
            for entry in flow_log:
                f_raw = (entry.get('from', '') or '').strip()
                t_raw = (entry.get('to', '') or '').strip()
                if f_raw and t_raw:
                    # 标准化名称：使用 _ORG_AGENT_MAP / _STATE_AGENT_MAP 转换
                    flow_pairs.add((f_raw, t_raw))
            
            # 校验完整回传链：尚书省→中书省、中书省→太子、太子→皇上
            #
            # ── BUG FIX #3: 原校验过于严格，只接受精确的部门名称匹配。
            #    实际运行中，可能出现以下合理偏差：
            #    1. 尚书省直接报告太子（跳过中书省），此时 flow_log 中有 尚书省→太子
            #       而非 中书省→太子，但信息已传达至太子，不应阻塞 Done。
            #    2. 太子只调用了 progress 而未调用 flow 来记录汇报皇上的流转。
            #    修复策略：
            #    - 接受尚书省→太子作为中书省→太子的等价替代路径
            #    - 对于太子→皇上的缺失，改为记录警告但不阻塞 Done（因为太子可能在
            #      其他渠道已向皇上汇报，且太子是皇上的直接代理，有自主汇报权限）
            has_return_to_zhongshu = any(
                f in ('尚书省', '尚书') and t in ('中书省', '中书')
                for f, t in flow_pairs
            )
            has_return_to_taizi = any(
                f in ('中书省', '中书') and t in ('太子', '太子殿下')
                for f, t in flow_pairs
            )
            # 新增：尚书省直接报告太子也算完成回奏（等价路径）
            has_shangshu_direct_to_taizi = any(
                f in ('尚书省', '尚书') and t in ('太子', '太子殿下')
                for f, t in flow_pairs
            )
            has_report_to_huangshang = any(
                f in ('太子', '太子殿下') and t in ('皇上',)
                for f, t in flow_pairs
            )
            
            missing_steps = []
            if not has_return_to_zhongshu:
                missing_steps.append('尚书省→中书省')
            # 修复：中书省→太子 或 尚书省直接→太子 均视为已回奏太子
            if not has_return_to_taizi and not has_shangshu_direct_to_taizi:
                missing_steps.append('中书省→太子（或尚书省→太子）')
            # 修复：太子→皇上缺失时仅警告，不阻塞（太子有权直接汇报皇上）
            if not has_report_to_huangshang:
                log.warning(
                    f'⚠️ 旨意任务 {task_id} 尚未记录太子→皇上的回奏流转。'
                    f'太子可能已通过其他渠道汇报皇上，本次不阻塞 Done。'
                    f'建议太子后续调用 flow 命令补录：flow {task_id} 太子 皇上 回奏皇上'
                )
            
            # ── 自动补全流转：六部有产出 + 太子已收到汇报 → 自动补全并允许完成 ──
            # 当以下两个条件同时满足时，认为任务实质上已完成：
            #   1. output 字段非空（六部有产出）
            #   2. flow_log 中存在 中书省→太子 或 尚书省→太子（太子已收到汇报）
            # 此时自动补全缺失的回传流转记录，然后允许标记 Done。
            _has_output = bool((t.get('output', '') or output_path).strip())
            _has_taizi_reported = has_return_to_taizi or has_shangshu_direct_to_taizi
            if _has_output and _has_taizi_reported and missing_steps:
                _auto_filled = []
                _now = now_iso()
                # 补全 尚书省→中书省
                if not has_return_to_zhongshu:
                    t.setdefault('flow_log', []).append({
                        'at': _now, 'from': '尚书省', 'to': '中书省',
                        'remark': '[自动补全] 尚书省汇总六部成果返回中书省',
                        'agent': 'system', 'agentLabel': '系统自动补全',
                    })
                    _auto_filled.append('尚书省→中书省')
                # 补全 中书省→太子（仅当尚书省也没有直报太子时）
                if not has_return_to_taizi and not has_shangshu_direct_to_taizi:
                    t.setdefault('flow_log', []).append({
                        'at': _now, 'from': '中书省', 'to': '太子',
                        'remark': '[自动补全] 中书省回奏太子',
                        'agent': 'system', 'agentLabel': '系统自动补全',
                    })
                    _auto_filled.append('中书省→太子')
                # 补全 太子→皇上
                if not has_report_to_huangshang:
                    t.setdefault('flow_log', []).append({
                        'at': _now, 'from': '太子', 'to': '皇上',
                        'remark': '[自动补全] 太子汇报皇上（六部已有产出且太子已收到汇报，系统自动补全回奏环节）',
                        'agent': 'system', 'agentLabel': '系统自动补全',
                    })
                    _auto_filled.append('太子→皇上')
                log.info(
                    f'🔧 旨意任务 {task_id} 自动补全流转：{"、".join(_auto_filled)} '
                    f'（条件：output 非空 + 太子已收到汇报）'
                )
                missing_steps = []

            if missing_steps:
                log.warning(
                    f'⚠️ 旨意任务 {task_id} 未完成回奏皇上，不允许标记 Done。'
                    f'缺失回传环节：{"、".join(missing_steps)}。'
                    f'完整回传链路要求：尚书省→中书省→太子→皇上'
                )
                return tasks
        t['state'] = 'Done'
        t['output'] = output_path
        t['now'] = summary or '任务已完成'
        # 同步设置 outputMeta，避免依赖 refresh_live_data.py 异步补充
        if output_path:
            p = pathlib.Path(output_path)
            if p.exists():
                ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                t['outputMeta'] = {"exists": True, "lastModified": ts}
            else:
                t['outputMeta'] = {"exists": False, "lastModified": None}
        t['updatedAt'] = now_iso()
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'✅ {task_id} 已完成')

    # ═══════════════════════════════════════════════════════════════════════
    # 🔧 FIX: Done 后程序级通知太子（修复中书省回奏太子传递错会话的根因）
    #
    # 旧代码：cmd_done 只写 state='Done'，不通知任何人。
    # 中书省 LLM 只能手动 sessions_send 给太子，但没有正确的 session_key，
    # 导致太子收到消息的位置不对（或根本收不到）。
    #
    # 修复：Done 后由程序统一通知太子，走 _notify_agent 标准路径：
    #   1. lookup 太子的 session_key（任务创建时自动保存）
    #   2. 有 key → sessions_send 精准投递到太子正确会话
    #   3. 无 key → openclaw agent 唤醒（降级到 main session）
    # 中书省不再需要手动通知太子。
    # ═══════════════════════════════════════════════════════════════════════
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if t and t.get('state') == 'Done':
            task_title = t.get('title', '')
            task_output = t.get('output', '')
            _notify_agent(
                agent_id='taizi',
                task_id=task_id,
                from_org='中书省',
                to_org='太子',
                title=task_title,
                remark=f"任务已完成，请回奏皇上。产出路径：{task_output}" if task_output else "任务已完成，请回奏皇上",
            )
            # 触发状态钩子（通知太子 + 其他注册钩子）
            _fire_state_hooks(task_id, old_state[0] if old_state[0] else 'Review', 'Done', t)
            log.info(f'📨 Done 通知已发送给太子 | {task_id}')
    except Exception as _e:
        log.warning(f'⚠️ Done 通知太子失败（不影响任务完成）: {_e}')


def cmd_block(task_id, reason):
    """标记阻塞（原子操作）"""
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state = t.get('state', '')
        if old_state in ('Done', 'Cancelled'):
            log.warning(f'⚠️ 任务 {task_id} 已处于终态 ({old_state})，不可阻塞')
            return tasks
        allowed = _VALID_TRANSITIONS.get(old_state, set())
        if allowed is not None and 'Blocked' not in allowed:
            log.warning(f'⚠️ 非法阻塞 {task_id}: {old_state} → Blocked（允许: {allowed}）')
            return tasks
        t['state'] = 'Blocked'
        t['block'] = reason
        t['updatedAt'] = now_iso()
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'🚫 {task_id} 已阻塞: {reason}')


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
    
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
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
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
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
    
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
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
        return tasks
    
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'✅ {task_id} todo [{result_info[0]}/{result_info[1]}]: {todo_id} → {status}')


# ═══════════════════════════════════════════════════════════════════════
# 📨 皇上通信命令（太子回奏专用）
#
# 用法:
#   # 获取皇上的 chat_id
#   python3 kanban_update.py huangshang-chat-id JJC-xxx
#
#   # 向皇上发送消息
#   python3 kanban_update.py huangshang-send JJC-xxx "消息内容"
# ═══════════════════════════════════════════════════════════════════════

def cmd_huangshang_chat_id(task_id):
    """获取任务的皇上 chat_id。
    
    返回格式（JSON）:
      - 找到: {"ok": true, "chat_id": "user:ou_xxx"}
      - 未找到: {"ok": false, "chat_id": null, "error": "..."}
    """
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if not t:
            result = {'ok': False, 'chat_id': None, 'error': f'任务 {task_id} 不存在'}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        
        chat_id = t.get('huangshang_chat_id')
        if chat_id:
            result = {'ok': True, 'chat_id': chat_id}
            log.info(f'📨 获取皇上chat_id: {task_id} → {chat_id}')
        else:
            result = {'ok': False, 'chat_id': None, 'error': '任务未保存皇上的 chat_id'}
            log.warning(f'📨 获取皇上chat_id: {task_id} → 未保存')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as e:
        log.error(f'huangshang-chat-id 失败: {e}')
        print(json.dumps({'ok': False, 'chat_id': None, 'error': str(e)}, ensure_ascii=False), flush=True)


def cmd_huangshang_send(task_id, message):
    """向皇上发送消息（太子回奏专用）。
    
    从任务数据中读取 huangshang_chat_id，然后使用 openclaw message 工具发送消息。
    
    返回格式（JSON）:
      - 成功: {"ok": true, "chat_id": "user:ou_xxx"}
      - 失败: {"ok": false, "error": "..."}
    """
    try:
        tasks = load()
        t = find_task(tasks, task_id)
        if not t:
            result = {'ok': False, 'error': f'任务 {task_id} 不存在'}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        
        chat_id = t.get('huangshang_chat_id')
        if not chat_id:
            result = {'ok': False, 'error': '任务未保存皇上的 chat_id，无法发送消息'}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        
        # 使用 openclaw message 工具发送消息
        # 格式: openclaw message send --target "user:ou_xxx" -m "消息内容"
        result = subprocess.run(
            ['openclaw', 'message', 'send', '--target', chat_id, '-m', message],
            capture_output=True, text=True, timeout=60,
        )
        
        if result.returncode == 0:
            log.info(f'📨 已向皇上发送消息: {task_id} → {chat_id}')
            print(json.dumps({'ok': True, 'chat_id': chat_id}, ensure_ascii=False), flush=True)
        else:
            error_msg = result.stderr or result.stdout or '未知错误'
            log.warning(f'📨 向皇上发送消息失败: {error_msg}')
            print(json.dumps({'ok': False, 'error': error_msg}, ensure_ascii=False), flush=True)
    except subprocess.TimeoutExpired:
        log.error(f'huangshang-send 超时: {task_id}')
        print(json.dumps({'ok': False, 'error': '发送超时'}, ensure_ascii=False), flush=True)
    except Exception as e:
        log.error(f'huangshang-send 失败: {e}')
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), flush=True)


_CMD_MIN_ARGS = {
    'create': 6, 'state': 3, 'flow': 5, 'done': 2, 'block': 3, 'todo': 4, 'progress': 3,
    'huangshang-send': 3, 'huangshang-chat-id': 2,
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
    elif cmd == 'state':
        cmd_state(args[1], args[2], args[3] if len(args)>3 else None)
    elif cmd == 'flow':
        cmd_flow(args[1], args[2], args[3], args[4])
    elif cmd == 'done':
        cmd_done(args[1], args[2] if len(args)>2 else '', args[3] if len(args)>3 else '')
    elif cmd == 'block':
        cmd_block(args[1], args[2])
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
    elif cmd == 'session-keys':
        if len(args) < 2:
            print('[session-keys] 子命令: save | lookup | list', flush=True)
            sys.exit(1)
        sub_cmd = args[1]
        if sub_cmd == 'save':
            if len(args) < 6:
                print('[session-keys] 用法: session-keys save {task_id} {agent_a} {agent_b} {sessionKey}', flush=True)
                sys.exit(1)
            cmd_session_keys_save(args[2], args[3], args[4], args[5])
        elif sub_cmd == 'lookup':
            if len(args) < 5:
                print('[session-keys] 用法: session-keys lookup {task_id} {agent_a} {agent_b}', flush=True)
                sys.exit(1)
            cmd_session_keys_lookup(args[2], args[3], args[4])
        elif sub_cmd == 'list':
            if len(args) < 3:
                print('[session-keys] 用法: session-keys list {task_id}', flush=True)
                sys.exit(1)
            cmd_session_keys_list(args[2])
        else:
            print(f'[session-keys] 未知子命令: {sub_cmd}（可用: save, lookup, list）', flush=True)
            sys.exit(1)
    elif cmd == 'huangshang-chat-id':
        cmd_huangshang_chat_id(args[1])
    elif cmd == 'huangshang-send':
        cmd_huangshang_send(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(1)
