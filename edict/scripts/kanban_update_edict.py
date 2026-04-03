#!/usr/bin/env python3
"""
看板任务更新工具 - Edict 兼容层

保持与旧版完全相同的 CLI 接口，内部改为调用 Edict REST API。
如果 API 不可用，降级回写 JSON 文件（过渡期保障）。

用法（与旧版 100% 兼容）:
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令
  python3 kanban_update.py state JJC-20260223-012 Menxia "规划方案已提交门下省"
  python3 kanban_update.py flow JJC-20260223-012 "中书省" "门下省" "规划方案提交审核"
  python3 kanban_update.py done JJC-20260223-012 "/path/to/output" "任务完成摘要"
  python3 kanban_update.py todo JJC-20260223-012 1 "实现API接口" in-progress
  python3 kanban_update.py progress JJC-20260223-012 "正在分析需求" "1.调研✅|2.文档🔄|3.原型"
  python3 kanban_update.py validate JJC-20260223-012  # 检查流程完整性
  python3 kanban_update.py next JJC-20260223-012  # 流转到下一阶段
"""

import json
import logging
import os
import re
import sys
import pathlib
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

# Fix #13: 跨平台文件锁（兼容 Windows）
_IS_WINDOWS = os.name == 'nt'
if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl

log = logging.getLogger('kanban')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

# Edict API 地址 — 环境变量 > 默认 localhost:8000
EDICT_API_URL = os.environ.get('EDICT_API_URL', 'http://localhost:8000')

# 是否启用 API 模式（EDICT_MODE=api | json | auto）
EDICT_MODE = os.environ.get('EDICT_MODE', 'auto').lower()

# API 重试配置
MAX_API_RETRIES = 3
API_RETRY_DELAY = 1  # 秒

# ── 文本清洗逻辑（与旧版完全一致） ──

_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}

STATE_ORG_MAP = {
    'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省', 'Assigned': '尚书省',
    # 注意：'Doing' 不写入此映射，保留原部门 org 以便省部调度面板精确匹配
    'Review': '尚书省', 'Done': '完成', 'Blocked': '阻塞',
}

# State → Edict TaskState value 映射
_STATE_TO_EDICT = {
    'Taizi': 'taizi', 'Zhongshu': 'zhongshu', 'Menxia': 'menxia',
    'Assigned': 'assigned', 'Next': 'next', 'Doing': 'doing',
    'Review': 'review', 'Done': 'done', 'Blocked': 'blocked',
    'Cancelled': 'cancelled', 'Pending': 'pending',
}

# Edict State → 中文部门名映射
_EDICT_TO_ORG = {v: k for k, v in _STATE_TO_EDICT.items()}
_EDICT_TO_ORG.update({
    'taizi': '太子', 'zhongshu': '中书省', 'menxia': '门下省',
    'assigned': '尚书省', 'next': '待办', 'doing': '执行中',
    'review': '审核', 'done': '完成', 'blocked': '阻塞',
})

# ====================== 流程完整性校验常量 ======================

# 禁止的越权流转（直接执行）
_FORBIDDEN_DIRECT_FLOWS = [
    ("中书省", "尚书省"),
    ("中书省", "礼部"),
    ("中书省", "户部"),
    ("中书省", "兵部"),
    ("中书省", "刑部"),
    ("中书省", "工部"),
    ("中书省", "吏部"),
    ("太子", "尚书省"),
    ("太子", "六部"),
]

# 允许的流转路径（白名单）
_VALID_FLOWS_MAP = {
    "皇上": ["太子"],
    "太子": ["中书省"],
    "中书省": ["门下省"],
    "门下省": ["尚书省"],
    "尚书省": ["礼部", "户部", "兵部", "刑部", "工部", "吏部", "太子"],  # 允许回路
    "礼部": ["尚书省"],
    "户部": ["尚书省"],
    "兵部": ["尚书省"],
    "刑部": ["尚书省"],
    "工部": ["尚书省"],
    "吏部": ["尚书省"],
}

