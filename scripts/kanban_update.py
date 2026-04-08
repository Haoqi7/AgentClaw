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
"""
import datetime
import json, pathlib, sys, subprocess, logging, os, re

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
        """降级更新：使用文件锁"""
        lock_path = path.with_suffix(path.suffix + '.lock')
        lock_f = open(lock_path, 'a')  # 'a' 模式，不会清空文件
        try:
            _acquire_lock(lock_f, exclusive=True)
            try:
                if path.exists():
                    with open(path, 'r') as f:
                        data = json.load(f)
                else:
                    data = default
                new_data = modifier(data)
                with open(path, 'w') as f:
                    json.dump(new_data, f, indent=2, ensure_ascii=False)
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
    'Assigned': 'shangshu',
    'Review': 'shangshu',
    'Pending': 'zhongshu',
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
            '1. 立即回复确认收到任务\n'
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
            '1. 立即回复确认收到任务\n'
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
            '1. 立即回复确认收到任务\n'
            '2. 按要求撰写文档/UI文案/对外沟通材料\n'
            '3. 更新看板进展，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，礼部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'hubu': {
        'role_hint': '你是户部，负责数据分析、统计、资源管理与成本分析',
        'action_items': (
            '1. 立即回复确认收到任务\n'
            '2. 按要求进行数据收集/清洗/统计/可视化\n'
            '3. 产出必附量化指标或统计摘要，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，户部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'bingbu': {
        'role_hint': '你是兵部，负责工程实现、架构设计与功能开发',
        'action_items': (
            '1. 立即回复确认收到任务\n'
            '2. 按要求进行需求分析/方案设计/代码实现\n'
            '3. 确保代码可运行，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，兵部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'xingbu': {
        'role_hint': '你是刑部，负责质量保障、测试验收与合规审计',
        'action_items': (
            '1. 立即回复确认收到任务\n'
            '2. 按要求进行代码审查/测试/合规审计\n'
            '3. 产出必附测试结果或审计清单，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，刑部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'gongbu': {
        'role_hint': '你是工部，负责基础设施、部署运维与性能监控',
        'action_items': (
            '1. 立即回复确认收到任务\n'
            '2. 按要求进行部署/运维/监控\n'
            '3. 产出必附回滚方案，完成后上报尚书省'
        ),
        'confirm_fmt': '已收到 {task_id} {title}，工部开始执行',
        'deadline': '5分钟内确认并开始执行',
    },
    'libu_hr': {
        'role_hint': '你是吏部，负责人事管理、Agent管理与能力培训',
        'action_items': (
            '1. 立即回复确认收到任务\n'
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
        subprocess.Popen(
            ["openclaw", "agent", "--agent", {agent_id_escaped}, "-m", msg, "--timeout", "120"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(f"[stall-watchdog] {task_id_escaped} 已催办 {agent_label_escaped}", flush=True)
except Exception as e:
    print(f"[stall-watchdog] {task_id_escaped} 检查失败: {{e}}", flush=True)
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


def _notify_agent(agent_id, task_id, from_org, to_org, title='', remark='', _retry=0):
    """异步通知目标 Agent 有新任务/流转（针对性增强版：部门差异化通知+唤醒重试+确认回执）。

    - 使用 openclaw sessions spawn 触发目标 Agent 会话。
    - 非阻塞：通过 Popen 异步执行，不延迟主流程。
    - 容错：通知失败仅记录日志，不影响看板写入结果。
    - 🎯 针对性通知：根据 _AGENT_NOTIFY_PROFILES 为每个部门生成专属通知内容，
      包含该部门的核心职责提醒和具体执行步骤指导。
    - 唤醒重试：首次失败后 3 秒重试一次。
    - 🔒 会话去重：同一任务 + 同一目标 Agent 在短时间内不重复唤醒。
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
    if action_items:
        parts.append(f"")
        parts.append(f"🚀 你需要做的：")
        parts.append(action_items)
    parts.append(f"")
    parts.append(f"⚠️ 【交接协议 - 强制执行】")
    parts.append(f"收到此消息后，你必须做的第一件事：")
    parts.append(f"  立即回复确认：「{confirm_fmt}」")
    parts.append(f"  要求：{deadline}")
    parts.append(f"")
    parts.append(f"请立即处理！")

    message = '\n'.join(parts)

    try:
        proc = subprocess.Popen(
            ['openclaw', 'agent', '--agent', agent_id, '-m', message, '--timeout', '120'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info(f'📨 已发送【针对性】通知给 {to_label} ({agent_id}) | 任务 {task_id}')
    except Exception as e:
        log.warning(f'⚠️ 通知 {to_label} ({agent_id}) 失败 (第{_retry+1}次): {e}')
        if _retry < 1:
            import time as _time
            _time.sleep(3)
            _notify_agent(agent_id, task_id, from_org, to_org, title, remark, _retry=_retry + 1)
        return

    # ═══════════════════════════════════════════════════════════════════════
    # 🔒 会话去重：记录本次通知（标记 done=true，后续不再重复通知）
    # ═══════════════════════════════════════════════════════════════════════
    def _record_notify(tasks):
        t = find_task(tasks, task_id)
        if not t:
            return tasks
        t.setdefault('_lastNotify', {})[agent_id] = {'at': now_iso(), 'remark': remark[:60] if remark else '', 'done': True}
        return tasks
    try:
        atomic_json_update(TASKS_FILE, _record_notify, [])
    except Exception:
        pass  # 去重记录写入失败不影响主流程


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


def cmd_create(task_id, title, state, org, official, remark=None):
    """新建任务（收旨时立即调用）"""
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
        tasks.insert(0, {
            "id": task_id, "title": title, "official": official,
            "org": actual_org, "state": state,
            "now": clean_remark[:60] if remark else f"已下旨，等待{actual_org}接旨",
            "eta": "-", "block": "无", "output": "", "ac": "",
            "flow_log": init_flow,
            "updatedAt": now_iso()
        })
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
    'Doing':     {'Review', 'Blocked', 'Cancelled', 'Zhongshu'},  # 六部可退回中书
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
_NOTIFY_COOLDOWN_SEC = 90       # 同一任务+同一Agent冷却时间（秒）


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
    
    # 🔒 流转去重：同一任务相同 from→to 在 60 秒内不重复记录
    DEDUP_FLOW_SEC = 60
    try:
        existing_tasks = load()
        existing_task = find_task(existing_tasks, task_id)
        if existing_task:
            for entry in reversed(existing_task.get('flow_log', [])):
                if entry.get('from') == from_dept and entry.get('to') == to_dept:
                    try:
                        dt = datetime.datetime.fromisoformat((entry.get('at', '') or '').replace('Z', '+00:00'))
                        now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
                        if (now - dt).total_seconds() < DEDUP_FLOW_SEC:
                            log.info(f'🔒 流转去重跳过：{task_id} {from_dept}→{to_dept} 在 {DEDUP_FLOW_SEC}s 内已记录')
                            return
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    
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

    # ⚠️ 不再在此处调用 _notify_agent
    # 旧逻辑：cmd_flow 和 cmd_state 双重通知 → 导致无限循环
    # 新逻辑：通知统一由 cmd_state 触发，cmd_flow 只记录流转日志


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
            has_return_to_zhongshu = any(
                f in ('尚书省', '尚书') and t in ('中书省', '中书')
                for f, t in flow_pairs
            )
            has_return_to_taizi = any(
                f in ('中书省', '中书') and t in ('太子', '太子殿下')
                for f, t in flow_pairs
            )
            has_report_to_huangshang = any(
                f in ('太子', '太子殿下') and t in ('皇上',)
                for f, t in flow_pairs
            )
            
            missing_steps = []
            if not has_return_to_zhongshu:
                missing_steps.append('尚书省→中书省')
            if not has_return_to_taizi:
                missing_steps.append('中书省→太子')
            if not has_report_to_huangshang:
                missing_steps.append('太子→皇上')
            
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


_CMD_MIN_ARGS = {
    'create': 6, 'state': 3, 'flow': 5, 'done': 2, 'block': 3, 'todo': 4, 'progress': 3,
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
        cmd_create(args[1], args[2], args[3], args[4], args[5], args[6] if len(args)>6 else None)
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
    else:
        print(__doc__)
        sys.exit(1)