# 下一阶段映射（用于 cmd_next）
_NEXT_STATE_MAP = {
    "太子": "中书省",
    "中书省": "门下省",
    "门下省": "尚书省",
    "尚书省": "礼部",  # 默认流转到礼部，可根据任务类型调整
    "礼部": "尚书省",
    "户部": "尚书省",
    "兵部": "尚书省",
    "刑部": "尚书省",
    "工部": "尚书省",
    "吏部": "尚书省",
}

# Fix #5: 任务文件路径（降级模式用）— 从项目根目录 data/ 下读取，而非 edict/scripts/ 下
_EDICT_HOME = os.environ.get('EDICT_HOME', '')
if _EDICT_HOME:
    TASKS_FILE = pathlib.Path(_EDICT_HOME) / 'data' / 'tasks_source.json'
else:
    # 向上查找项目根目录（找 data/ 目录）
    _dir = pathlib.Path(__file__).resolve()
    TASKS_FILE = _dir.parent.parent / 'data' / 'tasks_source.json'
    for _p in (_dir.parent.parent, _dir.parent.parent.parent, _dir.parent):
        if (_p / 'data').is_dir():
            TASKS_FILE = _p / 'data' / 'tasks_source.json'
            break
LOCK_FILE = TASKS_FILE.with_suffix('.json.lock')

# 全局缓存（避免重复导入）
_legacy_module = None
_api_ok = None

# ── 通用辅助函数 ──

def now_iso() -> str:
    """获取ISO格式当前时间（带时区）"""
    return datetime.now(timezone.utc).isoformat()

def find_task(tasks: List[Dict], task_id: str) -> Optional[Dict]:
    """从任务列表中查找指定ID的任务"""
    for task in tasks:
        if task.get('legacy_id') == task_id or task_id in task.get('tags', []):
            return task
    return None

# Fix #13: 跨平台文件锁实现（兼容 Windows msvcrt + Unix fcntl）
def _acquire_file_lock(lock_file: pathlib.Path, timeout: int = 5) -> Optional[Any]:
    """获取文件锁，超时返回 None。返回文件描述符或 None。"""
    start_time = time.time()
    lock_fd = None
    while time.time() - start_time < timeout:
        try:
            lock_fd = open(lock_file, 'w')
            if _IS_WINDOWS:
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                except (IOError, OSError):
                    if lock_fd:
                        lock_fd.close()
                    lock_fd = None
                    time.sleep(0.1)
                    continue
            else:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except (IOError, OSError):
            if lock_fd:
                lock_fd.close()
            time.sleep(0.1)
    return None

def _release_file_lock(lock_fd):
    """释放文件锁。"""
    if lock_fd:
        try:
            if _IS_WINDOWS:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()

def atomic_json_update(file_path: pathlib.Path, modifier, default=None):
    """原子更新JSON文件（带文件锁）"""
    if default is None:
        default = []
    
    lock_fd = _acquire_file_lock(LOCK_FILE)
    if not lock_fd:
        log.error("无法获取文件锁，可能有其他进程正在写入")
        return None
    
    try:
        # 读取现有数据
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = default
        
        # 修改数据
        new_data = modifier(data)
        
        # 原子写入：先写临时文件，再替换
        temp_path = file_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        temp_path.replace(file_path)
        
        return new_data
    except Exception as e:
        log.error(f"JSON文件更新失败: {e}")
        return None
    finally:
        _release_file_lock(lock_fd)

def _trigger_refresh():
    """触发看板刷新（兼容旧版）"""
    pass

# ── API 客户端 ──

def _api_available() -> bool:
    """检查 API 是否可用"""
    if EDICT_MODE == 'json':
        return False
    if EDICT_MODE == 'api':
        return True
    
    try:
        import urllib.request
        req = urllib.request.Request(f"{EDICT_API_URL}/health", method='GET')
        req.add_header('Accept', 'application/json')
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

def _check_api() -> bool:
    """检查 API 可用性（带缓存）"""
    global _api_ok
    if _api_ok is None:
        _api_ok = _api_available()
        if _api_ok:
            log.info('Edict API 可用，使用 API 模式')
        else:
            log.warning('Edict API 不可用，降级到 JSON 模式')
    return _api_ok

def _api_request(method: str, path: str, data: Optional[Dict] = None, retry: int = 0) -> Tuple[Optional[Dict], Optional[str]]:
    """
    发送 API 请求
    返回: (data, error_msg) - 成功时 error_msg 为 None
    """
    try:
        import urllib.request
        import urllib.error
        
        body = None
        if data:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        
        req = urllib.request.Request(
            f"{EDICT_API_URL}{path}",
            data=body,
            method=method,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 200 and resp.status < 300:
                return json.loads(resp.read()), None
            return None, f"HTTP {resp.status}"
            
    except urllib.error.URLError as e:
        error_msg = f"网络错误: {e.reason}"
    except json.JSONDecodeError as e:
        error_msg = f"JSON解析错误: {e}"
    except Exception as e:
        error_msg = f"未知错误: {e}"
    
    # 重试逻辑
    if retry < MAX_API_RETRIES:
        log.warning(f'API 调用失败 ({method} {path}): {error_msg}，{API_RETRY_DELAY}秒后重试 ({retry + 1}/{MAX_API_RETRIES})')
        time.sleep(API_RETRY_DELAY)
        return _api_request(method, path, data, retry + 1)
    
    log.warning(f'API 调用失败 ({method} {path}): {error_msg}')
    return None, error_msg

def _api_post(path: str, data: dict) -> Optional[Dict]:
    """POST 请求"""
    result, error = _api_request('POST', path, data)
    return result

def _api_put(path: str, data: dict) -> Optional[Dict]:
    """PUT 请求"""
    result, error = _api_request('PUT', path, data)
    return result

def _api_get(path: str) -> Optional[Dict]:
    """GET 请求"""
    result, error = _api_request('GET', path)
    return result

# ── 状态获取辅助函数 ──

def _get_task_current_dept(task_id: str) -> Optional[str]:
    """
    获取任务当前所在部门（API优先）
    返回: 部门名称字符串，失败返回 None
    """
    if _check_api():
        result = _api_get(f'/api/tasks/by-legacy/{task_id}')
        if result:
            # 尝试多种字段名
            org = result.get('org') or result.get('assignee_org') or result.get('department')
            if not org:
                # 通过 state 推断部门
                state = result.get('state', '')
                org = _EDICT_TO_ORG.get(state, state)
            return org
    
    # 降级模式：读取本地 JSON
    try:
        if TASKS_FILE.exists():
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
            t = find_task(tasks, task_id)
            if t:
                return t.get('org')
    except Exception as e:
        log.error(f"读取本地任务状态失败: {e}")
    
    return None

# ── 流程完整性校验核心函数 ──

def _validate_flow_integrity(task_id: str, from_dept: str, to_dept: str, skip_actual_check: bool = False) -> Tuple[bool, str]:
    """
    统一校验流转规则
    返回: (是否通过, 失败原因)
    """
    # 1. 黑名单检查：越权流转
    if (from_dept, to_dept) in _FORBIDDEN_DIRECT_FLOWS:
        reason = f'越权流转: {from_dept} → {to_dept} 被禁止'
        log.warning(f'🚨 流程违规：{reason}')
        return False, reason
    
    # 2. 白名单检查：流转顺序是否合法
    allowed_targets = _VALID_FLOWS_MAP.get(from_dept)
    
    if allowed_targets:
        if to_dept not in allowed_targets:
            reason = f'{from_dept} → {to_dept} 不在允许的流转路径中，允许的目标: {", ".join(allowed_targets)}'
            log.warning(f'🚨 流程违规：{reason}')
            return False, reason
    else:
        # 如果 from_dept 不在映射中（如新部门），默认允许
        log.info(f'⚠️ 来源部门 {from_dept} 未在流程地图中定义，默认放行')
    
    # 3. 验证 from_dept 是否与任务实际状态一致（防止客户端伪造）
    if not skip_actual_check:
        actual_dept = _get_task_current_dept(task_id)
        if actual_dept and actual_dept != from_dept:
            reason = f'任务实际在 {actual_dept}，参数声称在 {from_dept}'
            log.warning(f'🚨 流程违规：{reason}')
            return False, reason
    
    return True, ""

# ── 文本清洗 ──

def _sanitize_text(raw: str, max_len: int = 80) -> str:
    """清洗文本，去除噪音"""
    if not raw:
        return ""
    t = raw.strip()
    t = re.split(r'\n*Conversation\b', t, maxsplit=1)[0].strip()
    t = re.split(r'\n*```', t, maxsplit=1)[0].strip()
    t = re.sub(r'[/\\.~][A-Za-z0-9_\-./]+(?:\.(?:py|js|ts|json|md|sh|yaml|yml|txt|csv|html|css|log))?', '', t)
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'^(传旨|下旨)([（(][^)）]*[)）])?[：:\uff1a]\s*', '', t)
    t = re.sub(r'(message_id|session_id|chat_id|open_id|user_id|tenant_key)\s*[:=]\s*\S+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > max_len:
        t = t[:max_len] + '…'
    return t

def _sanitize_title(raw: str) -> str:
    """清洗标题"""
    return _sanitize_text(raw, 80)

def _sanitize_remark(raw: str) -> str:
    """清洗备注"""
    return _sanitize_text(raw, 120)

def _is_valid_task_title(title: str) -> Tuple[bool, str]:
    """验证任务标题是否有效"""
    t = (title or '').strip()
    if len(t) < _MIN_TITLE_LEN:
        return False, f'标题过短（{len(t)}<{_MIN_TITLE_LEN}字），疑似非旨意'
    if t.lower() in _JUNK_TITLES:
        return False, f'标题 "{t}" 不是有效旨意'
    if re.fullmatch(r'[\s?？!！.。,，…·\-—~]+', t):
        return False, '标题只有标点符号'
    if re.match(r'^[/\\~.]', t) or re.search(r'/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+', t):
        return False, '标题看起来像文件路径'
    if re.fullmatch(r'[\s\W]*', t):
        return False, '标题清洗后为空'
    return True, ''

def _infer_agent_id() -> str:
    """推断 agent ID"""
    for k in ('OPENCLAW_AGENT_ID', 'OPENCLAW_AGENT', 'AGENT_ID'):
        v = (os.environ.get(k) or '').strip()
        if v:
            return v
    cwd = str(pathlib.Path.cwd())
    m = re.search(r'workspace-([a-zA-Z0-9_\-]+)', cwd)
    if m:
        return m.group(1)
    return 'system'

# ── 降级函数 ──
def _get_legacy_module():
    """懒加载旧版模块"""
    global _legacy_module
    if _legacy_module is not None:
        return _legacy_module
    
    old_path = pathlib.Path(__file__).parent / 'kanban_update_legacy.py'
    if old_path.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location('kanban_legacy', old_path)
        _legacy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_legacy_module)
        log.debug("已加载旧版兼容模块")
        return _legacy_module
    return None

# ── 业务命令 ──

def cmd_create(task_id: str, title: str, state: str, org: str, official: str, remark: Optional[str] = None):
    """创建任务"""
    title = _sanitize_title(title)
    valid, reason = _is_valid_task_title(title)
    if not valid:
        log.warning(f'⚠️ 拒绝创建 {task_id}：{reason}')
        print(f'[看板] 拒绝创建：{reason}', flush=True)
        return
    
    if _check_api():
        edict_state = _STATE_TO_EDICT.get(state, state.lower())
        result = _api_post('/api/tasks', {
            'title': title,
            'description': remark or f'下旨：{title}',
            'priority': '中',
            'assignee_org': org,
            'creator': official,
            'tags': [task_id],
            'meta': {'legacy_id': task_id, 'legacy_state': state},
        })
        if result:
            log.info(f'✅ 创建 {task_id} → Edict {result.get("task_id", "?")} | {title[:30]}')
            return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_create(task_id, title, state, org, official, remark)
    else:
        log.error(f'无法创建任务：API 不可用且无降级模块')

def cmd_state(task_id: str, new_state: str, now_text: Optional[str] = None):
    """更新任务状态"""
    if _check_api():
        edict_state = _STATE_TO_EDICT.get(new_state, new_state.lower())
        agent = _infer_agent_id()
        result = _api_post(f'/api/tasks/by-legacy/{task_id}/transition', {
            'new_state': edict_state,
            'agent': agent,
            'reason': now_text or f'状态更新为 {new_state}',
        })
        if result:
            log.info(f'✅ {task_id} 状态更新 → {new_state}')
            return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_state(task_id, new_state, now_text)
    else:
        log.error(f'无法更新状态：API 不可用且无降级模块')

def cmd_flow(task_id: str, from_dept: str, to_dept: str, remark: str):
    """任务流转"""
    clean_remark = _sanitize_remark(remark)
    
    # 执行流程校验（启用实际部门检查）
    valid, reason = _validate_flow_integrity(task_id, from_dept, to_dept, skip_actual_check=False)
    if not valid:
        log.error(f'❌ 流程校验失败，拒绝流转 {task_id}: {reason}')
        print(f'[看板] 流转被拒绝：{reason}', flush=True)
        return
    
    # API 模式
    if _check_api():
        agent = _infer_agent_id()
        result = _api_post(f'/api/tasks/by-legacy/{task_id}/progress', {
            'agent': agent,
            'content': f'流转: {from_dept} → {to_dept} | {clean_remark}',
        })
        if result:
            log.info(f'✅ {task_id} 流转记录: {from_dept} → {to_dept}')
            # 同时更新任务状态（如果 API 支持）
            edict_state = None
            for state, org_name in _STATE_TO_EDICT.items():
                if org_name == to_dept or state == to_dept:
                    edict_state = _STATE_TO_EDICT.get(state, state.lower())
                    break
            if edict_state:
                _api_post(f'/api/tasks/by-legacy/{task_id}/transition', {
                    'new_state': edict_state,
                    'agent': agent,
                    'reason': f'流转至 {to_dept}',
                })
            return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_flow(task_id, from_dept, to_dept, remark)
    else:
        # 原生降级写入JSON
        agent_id = _infer_agent_id()
        def modifier(tasks):
            t = find_task(tasks, task_id)
            if not t:
                log.error(f'任务 {task_id} 不存在')
                return tasks
            t.setdefault('flow_log', []).append({
                "at": now_iso(), "from": from_dept, "to": to_dept, "remark": clean_remark,
                "agent": agent_id, "agentLabel": agent_id,
            })
            t['org'] = to_dept
            t['updatedAt'] = now_iso()
            return tasks
        atomic_json_update(TASKS_FILE, modifier, [])
        _trigger_refresh()
        log.info(f'✅ {task_id} 流转记录: {from_dept} → {to_dept}')

def cmd_next(task_id: str, remark: Optional[str] = None):
    """流转到下一阶段（根据预定义映射）"""
    # 获取当前部门
    current_dept = _get_task_current_dept(task_id)
    if not current_dept:
        log.error(f'无法获取任务 {task_id} 的当前部门')
        return
    
    # 确定下一部门
    next_dept = _NEXT_STATE_MAP.get(current_dept)
    if not next_dept:
        log.error(f'任务 {task_id} 当前在 {current_dept}，没有定义下一阶段')
        return
    
    # 执行流转
    cmd_flow(task_id, current_dept, next_dept, remark or f'自动流转至 {next_dept}')

def cmd_done(task_id: str, output_path: str = '', summary: str = ''):
    """完成任务"""
    if _check_api():
        agent = _infer_agent_id()
        result = _api_post(f'/api/tasks/by-legacy/{task_id}/transition', {
            'new_state': 'done',
            'agent': agent,
            'reason': summary or '任务已完成',
        })
        if result:
            log.info(f'✅ {task_id} 已完成')
            return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_done(task_id, output_path, summary)

def cmd_block(task_id: str, reason: str):
    """阻塞任务"""
    if _check_api():
        agent = _infer_agent_id()
        result = _api_post(f'/api/tasks/by-legacy/{task_id}/transition', {
            'new_state': 'blocked',
            'agent': agent,
            'reason': reason,
        })
        if result:
            log.warning(f'⚠️ {task_id} 已阻塞: {reason}')
            return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_block(task_id, reason)

def cmd_progress(task_id: str, now_text: str, todos_pipe: str = '', tokens: int = 0, cost: float = 0.0, elapsed: int = 0):
    """记录进展"""
    clean = _sanitize_remark(now_text)
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
    
    if _check_api():
        agent = _infer_agent_id()
        _api_post(f'/api/tasks/by-legacy/{task_id}/progress', {
            'agent': agent,
            'content': clean,
        })
        if parsed_todos:
            _api_put(f'/api/tasks/by-legacy/{task_id}/todos', {
                'todos': parsed_todos,
            })
        log.info(f'📡 {task_id} 进展: {clean[:40]}...')
        return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_progress(task_id, now_text, todos_pipe, tokens, cost, elapsed)

def cmd_todo(task_id: str, todo_id: str, title: str, status: str = 'not-started', detail: str = ''):
    """更新 TODO 项"""
    if status not in ('not-started', 'in-progress', 'completed'):
        status = 'not-started'
    
    if _check_api():
        agent = _infer_agent_id()
        _api_post(f'/api/tasks/by-legacy/{task_id}/progress', {
            'agent': agent,
            'content': f'Todo #{todo_id}: {title} → {status}',
        })
        log.info(f'✅ {task_id} todo: {todo_id} → {status}')
        return
    
    # 降级模式
    legacy = _get_legacy_module()
    if legacy:
        legacy.cmd_todo(task_id, todo_id, title, status, detail)

def cmd_validate(task_id: str):
    """检查任务流程完整性（API 模式 + 降级模式均支持）"""
    log.info(f'🔍 开始检查 {task_id} 流程完整性')
    
    # API 模式：调用服务端校验接口
    if _check_api():
        result = _api_get(f'/api/tasks/by-legacy/{task_id}/validate')
        if result:
            is_valid = result.get('valid', False)
            issues = result.get('issues', [])
            if is_valid:
                log.info(f'✅ {task_id} 流程完整性检查通过（服务端）')
            else:
                log.warning(f'🚨 {task_id} 流程违规: {", ".join(issues)}')
                # 尝试在服务端修复或标记阻塞
                if issues:
                    _api_post(f'/api/tasks/by-legacy/{task_id}/block', {
                        'reason': f'流程问题: {", ".join(issues[:3])}',
                        'agent': _infer_agent_id(),
                    })
            return
        else:
            log.warning('API 校验接口不可用，尝试降级到本地校验')
    
    # 降级模式：本地校验
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        
        flow_log = t.get('flow_log', [])
        issues = []
        
        # 检查越权流转
        for flow in flow_log:
            f_from = flow.get('from', '')
            f_to = flow.get('to', '')
            if (f_from, f_to) in _FORBIDDEN_DIRECT_FLOWS:
                issues.append(f'越权流转: {f_from}→{f_to}')
        
        # 检查流转顺序
        for i, flow in enumerate(flow_log):
            if i == 0:
                continue
            prev_to = flow_log[i-1].get('to', '')
            curr_from = flow.get('from', '')
            if prev_to != curr_from:
                issues.append(f'流转断裂: {prev_to} → {curr_from}')
        
        # 检查结果
        if issues:
            log.warning(f'🚨 {task_id} 流程违规: {", ".join(issues)}')
            t['block'] = f'流程问题: {", ".join(issues[:3])}'
            t['state'] = 'blocked'
        else:
            log.info(f'✅ {task_id} 流程完整性检查通过')
            t['block'] = '无'
        
        t['updatedAt'] = now_iso()
        return tasks
    
    result = atomic_json_update(TASKS_FILE, modifier, [])
    if result is None:
        log.error(f'校验 {task_id} 失败：无法更新任务文件')
    else:
        _trigger_refresh()

# ── CLI 分发 ──
_CMD_MIN_ARGS = {
    'create': 6, 'state': 3, 'flow': 5, 'done': 2, 'block': 3,
    'todo': 4, 'progress': 3, 'validate': 2, 'next': 2
}

def parse_todo_args(args: List[str]) -> Tuple[str, str, str, str, str]:
    """解析 todo 命令参数"""
    task_id = ''
    todo_id = ''
    title = ''
    status = 'not-started'
    detail = ''
    
    i = 0
    while i < len(args):
        if args[i] == '--detail' and i + 1 < len(args):
            detail = args[i + 1]
            i += 2
        elif not task_id:
            task_id = args[i]
            i += 1
        elif not todo_id:
            todo_id = args[i]
            i += 1
        elif not title:
            title = args[i]
            i += 1
        elif not status or status not in ('not-started', 'in-progress', 'completed'):
            if args[i] in ('not-started', 'in-progress', 'completed'):
                status = args[i]
            else:
                title = f"{title} {args[i]}" if title else args[i]
            i += 1
        else:
            i += 1
    
    return task_id, todo_id, title, status, detail

def parse_progress_args(args: List[str]) -> Tuple[str, str, str, int, float, int]:
    """解析 progress 命令参数"""
    task_id = ''
    now_text = ''
    todos_pipe = ''
    tokens = 0
    cost = 0.0
    elapsed = 0
    
    i = 0
    while i < len(args):
        if args[i] == '--tokens' and i + 1 < len(args):
            try:
                tokens = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == '--cost' and i + 1 < len(args):
            try:
                cost = float(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == '--elapsed' and i + 1 < len(args):
            try:
                elapsed = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif not task_id:
            task_id = args[i]
            i += 1
        elif not now_text:
            now_text = args[i]
            i += 1
        else:
            todos_pipe = args[i]
            i += 1
    
    return task_id, now_text, todos_pipe, tokens, cost, elapsed

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
        cmd_create(args[1], args[2], args[3], args[4], args[5], args[6] if len(args) > 6 else None)
    elif cmd == 'state':
        cmd_state(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == 'flow':
        cmd_flow(args[1], args[2], args[3], args[4])
    elif cmd == 'done':
        cmd_done(args[1], args[2] if len(args) > 2 else '', args[3] if len(args) > 3 else '')
    elif cmd == 'block':
        cmd_block(args[1], args[2])
    elif cmd == 'next':
        cmd_next(args[1], args[2] if len(args) > 2 else None)
    elif cmd == 'todo':
        task_id, todo_id, title, status, detail = parse_todo_args(args[1:])
        if not task_id or not todo_id or not title:
            print('错误：todo 命令需要 task_id, todo_id, title 参数')
            sys.exit(1)
        cmd_todo(task_id, todo_id, title, status, detail)
    elif cmd == 'progress':
        task_id, now_text, todos_pipe, tokens, cost, elapsed = parse_progress_args(args[1:])
        if not task_id or not now_text:
            print('错误：progress 命令需要 task_id 和 now_text 参数')
            sys.exit(1)
        cmd_progress(task_id, now_text, todos_pipe, tokens, cost, elapsed)
    elif cmd == 'validate':
        cmd_validate(args[1])
    else:
        print(f'未知命令: {cmd}')
        print(__doc__)
        sys.exit(1)
