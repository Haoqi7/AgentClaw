#!/usr/bin/env python3
"""
三省六部 · 看板本地 API 服务器
Port: 7891 (可通过 --port 修改)

Endpoints:
  GET  /                       → dashboard.html
  GET  /api/live-status        → data/live_status.json
  GET  /api/agent-config       → data/agent_config.json
  POST /api/set-model          → {agentId, model}
  GET  /api/model-change-log   → data/model_change_log.json
  GET  /api/last-result        → data/last_model_change_result.json
"""
import json, pathlib, subprocess, sys, threading, argparse, datetime, logging, re, os, socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import signal, time as _time
from urllib.parse import urlparse, quote as _url_quote, unquote
from urllib.request import Request, urlopen

# 引入文件锁工具，确保与其他脚本并发安全
scripts_dir = str(pathlib.Path(__file__).parent.parent / 'scripts')
sys.path.insert(0, scripts_dir)
from file_lock import atomic_json_read, atomic_json_write, atomic_json_update
from utils import validate_url, read_json, now_iso
from court_discuss import (
    create_session as cd_create, advance_discussion as cd_advance,
    get_session as cd_get, conclude_session as cd_conclude,
    list_sessions as cd_list, destroy_session as cd_destroy,
    get_fate_event as cd_fate, OFFICIAL_PROFILES as CD_PROFILES,
)
import shutil as _shutil
import cgi as _cgi
import io as _io

log = logging.getLogger('server')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

CHANNELS_DIR = pathlib.Path(__file__).parent / 'channels'
# 修复：channels 模块为可选依赖，导入失败时降级为空实现，避免服务崩溃
NOTIFICATION_CHANNELS = {}
def get_channel(channel_type):
    """获取渠道通知类（降级实现：无 channels 模块时返回 None）"""
    return None
def get_channel_info():
    """获取渠道信息（降级实现）"""
    return []

if CHANNELS_DIR.is_dir() and str(CHANNELS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(CHANNELS_DIR.parent))
try:
    from channels import get_channel as _get_channel, get_channel_info as _get_channel_info, CHANNELS as _CHANNELS
    get_channel = _get_channel
    get_channel_info = _get_channel_info
    NOTIFICATION_CHANNELS = _CHANNELS
except ImportError:
    log.warning('⚠️ channels 模块未找到（dashboard/channels/），多渠道通知功能不可用')

OCLAW_HOME = pathlib.Path.home() / '.openclaw'
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MB
ALLOWED_ORIGIN = None  # Set via --cors; None means restrict to localhost
_DASHBOARD_PORT = 7891  # Updated at startup from --port arg
_DEFAULT_ORIGINS = {
    'http://127.0.0.1:7891', 'http://localhost:7891',
    'http://127.0.0.1:5173', 'http://localhost:5173',  # Vite dev server
}

# ── Fix Docker 部署：外部可访问 URL 配置 ──
# 优先级: EDICT_EXTERNAL_URL 环境变量 > 从请求头 Host 推断 > 默认值
_EDICT_EXTERNAL_URL = None
_ext_url_env = os.environ.get('EDICT_EXTERNAL_URL', '').strip()
if _ext_url_env:
    _EDICT_EXTERNAL_URL = _ext_url_env.rstrip('/')

# ── Fix #6: CORS 配置支持 Docker 部署的自定义端口 ──
# 通过环境变量 EDICT_CORS_ORIGINS 添加额外允许的 Origin（逗号分隔）
# 例如: EDICT_CORS_ORIGINS=http://myhost:8080,http://192.168.1.100:3000
_EXTRA_CORS_ORIGINS = set()
_cors_env = os.environ.get('EDICT_CORS_ORIGINS', '').strip()
if _cors_env:
    for _origin in _cors_env.split(','):
        _origin = _origin.strip().rstrip('/')
        if _origin:
            _EXTRA_CORS_ORIGINS.add(_origin)
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')

BASE = pathlib.Path(__file__).parent
DIST = BASE / 'dist'          # React 构建产物 (npm run build)
DATA = BASE.parent / "data"
SCRIPTS = BASE.parent / 'scripts'
_ACTIVE_TASK_DATA_DIR = None

# 静态资源 MIME 类型
_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':  'font/ttf',
    '.map':  'application/json',
}


def _infer_external_base(handler):
    """从请求头推断外部可访问的基础 URL（scheme://host:port）。

    优先使用 EDICT_EXTERNAL_URL 环境变量，否则从 Host 头推断。
    返回值形如 'http://192.168.1.100:7891'（不含尾部斜杠）。
    """
    # 1. 环境变量显式配置（最高优先级）
    if _EDICT_EXTERNAL_URL:
        return _EDICT_EXTERNAL_URL
    # 2. 从 Host 头推断
    host_hdr = handler.headers.get('Host', '').strip()
    if host_hdr:
        # Host 头可能是 'hostname:port' 或仅 'hostname' 或 '[ipv6]:port'
        return f'http://{host_hdr}'
    # 3. 兜底默认
    return f'http://127.0.0.1:{_DASHBOARD_PORT}'


def _get_external_gateway_url(handler):
    """获取浏览器可直接访问的 Gateway 外部 URL。

    策略（方案 C 混合）:
    1. EDICT_EXTERNAL_GATEWAY_URL 环境变量（显式 Gateway 外部地址）
    2. 基于请求 Host 头 + Gateway 端口偏移推断
    3. EDICT_EXTERNAL_URL + Gateway 端口偏移
    4. 兜底回退到 _get_gateway_base_url()（容器内部地址）
    """
    # 1. 显式 Gateway 外部地址
    env_gw_ext = os.environ.get('EDICT_EXTERNAL_GATEWAY_URL', '').strip()
    if env_gw_ext:
        return env_gw_ext.rstrip('/')
    # 2/3. 从外部基础 URL 推断（替换端口号为 Gateway 端口）
    try:
        internal_gw = _get_gateway_base_url()
        parsed = urlparse(internal_gw)
        gw_port = parsed.port or 18789
        external_base = _infer_external_base(handler)
        ext_parsed = urlparse(external_base)
        ext_host = ext_parsed.hostname or ''
        # 只要有有效的 host，就用它构造 Gateway URL（移除 localhost/127.0.0.1 限制）
        if ext_host:
            return f'http://{ext_host}:{gw_port}'
    except Exception:
        pass
    # 4. 兜底
    return _get_gateway_base_url()


def _get_external_dashboard_url(handler=None):
    """获取浏览器可直接访问的 Dashboard 外部 URL。

    用于通知推送链接等场景。
    """
    if handler:
        return _infer_external_base(handler)
    if _EDICT_EXTERNAL_URL:
        return _EDICT_EXTERNAL_URL
    return f'http://127.0.0.1:{_DASHBOARD_PORT}'


def cors_headers(h):
    req_origin = h.headers.get('Origin', '')
    # Fix #6: 将 EDICT_EXTERNAL_URL 对应的 origin 也加入白名单
    ext_origin = None
    if _EDICT_EXTERNAL_URL:
        ext_origin = _EDICT_EXTERNAL_URL
    if ALLOWED_ORIGIN:
        origin = ALLOWED_ORIGIN
    elif req_origin in _DEFAULT_ORIGINS or req_origin in _EXTRA_CORS_ORIGINS:
        origin = req_origin
    elif ext_origin and req_origin == ext_origin:
        origin = req_origin
    else:
        # Fix Docker 部署：动态匹配 Origin 与 Host 头
        # 当用户通过外部 IP（如 http://192.168.1.100:7891）访问时，
        # Origin 为 http://192.168.1.100:7891，Host 为 192.168.1.100:7891
        # 此时应该放行（Origin 和 Host 指向同一服务）
        host_hdr = h.headers.get('Host', '').strip()
        if host_hdr and req_origin:
            # 构造期望的 origin：支持 http 和 https（反向代理场景）
            for scheme in ('http://', 'https://'):
                expected = f'{scheme}{host_hdr}'
                if req_origin == expected:
                    origin = req_origin
                    break
            else:
                # 代理端口匹配：Origin hostname == Host hostname（不同端口，同一机器）
                origin_host = ''
                try:
                    _m = re.match(r'https?://([^:/]+)', req_origin)
                    if _m:
                        origin_host = _m.group(1)
                except Exception:
                    pass
                host_host = host_hdr.split(':')[0] if ':' in host_hdr else host_hdr
                if origin_host and host_host and origin_host == host_host:
                    origin = req_origin
                else:
                    # Origin 与 Host 不匹配，拒绝
                    return
        else:
            return
    h.send_header('Access-Control-Allow-Origin', origin)
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')


# Issue #6#5: API Token 认证（复用 Gateway auth.token 保护 POST 端点）
_API_TOKEN = None

def _load_api_token():
    """从 openclaw.json 加载 Gateway auth.token 作为看板 API 认证凭证"""
    global _API_TOKEN
    if _API_TOKEN is not None:
        return _API_TOKEN
    try:
        cfg = json.loads(OCLAW_HOME.joinpath('openclaw.json').read_text())
        _API_TOKEN = cfg.get('gateway', {}).get('auth', {}).get('token', '')
    except Exception:
        _API_TOKEN = ''
    return _API_TOKEN


def _get_gateway_base_url():
    """获取 Gateway 基础 URL（支持环境变量/配置文件覆盖，默认 127.0.0.1:18789）。
    
    优先级: 环境变量 EDICT_GATEWAY_URL > openclaw.json 配置 > 默认值
    
    Fix #3: 对 0.0.0.0 进行归一化处理，避免拼接出不可连接的 URL。
    """
    # 优先使用环境变量
    env_url = os.environ.get('EDICT_GATEWAY_URL', '').strip()
    if env_url:
        return env_url.rstrip('/')
    # 从 openclaw.json 读取
    try:
        cfg = json.loads(OCLAW_HOME.joinpath('openclaw.json').read_text())
        gw = cfg.get('gateway', {})
        # 支持 gateway.url 或 gateway.host + gateway.port
        url = gw.get('url', '').strip()
        if url:
            return url.rstrip('/')
        host = gw.get('host', '127.0.0.1')
        port = gw.get('port', 18789)
        # Fix #3: 0.0.0.0 / [::] / :: 归一化为 127.0.0.1（与 pipeline_watchdog.py 保持一致）
        if host in ('0.0.0.0', '::', '[::]', '::1', '[::1]'):
            host = '127.0.0.1'
        if host and port:
            return f'http://{host}:{port}'
    except Exception:
        pass
    return 'http://127.0.0.1:18789'


def _get_gateway_token():
    """获取 Gateway API token（从 openclaw.json 读取）"""
    try:
        cfg = json.loads(OCLAW_HOME.joinpath('openclaw.json').read_text())
        return cfg.get('gateway', {}).get('auth', {}).get('token', '')
    except Exception:
        return ''


def _check_api_auth(handler):
    """校验请求中的 Authorization token。无 token 配置时跳过认证。

    同源请求（前端由本看板服务提供）自动放行，无需携带 token。
    外部请求（如 curl / 第三方集成）需要 Bearer token。
    支持反向代理场景：Origin/Host 端口与服务器端口不同时自动放行。
    """
    token = _load_api_token()
    if not token:
        return True  # 未配置 token，跳过认证
    # 同源放行：前端由本服务提供，Origin 匹配则信任
    origin = handler.headers.get('Origin', '')
    referer = handler.headers.get('Referer', '')
    host = handler.headers.get('Host', '')
    server_port = getattr(_check_api_auth, '_port', 7891)  # main() 设置

    # 1. 直接访问：Origin/Host 包含服务器端口
    _port_pat = re.compile(rf':({server_port})(/|$)')
    for src in (origin, referer):
        if src and _port_pat.search(src):
            return True
    if host:
        if _port_pat.search(host):
            return True
    # 2. 反向代理场景：Origin hostname == Host hostname（端口可能不同）
    # 例如：Origin http://10.147.20.138:7892，Host 10.147.20.138:7892
    if origin and host:
        origin_host = ''
        try:
            _m = re.match(r'https?://([^:/]+)', origin)
            if _m:
                origin_host = _m.group(1)
        except Exception:
            pass
        host_host = host.split(':')[0] if ':' in host else host
        if origin_host and host_host and origin_host == host_host:
            return True
    # 3. 兼容旧逻辑：无 Origin 且 Host 为 localhost
    if not origin and host and (host == f'127.0.0.1:{server_port}' or host == f'localhost:{server_port}'):
        return True
    # 4. Bearer token 兜底
    auth_header = handler.headers.get('Authorization', '')
    if auth_header == f'Bearer {token}':
        return True
    return False


def _iter_task_data_dirs():
    """返回可用的任务数据目录候选（优先 workspace，其次本地 data）。"""
    dirs = [DATA]
    for p in sorted(OCLAW_HOME.glob('workspace-*/data')):
        if p.is_dir():
            dirs.append(p)
    return dirs


def _task_source_score(task_file: pathlib.Path):
    """给任务源打分：优先非 demo 任务，其次任务数，再按文件更新时间。"""
    try:
        tasks = atomic_json_read(task_file, [])
    except Exception:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []
    non_demo = sum(1 for t in tasks if str((t or {}).get('id', '')) and not str((t or {}).get('id', '')).startswith('JJC-DEMO'))
    try:
        mtime = task_file.stat().st_mtime
    except Exception:
        mtime = 0
    return (1 if non_demo > 0 else 0, non_demo, len(tasks), mtime)


_TASK_DIR_CACHE_TS = 0
_TASK_DIR_CACHE_TTL = 300  # 5 分钟缓存过期（Issue #6#3）

def get_task_data_dir():
    """自动选择当前任务数据目录，并缓存结果以保持一次服务期内稳定。"""
    global _ACTIVE_TASK_DATA_DIR, _TASK_DIR_CACHE_TS
    import time as _time
    # Issue #6#3: 缓存超过 TTL 时自动刷新
    if _ACTIVE_TASK_DATA_DIR and _ACTIVE_TASK_DATA_DIR.is_dir():
        if _time.time() - _TASK_DIR_CACHE_TS < _TASK_DIR_CACHE_TTL:
            return _ACTIVE_TASK_DATA_DIR
        log.info('任务数据目录缓存已过期，重新评估最佳数据源')
    best_dir = DATA
    best_score = (-1, -1, -1, -1)
    for d in _iter_task_data_dirs():
        tf = d / 'tasks_source.json'
        if not tf.exists():
            continue
        score = _task_source_score(tf)
        if score > best_score:
            best_score = score
            best_dir = d
    _ACTIVE_TASK_DATA_DIR = best_dir
    _TASK_DIR_CACHE_TS = _time.time()
    log.info(f'任务数据源: {_ACTIVE_TASK_DATA_DIR}')
    return _ACTIVE_TASK_DATA_DIR


def load_tasks():
    task_data_dir = get_task_data_dir()
    return atomic_json_read(task_data_dir / 'tasks_source.json', [])


# ── 防抖刷新 live_status（3秒内多次 save_tasks 只触发一次刷新）──
_refresh_timer = None
_refresh_lock = threading.Lock()
_REFRESH_DEBOUNCE_SEC = 3


def _do_refresh_live(script):
    """实际执行 live_status 刷新。"""
    try:
        subprocess.run(['python3', str(script)], timeout=30)
    except Exception as e:
        log.warning(f'refresh_live_data.py 触发失败: {e}')


def save_tasks(tasks):
    task_data_dir = get_task_data_dir()
    atomic_json_write(task_data_dir / 'tasks_source.json', tasks)
    # 防抖刷新：3秒内多次 save_tasks 只触发最后一次
    global _refresh_timer
    script = task_data_dir.parent / 'scripts' / 'refresh_live_data.py'
    if not script.exists():
        script = SCRIPTS / 'refresh_live_data.py'

    def _schedule_refresh():
        global _refresh_timer
        with _refresh_lock:
            if _refresh_timer is not None:
                _refresh_timer.cancel()
            _refresh_timer = threading.Timer(_REFRESH_DEBOUNCE_SEC, _do_refresh_live, args=(script,))
            _refresh_timer.daemon = True
            _refresh_timer.start()

    _schedule_refresh()


def handle_task_action(task_id, action, reason):
    """Stop/cancel/resume a task from the dashboard."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    old_state = task.get('state', '')
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'task-action-before-{action}')

    if action == 'stop':
        task['state'] = 'Blocked'
        task['block'] = reason or '皇上叫停'
        task['now'] = f'⏸️ 已暂停：{reason}'
    elif action == 'cancel':
        task['state'] = 'Cancelled'
        task['block'] = reason or '皇上取消'
        task['now'] = f'🚫 已取消：{reason}'
    elif action == 'resume':
        # Resume to previous active state or Doing
        task['state'] = task.get('_prev_state', 'Doing')
        task['block'] = '无'
        task['now'] = f'▶️ 已恢复执行'

    if action in ('stop', 'cancel'):
        task['_prev_state'] = old_state  # Save for resume

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '太子',
        'to': task.get('org', ''),
        'remark': f'{"⏸️ 叫停" if action == "stop" else "🚫 取消" if action == "cancel" else "▶️ 恢复"}：{reason}'
    })
    # Issue #3: 标记为太子手动操作，监察跳过此类任务
    _ensure_scheduler(task)
    task['_scheduler']['_taiziManual'] = True

    if action == 'resume':
        _scheduler_mark_progress(task, f'恢复到 {task.get("state", "Doing")}')
    else:
        _scheduler_add_flow(task, f'皇上{action}：{reason or "无"}')

    task['updatedAt'] = now_iso()

    save_tasks(tasks)
    if action == 'resume' and task.get('state') not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, task.get('state'), trigger='resume')
    label = {'stop': '已叫停', 'cancel': '已取消', 'resume': '已恢复'}[action]
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_archive_task(task_id, archived, archive_all_done=False):
    """Archive or unarchive a task, or batch-archive all Done/Cancelled tasks."""
    tasks = load_tasks()
    if archive_all_done:
        count = 0
        for t in tasks:
            if t.get('state') in ('Done', 'Cancelled') and not t.get('archived'):
                t['archived'] = True
                t['archivedAt'] = now_iso()
                count += 1
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道旨意已归档', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    task['archived'] = archived
    if archived:
        task['archivedAt'] = now_iso()
    else:
        task.pop('archivedAt', None)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    label = '已归档' if archived else '已取消归档'
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_delete_task(task_id, confirm_id=''):
    """删除任务（仅允许删除 Done/Cancelled 状态的任务）。"""
    if task_id != confirm_id:
        return {'ok': False, 'error': f'确认ID不匹配: 需要输入 "{task_id}" 进行二次确认'}
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if task.get('state') not in ('Done', 'Cancelled'):
        return {'ok': False, 'error': f'只能删除已完成或已取消的任务，当前状态: {task.get("state")}'}
    tasks = [t for t in tasks if t.get('id') != task_id]
    save_tasks(tasks)
    log.info(f'删除任务: {task_id} | {task.get("title", "")[:40]}')
    return {'ok': True, 'message': f'任务 {task_id} 已删除'}


def update_task_todos(task_id, todos):
    """Update the todos list for a task."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    task['todos'] = todos
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} todos 已更新'}


def read_skill_content(agent_id, skill_name):
    """Read SKILL.md content for a specific skill."""
    # 输入校验：防止路径遍历
    if not _SAFE_NAME_RE.match(agent_id) or not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': '参数含非法字符'}
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    ag = next((a for a in agents if a.get('id') == agent_id), None)
    if not ag:
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    sk = next((s for s in ag.get('skills', []) if s.get('name') == skill_name), None)
    if not sk:
        return {'ok': False, 'error': f'技能 {skill_name} 不存在'}
    skill_path = pathlib.Path(sk.get('path', '')).resolve()
    # 路径遍历保护：确保路径在 OCLAW_HOME 或项目目录下
    allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
    if not any(str(skill_path).startswith(str(root) + os.sep) or skill_path == root for root in allowed_roots):
        return {'ok': False, 'error': '路径不在允许的目录范围内'}
    if not skill_path.exists():
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': '(SKILL.md 文件不存在)', 'path': str(skill_path)}
    try:
        content = skill_path.read_text()
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': content, 'path': str(skill_path)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def add_skill_to_agent(agent_id, skill_name, description, trigger=''):
    """Create a new skill for an agent with a standardised SKILL.md template."""
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skill_name 含非法字符: {skill_name}'}
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    desc_line = description or skill_name
    trigger_section = f'\n## 触发条件\n{trigger}\n' if trigger else ''
    template = (f'---\n'
                f'name: {skill_name}\n'
                f'description: {desc_line}\n'
                f'---\n\n'
                f'# {skill_name}\n\n'
                f'{desc_line}\n'
                f'{trigger_section}\n'
                f'## 输入\n\n'
                f'<!-- 说明此技能接收什么输入 -->\n\n'
                f'## 处理流程\n\n'
                f'1. 步骤一\n'
                f'2. 步骤二\n\n'
                f'## 输出规范\n\n'
                f'<!-- 说明产出物格式与交付要求 -->\n\n'
                f'## 注意事项\n\n'
                f'- (在此补充约束、限制或特殊规则)\n')
    skill_md.write_text(template)
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    return {'ok': True, 'message': f'技能 {skill_name} 已添加到 {agent_id}', 'path': str(skill_md)}


def add_remote_skill(agent_id, skill_name, source_url, description=''):
    """从远程 URL 或本地路径为 Agent 添加 skill SKILL.md 文件。
    
    支持的源：
    - HTTPS URLs: https://raw.githubusercontent.com/...
    - 本地路径: /path/to/SKILL.md 或 file:///path/to/SKILL.md
    """
    # 输入校验
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    if not source_url or not isinstance(source_url, str):
        return {'ok': False, 'error': 'sourceUrl 必须是有效的字符串'}
    
    source_url = source_url.strip()
    
    # 检查 Agent 是否存在
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    if not any(a.get('id') == agent_id for a in agents):
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    
    # 下载或读取文件内容
    try:
        if source_url.startswith('http://'):
            return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
        if source_url.startswith('https://'):
            # HTTPS URL 校验
            if not validate_url(source_url, allowed_schemes=('https',)):
                return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
            
            # 从 URL 下载，带超时保护
            req = Request(source_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            try:
                resp = urlopen(req, timeout=10)
                raw = resp.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    return {'ok': False, 'error': '文件过大（最大 10MB）'}
                content = raw.decode('utf-8', errors='replace')
            except Exception as e:
                return {'ok': False, 'error': f'URL 无法访问: {str(e)[:100]}'}
        
        elif source_url.startswith('file://'):
            # file:// URL 格式
            local_path = pathlib.Path(source_url[7:]).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root) + os.sep) or local_path == root for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        
        elif source_url.startswith('/') or source_url.startswith('.'):
            # 本地绝对或相对路径
            local_path = pathlib.Path(source_url).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root) + os.sep) or local_path == root for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        else:
            return {'ok': False, 'error': '不支持的 URL 格式（仅支持 https://, file://, 或本地路径）'}
    except Exception as e:
        return {'ok': False, 'error': f'文件读取失败: {str(e)[:100]}'}
    
    # 基础验证：检查是否为 Markdown 且包含 YAML frontmatter
    if not content.startswith('---'):
        return {'ok': False, 'error': '文件格式无效（缺少 YAML frontmatter）'}
    
    # 验证 frontmatter 结构（先做字符串检查，再尝试 YAML 解析）
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {'ok': False, 'error': '文件格式无效（YAML frontmatter 结构错误）'}
    if 'name:' not in content[:500]:
        return {'ok': False, 'error': '文件格式无效：frontmatter 缺少 name 字段'}
    try:
        import yaml
        yaml.safe_load(parts[1])  # 严格校验 YAML 语法
    except ImportError:
        pass  # PyYAML 未安装，跳过严格验证，字符串检查已通过
    except Exception as e:
        return {'ok': False, 'error': f'YAML 格式无效: {str(e)[:100]}'}
    
    # 创建本地目录
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    
    # 写入 SKILL.md
    skill_md.write_text(content)
    
    # 保存源信息到 .source.json
    source_info = {
        'skillName': skill_name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    
    return {
        'ok': True,
        'message': f'技能 {skill_name} 已从远程源添加到 {agent_id}',
        'skillName': skill_name,
        'agentId': agent_id,
        'source': source_url,
        'localPath': str(skill_md),
        'size': len(content),
        'addedAt': now_iso(),
    }


def get_remote_skills_list():
    """列表所有已添加的远程 skills 及其源信息"""
    remote_skills = []
    
    # 遍历所有 workspace
    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            source_json = skill_dir / '.source.json'
            skill_md = skill_dir / 'SKILL.md'
            
            if not source_json.exists():
                # 本地创建的 skill，跳过
                continue
            
            try:
                source_info = json.loads(source_json.read_text())
                # 检查 SKILL.md 是否存在
                status = 'valid' if skill_md.exists() else 'not-found'
                remote_skills.append({
                    'skillName': skill_name,
                    'agentId': agent_id,
                    'sourceUrl': source_info.get('sourceUrl', ''),
                    'description': source_info.get('description', ''),
                    'localPath': str(skill_md),
                    'addedAt': source_info.get('addedAt', ''),
                    'lastUpdated': source_info.get('lastUpdated', ''),
                    'status': status,
                })
            except Exception:
                pass
    
    return {
        'ok': True,
        'remoteSkills': remote_skills,
        'count': len(remote_skills),
        'listedAt': now_iso(),
    }


def update_remote_skill(agent_id, skill_name):
    """更新已添加的远程 skill 为最新版本（重新从源 URL 下载）"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    source_json = workspace / '.source.json'
    skill_md = workspace / 'SKILL.md'
    
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill（无 .source.json）'}
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl', '')
        if not source_url:
            return {'ok': False, 'error': '源 URL 不存在'}
        
        # 重新下载
        result = add_remote_skill(agent_id, skill_name, source_url, 
                                  source_info.get('description', ''))
        if result['ok']:
            result['message'] = f'技能已更新'
            source_info_updated = json.loads(source_json.read_text())
            result['newVersion'] = source_info_updated.get('checksum', 'unknown')
        return result
    except Exception as e:
        return {'ok': False, 'error': f'更新失败: {str(e)[:100]}'}


def remove_remote_skill(agent_id, skill_name):
    """移除已添加的远程 skill"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    if not workspace.exists():
        return {'ok': False, 'error': f'技能不存在: {skill_name}'}
    
    # 检查是否为远程 skill
    source_json = workspace / '.source.json'
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill，无法通过此 API 移除'}
    
    try:
        # 删除整个 skill 目录
        import shutil
        shutil.rmtree(workspace)
        
        # Re-sync agent config
        try:
            subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
        except Exception:
            pass
        
        return {'ok': True, 'message': f'技能 {skill_name} 已从 {agent_id} 移除'}
    except Exception as e:
        return {'ok': False, 'error': f'移除失败: {str(e)[:100]}'}


def _compute_checksum(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def migrate_notification_config():
    """自动迁移旧配置 (feishu_webhook) 到新结构 (notification)"""
    cfg_path = DATA / 'morning_brief_config.json'
    cfg = read_json(cfg_path, {})
    if not cfg:
        return
    if 'notification' in cfg:
        return
    if 'feishu_webhook' not in cfg:
        return
    webhook = cfg.get('feishu_webhook', '').strip()
    cfg['notification'] = {
        'enabled': bool(webhook),
        'channel': 'feishu',
        'webhook': webhook
    }
    try:
        atomic_json_write(cfg_path, cfg)
        log.info('已自动迁移 feishu_webhook 到 notification 配置')
    except Exception as e:
        log.warning(f'迁移配置失败: {e}')


def push_notification():
    """通用消息推送 (支持多渠道)"""
    cfg = read_json(DATA / 'morning_brief_config.json', {})
    notification = cfg.get('notification', {})
    if not notification and cfg.get('feishu_webhook'):
        notification = {'enabled': True, 'channel': 'feishu', 'webhook': cfg['feishu_webhook']}
    if not notification.get('enabled', True):
        return
    channel_type = notification.get('channel', 'feishu')
    webhook = notification.get('webhook', '').strip()
    if not webhook:
        return
    channel_cls = get_channel(channel_type)
    if not channel_cls:
        log.warning(f'未知的通知渠道: {channel_type}')
        return
    if not channel_cls.validate_webhook(webhook):
        log.warning(f'{channel_cls.label} Webhook URL 不合法: {webhook}')
        return
    brief = read_json(DATA / 'morning_brief.json', {})
    date_str = brief.get('date', '')
    total = sum(len(v) for v in (brief.get('categories') or {}).values())
    if not total:
        return
    cat_lines = []
    for cat, items in (brief.get('categories') or {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    summary = '\n'.join(cat_lines)
    date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
    title = f'📰 天下要闻 · {date_fmt}'
    content = f'共 **{total}** 条要闻已更新\n{summary}'
    url = _get_external_dashboard_url()
    success = channel_cls.send(webhook, title, content, url)
    print(f'[{channel_cls.label}] 推送{"成功" if success else "失败"}')


def push_to_feishu():
    """Push morning brief link to Feishu via webhook. (已弃用，使用 push_notification)"""
    push_notification()


# 旨意标题最低要求
_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def handle_create_task(title, org='中书省', official='中书令', priority='normal', template_id='', params=None, target_dept=''):
    """从看板创建新任务（圣旨模板下旨）。"""
    if not title or not title.strip():
        return {'ok': False, 'error': '任务标题不能为空'}
    title = title.strip()
    # 剥离 Conversation info 元数据
    title = re.split(r'\n*Conversation info\s*\(', title, maxsplit=1)[0].strip()
    title = re.split(r'\n*```', title, maxsplit=1)[0].strip()
    # 清理常见前缀: "传旨:" "下旨:" 等
    title = re.sub(r'^(传旨|下旨)[：:\uff1a]\s*', '', title)
    if len(title) > 100:
        title = title[:100] + '…'
    # 标题质量校验：防止闲聊被误建为旨意
    if len(title) < _MIN_TITLE_LEN:
        return {'ok': False, 'error': f'标题过短（{len(title)}<{_MIN_TITLE_LEN}字），不像是旨意'}
    if title.lower() in _JUNK_TITLES:
        return {'ok': False, 'error': f'「{title}」不是有效旨意，请输入具体工作指令'}
    # 生成 task id: JJC-YYYYMMDD-NNN
    _BJT = datetime.timezone(datetime.timedelta(hours=8))
    today = datetime.datetime.now(_BJT).strftime('%Y%m%d')
    tasks = load_tasks()
    today_ids = [t['id'] for t in tasks if t.get('id', '').startswith(f'JJC-{today}-')]
    seq = 1
    if today_ids:
        nums = [int(tid.split('-')[-1]) for tid in today_ids if tid.split('-')[-1].isdigit()]
        seq = max(nums) + 1 if nums else 1
    task_id = f'JJC-{today}-{seq:03d}'
    # 看板创建任务直接进入中书省（跳过太子分拣）
    # 原因：太子 SOUL 协议要求「皇上明确说执行」才可转交中书省，
    # 程序化派发缺乏此确认上下文，导致太子死锁不推进。
    # 飞书渠道不受影响（走独立对话流，用户可自然确认）。
    # target_dept 记录模板建议的最终执行部门（仅供尚书省派发参考）
    initial_org = '中书省'
    new_task = {
        'id': task_id,
        'title': title,
        'official': official,
        'org': initial_org,
        'state': 'Zhongshu',
        'now': '中书省正在起草执行方案',
        'eta': '-',
        'block': '无',
        'output': '',
        'ac': '',
        'priority': priority,
        'templateId': template_id,
        'templateParams': params or {},
        'flow_log': [
            {
                'at': now_iso(),
                'from': '皇上',
                'to': '太子',
                'remark': f'旨库下旨：{title}'
            },
            {
                'at': now_iso(),
                'from': '太子',
                'to': '中书省',
                'remark': '旨库派发→中书省'
            },
        ],
        'updatedAt': now_iso(),
    }
    if target_dept:
        new_task['targetDept'] = target_dept

    _ensure_scheduler(new_task)
    _scheduler_snapshot(new_task, 'create-task-initial')
    _scheduler_mark_progress(new_task, '任务创建')

    tasks.insert(0, new_task)
    save_tasks(tasks)
    log.info(f'创建任务: {task_id} | {title[:40]}')

    dispatch_for_state(task_id, new_task, 'Zhongshu', trigger='imperial-edict')

    return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，正在派发给中书省起草方案'}


def handle_review_action(task_id, action, comment=''):
    """门下省御批：准奏/封驳。

    准奏流程（两阶段派发）：
    1. 立即：flow_log 记录「门下省→中书省」（准奏通知），异步通知中书省知悉
    2. 3秒后：flow_log 追加「中书省→尚书省」（准奏转交），程序派发尚书省
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if task.get('state') not in ('Review', 'Menxia'):
        return {'ok': False, 'error': f'任务 {task_id} 当前状态为 {task.get("state")}，无法御批'}

    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'review-before-{action}')

    if action == 'approve':
        if task['state'] == 'Menxia':
            task['state'] = 'Assigned'
            task['org'] = '尚书省'  # 【F3】同步更新 org，与 cmd_state STATE_ORG_MAP 保持一致
            task['now'] = '门下省准奏，已通知中书省，移交尚书省派发'
            remark = f'✅ 准奏通知：{comment or "门下省审议通过，中书省知悉记录"}'
            to_dept = '中书省'
        else:  # Review
            task['state'] = 'Done'
            task['org'] = '完成'  # 【F3】同步更新 org
            task['now'] = '御批通过，任务完成'
            remark = f'✅ 御批准奏：{comment or "审查通过"}'
            to_dept = '皇上'
    elif action == 'reject':
        round_num = (task.get('review_round') or 0) + 1
        task['review_round'] = round_num
        task['state'] = 'Zhongshu'
        task['org'] = '中书省'  # 【F3】同步更新 org
        task['now'] = f'封驳退回中书省修订（第{round_num}轮）'
        remark = f'🚫 封驳：{comment or "需要修改"}'
        to_dept = '中书省'
    else:
        return {'ok': False, 'error': f'未知操作: {action}'}

    # 追加第1条 flow_log（立即写入）
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '门下省' if task.get('state') != 'Done' else '皇上',
        'to': to_dept,
        'remark': remark
    })
    _scheduler_mark_progress(task, f'审议动作 {action} -> {task.get("state")}')
    task['_scheduler']['_taiziManual'] = True
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    if action == 'approve' and task.get('state') == 'Assigned':
        # ═══════════════════════════════════════════════════════════════
        # 两阶段准奏派发
        # ═══════════════════════════════════════════════════════════════
        _title = task.get('title', '(无标题)')

        # 1a. 异步通知中书省（fire-and-forget，仅告知，无需回复）
        def _notify_zhongshu():
            try:
                _msg = (
                    f'📢 门下省准奏通知\n\n'
                    f'任务ID: {task_id}\n'
                    f'旨意: {_title}\n'
                    f'门下省已准奏，程序将自动派发尚书省。\n\n'
                    f'你无需操作，请知悉记录。禁止执行任何派发操作，禁止联系尚书省或六部。'
                )
                subprocess.run(
                    ['openclaw', 'agent', '--agent', 'zhongshu', '-m', _msg, '--timeout', '60'],
                    capture_output=True, text=True, timeout=70
                )
            except Exception as e:
                log.warning(f'通知中书省失败（不影响流程）: {e}')
        threading.Thread(target=_notify_zhongshu, daemon=True).start()

        # 1b. 3秒后：追加第2条 flow_log + 派发尚书省
        def _delayed_dispatch():
            try:
                _tasks = load_tasks()
                _t = next((t for t in _tasks if t.get('id') == task_id), None)
                if not _t or _t.get('state') != 'Assigned':
                    log.warning(f'{task_id} 3秒后状态已变更（{_t.get("state") if _t else "不存在"}），取消延迟派发')
                    return
                # 追加第2条 flow_log：中书省 → 尚书省
                _t.setdefault('flow_log', []).append({
                    'at': now_iso(),
                    'from': '中书省',
                    'to': '尚书省',
                    'remark': '📋 准奏转交：门下省已准奏，转交尚书省派发执行'
                })
                _t['updatedAt'] = now_iso()
                save_tasks(_tasks)
                log.info(f'{task_id} flow_log 已追加：中书省→尚书省，开始派发尚书省')
                dispatch_for_state(task_id, _t, 'Assigned')
            except Exception as e:
                log.error(f'{task_id} 延迟派发失败: {e}')
        threading.Timer(3.0, _delayed_dispatch).start()

        return {'ok': True, 'message': f'{task_id} 已准奏，已通知中书省，即将派发尚书省'}

    # 封驳/审查通过路径：按原逻辑派发
    new_state = task['state']
    if new_state not in ('Done',):
        dispatch_for_state(task_id, task, new_state)

    label = '已准奏' if action == 'approve' else '已封驳'
    dispatched = ' (已自动派发 Agent)' if new_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {label}{dispatched}'}


# ══ Agent 在线状态检测 ══

_AGENT_DEPTS = [
    {'id':'taizi',   'label':'太子',  'emoji':'🤴', 'role':'太子',     'rank':'储君'},
    {'id':'zhongshu','label':'中书省','emoji':'📜', 'role':'中书令',   'rank':'正一品'},
    {'id':'menxia',  'label':'门下省','emoji':'🔍', 'role':'侍中',     'rank':'正一品'},
    {'id':'shangshu','label':'尚书省','emoji':'📮', 'role':'尚书令',   'rank':'正一品'},
    {'id':'hubu',    'label':'户部',  'emoji':'💰', 'role':'户部尚书', 'rank':'正二品'},
    {'id':'libu',    'label':'礼部',  'emoji':'📝', 'role':'礼部尚书', 'rank':'正二品'},
    {'id':'bingbu',  'label':'兵部',  'emoji':'⚔️', 'role':'兵部尚书', 'rank':'正二品'},
    {'id':'xingbu',  'label':'刑部',  'emoji':'⚖️', 'role':'刑部尚书', 'rank':'正二品'},
    {'id':'gongbu',  'label':'工部',  'emoji':'🔧', 'role':'工部尚书', 'rank':'正二品'},
    {'id':'libu_hr', 'label':'吏部',  'emoji':'👔', 'role':'吏部尚书', 'rank':'正二品'},
    {'id':'zaochao', 'label':'钦天监','emoji':'📰', 'role':'朝报官',   'rank':'正三品'},
    {'id':'jiancha', 'label':'御史台','emoji':'🛡️', 'role':'监察御史', 'rank':'正三品'},
]

_AGENT_LABELS = {d['id']: d['label'] for d in _AGENT_DEPTS}


def _check_gateway_alive():
    """检测 Gateway 是否在运行。

    使用纯内部判断机制，不依赖外部 HTTP 请求或用户访问的 IP/Port：
    1. 本地 TCP 端口探测（最可靠，适用于所有环境含 Docker）
    2. 进程名检测 pgrep（Linux/macOS 兜底）
    """
    # 1. 本地 TCP 端口探测 —— 无论是物理机还是 Docker，同容器内 127.0.0.1 始终可达
    try:
        gw_url = _get_gateway_base_url()
        parsed = urlparse(gw_url)
        gw_port = parsed.port or 18789
        with socket.create_connection(('127.0.0.1', gw_port), timeout=2):
            return True
    except Exception:
        pass
    # 2. 进程名检测兜底（Linux/macOS）
    if os.name != 'nt':
        try:
            result = subprocess.run(['pgrep', '-f', 'openclaw-gateway'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _check_gateway_probe():
    """通过 HTTP probe 检测 Gateway 是否响应。"""
    base = _get_gateway_base_url()
    for url in (f'{base}/', f'{base}/healthz'):
        try:
            from urllib.request import urlopen
            resp = urlopen(url, timeout=3)
            if 200 <= resp.status < 500:
                return True
        except Exception:
            continue
    return False


def _get_agent_session_status(agent_id):
    """读取 Agent 的 sessions.json 获取活跃状态。
    返回: (last_active_ts_ms, session_count, is_busy)
    """
    sessions_file = OCLAW_HOME / 'agents' / agent_id / 'sessions' / 'sessions.json'
    if not sessions_file.exists():
        return 0, 0, False
    try:
        data = json.loads(sessions_file.read_text())
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for v in data.values():
            ts = v.get('updatedAt', 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = ts
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        is_busy = age_ms <= 2 * 60 * 1000  # 2分钟内视为正在工作
        return last_ts, session_count, is_busy
    except Exception:
        return 0, 0, False


def _check_agent_process(agent_id):
    """检测是否有该 Agent 的 openclaw-agent 进程正在运行。
    
    支持多种检测模式:
    1. pgrep 检测进程名（标准模式）
    2. Session 文件最近活跃时间检测（daemon/守护进程模式）
    3. 跳过检测，返回 False（Docker 容器等受限环境）
    """
    # 方式1: pgrep 检测进程名
    try:
        # Issue #6#2: 使用精确匹配，防止 libu 匹配到 libu_hr
        result = subprocess.run(
            ['pgrep', '-f', f'openclaw.*--agent.*(\\s|^|/){agent_id}(\\s|$)'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass
    
    # 方式2: 检查 session 文件最近活跃（daemon 模式下无独立进程）
    try:
        sessions_file = OCLAW_HOME / 'agents' / agent_id / 'sessions' / 'sessions.json'
        if sessions_file.exists():
            data = json.loads(sessions_file.read_text())
            if isinstance(data, dict):
                now_ms = int(datetime.datetime.now().timestamp() * 1000)
                for v in data.values():
                    ts = v.get('updatedAt', 0)
                    if isinstance(ts, (int, float)) and (now_ms - ts) <= 5 * 60 * 1000:
                        return True
    except Exception:
        pass
    
    return False


def _check_agent_workspace(agent_id):
    """检查 Agent 工作空间是否存在。"""
    ws = OCLAW_HOME / f'workspace-{agent_id}'
    return ws.is_dir()


def get_agents_status():
    """获取所有 Agent 的在线状态。
    返回各 Agent 的:
    - status: 'running' | 'idle' | 'offline' | 'unconfigured'
    - lastActive: 最后活跃时间
    - sessions: 会话数
    - hasWorkspace: 工作空间是否存在
    - processAlive: 是否有进程在运行
    """
    gateway_alive = _check_gateway_alive()
    gateway_probe = _check_gateway_probe() if gateway_alive else False

    agents = []
    seen_ids = set()
    for dept in _AGENT_DEPTS:
        aid = dept['id']
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        has_workspace = _check_agent_workspace(aid)
        last_ts, sess_count, is_busy = _get_agent_session_status(aid)
        process_alive = _check_agent_process(aid)

        # 状态判定
        if not has_workspace:
            status = 'unconfigured'
            status_label = '❌ 未配置'
        elif not gateway_alive:
            status = 'offline'
            status_label = '🔴 Gateway 离线'
        elif process_alive or is_busy:
            status = 'running'
            status_label = '🟢 运行中'
        elif last_ts > 0:
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            age_ms = now_ms - last_ts
            if age_ms <= 10 * 60 * 1000:  # 10分钟内
                status = 'idle'
                status_label = '🟡 待命'
            elif age_ms <= 3600 * 1000:  # 1小时内
                status = 'idle'
                status_label = '⚪ 空闲'
            else:
                status = 'idle'
                status_label = '⚪ 休眠'
        else:
            status = 'idle'
            status_label = '⚪ 无记录'

        # 格式化最后活跃时间
        last_active_str = None
        if last_ts > 0:
            try:
                last_active_str = datetime.datetime.fromtimestamp(
                    last_ts / 1000
                ).strftime('%m-%d %H:%M')
            except Exception:
                pass

        agents.append({
            'id': aid,
            'label': dept['label'],
            'emoji': dept['emoji'],
            'role': dept['role'],
            'status': status,
            'statusLabel': status_label,
            'lastActive': last_active_str,
            'lastActiveTs': last_ts,
            'sessions': sess_count,
            'hasWorkspace': has_workspace,
            'processAlive': process_alive,
        })

    return {
        'ok': True,
        'gateway': {
            'alive': gateway_alive,
            'probe': gateway_probe,
            'status': '🟢 运行中' if gateway_probe else ('🟡 进程在但无响应' if gateway_alive else '🔴 未启动'),
        },
        'agents': agents,
        'checkedAt': now_iso(),
    }


def wake_agent(agent_id, message=''):
    """唤醒指定 Agent，发送一条心跳/唤醒消息。"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agent_id 非法: {agent_id}'}
    if not _check_agent_workspace(agent_id):
        return {'ok': False, 'error': f'{agent_id} 工作空间不存在，请先配置'}
    if not _check_gateway_alive():
        return {'ok': False, 'error': 'Gateway 未启动，请先运行 openclaw gateway start'}

    # agent_id 直接作为 runtime_id（openclaw agents list 中的注册名）
    runtime_id = agent_id
    msg = message or f'🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {now_iso()}'

    def do_wake():
        try:
            cmd = ['openclaw', 'agent', '--agent', runtime_id, '-m', msg, '--timeout', '120']
            log.info(f'🔔 唤醒 {agent_id}...')
            # 带重试（最多2次）
            for attempt in range(1, 3):
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=130)
                if result.returncode == 0:
                    log.info(f'✅ {agent_id} 已唤醒')
                    return
                err_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {agent_id} 唤醒失败(第{attempt}次): {err_msg}')
                if attempt < 2:
                    import time
                    time.sleep(5)
            log.error(f'❌ {agent_id} 唤醒最终失败')
        except subprocess.TimeoutExpired:
            log.error(f'❌ {agent_id} 唤醒超时(130s)')
        except Exception as e:
            log.warning(f'⚠️ {agent_id} 唤醒异常: {e}')
    threading.Thread(target=do_wake, daemon=True).start()

    return {'ok': True, 'message': f'{agent_id} 唤醒指令已发出，约10-30秒后生效'}


# ══ Agent 实时活动读取 ══

# 状态 → agent_id 映射
_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,         # 六部，需从 org 推断
    'Review': 'shangshu',
    'Next': None,          # 待执行，从 org 推断
    'Pending': 'zhongshu', # 待处理，默认中书省
    'Done': 'taizi',       # 完成时通知太子回奏皇上（与 kanban_update.py V4 一致）
}
_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
    '执行中': None,  # Fix #3: fallback — 无法确定具体部门时不自动派发
}

_TERMINAL_STATES = {'Done', 'Cancelled'}

# Issue #6#6: 并发派发上限（防止短时间内大量派发压垮 Gateway）
_MAX_CONCURRENT_DISPATCHES = 3
_dispatch_semaphore = threading.Semaphore(_MAX_CONCURRENT_DISPATCHES)

# 分级超时监督配置
_SUPERVISION_CONFIG = {
    # 三省：10分钟催办，15分钟超时上报太子
    'Zhongshu':  {'remind_sec': 600, 'timeout_sec': 900, 'label': '中书省', 'parent_agent': 'taizi'},
    'Menxia':    {'remind_sec': 600, 'timeout_sec': 900, 'label': '门下省', 'parent_agent': 'zhongshu'},
    'Assigned':  {'remind_sec': 600, 'timeout_sec': 900, 'label': '尚书省', 'parent_agent': 'taizi'},
    # 六部/Next：5分钟催办，30分钟超时上报太子（原 timeout_sec=0 不会上报）
    'Doing':     {'remind_sec': 300, 'timeout_sec': 1500, 'label': '六部',   'parent_agent': 'shangshu'},
    'Next':      {'remind_sec': 300, 'timeout_sec': 1500, 'label': '六部',   'parent_agent': 'shangshu'},
    'Taizi':     {'remind_sec': 600, 'timeout_sec': 900, 'label': '太子',   'parent_agent': 'taizi'},
    'Pending':   {'remind_sec': 600, 'timeout_sec': 900, 'label': '中书省', 'parent_agent': 'taizi'},
    'Review':    {'remind_sec': 300, 'timeout_sec': 600,  'label': '汇总审查', 'parent_agent': 'taizi'},
}

# 长期停滞二次通知间隔（秒）：超过 timeout_sec 后，每 N 秒重复通知太子
_PERIODIC_RENOTIFY_SEC = 1600  # 30 分钟

# ═══════════════════════════════════════════════════════════════════════
# 🎯 针对性催办消息模板（每个部门独立定制）
#
# 和 SOUL.md 一样可以随时修改每个部门的催办内容。
# 每个部门收到催办时，会看到与其职责相关的专属催办消息。
# ═══════════════════════════════════════════════════════════════════════
_AGENT_REMIND_TEMPLATES = {
    # 三省催办模板（由上级部门发出）
    'zhongshu': (
        '⏰ 中书省催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟（超过10分钟阈值）\n\n'
        '中书省作为方案起草部门，请立即：\n'
        '1. 确认是否已收到太子转交的旨意\n'
        '2. 如已收到，说明当前进展（分析/起草/提交门下审议/等待门下回复）\n'
        '3. 如未收到，说明情况以便太子协调\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'menxia': (
        '⏰ 门下省催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟（超过10分钟阈值）\n\n'
        '中书省提醒：请立即确认是否已收到审议请求。\n'
        '如已收到，请说明审议进展：\n'
        '  · 正在进行四维审核（可行性/完整性/风险/资源）\n'
        '  · 已出具结论（准奏/封驳）\n'
        '如未收到，请回复以便中书省重新发送方案。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'shangshu': (
        '⏰ 尚书省催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟（超过10分钟阈值）\n\n'
        '太子调度提醒：门下省已准奏，请尚书省立即：\n'
        '1. 确认是否已收到执行请求\n'
        '2. 说明当前进展（分析方案/确定派发/已派发六部/汇总结果中）\n'
        '3. 如未收到，说明情况以便太子协调处理\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    # 六部催办模板（由尚书省发出）
    'libu': (
        '⏰ 礼部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒礼部：请立即确认是否已收到任务。\n'
        '如已收到，请说明文档/UI撰写进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'hubu': (
        '⏰ 户部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒户部：请立即确认是否已收到任务。\n'
        '如已收到，请说明数据分析/统计工作进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'bingbu': (
        '⏰ 兵部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒兵部：请立即确认是否已收到任务。\n'
        '如已收到，请说明开发/编码工作进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'xingbu': (
        '⏰ 刑部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒刑部：请立即确认是否已收到任务。\n'
        '如已收到，请说明审查/测试工作进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'gongbu': (
        '⏰ 工部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒工部：请立即确认是否已收到任务。\n'
        '如已收到，请说明部署/运维工作进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
    'libu_hr': (
        '⏰ 吏部催办通知\n'
        '任务ID: {task_id}\n'
        '任务标题: {task_title}\n'
        '已等待: {stalled_min} 分钟\n\n'
        '尚书省提醒吏部：请立即确认是否已收到任务。\n'
        '如已收到，请说明Agent管理/培训工作进展。\n'
        '如未收到，请回复以便尚书省重新派发。\n\n'
        '⚠️ 看板已有此任务，请勿重复创建。'
    ),
}

# 默认催办模板
_DEFAULT_REMIND_TEMPLATE = (
    '⏰ 催办通知\n'
    '任务ID: {task_id}\n'
    '任务标题: {task_title}\n'
    '已等待: {stalled_min} 分钟\n\n'
    '请立即确认是否已收到该任务。如未收到，请说明情况。\n\n'
    '⚠️ 看板已有此任务，请勿重复创建。'
)

# 超时上报太子的针对性模板
_TIMEOUT_REPORT_TEMPLATE = (
    '🚨 超时上报\n'
    '任务ID: {task_id}\n'
    '任务标题: {task_title}\n'
    '停滞部门: {state_label}\n'
    '负责催办的上级: {parent_agent_label}\n'
    '已超时: {stalled_min} 分钟（超过15分钟阈值）\n\n'
    '太子请介入协调：\n'
    '1. 检查 {state_label} 当前状态\n'
    '2. 联系 {parent_agent_label} 了解催办结果\n'
    '3. 必要时直接唤醒停滞部门或调整流程\n\n'
    '⚠️ 看板已有此任务，请勿重复创建。'
)


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _ensure_scheduler(task):
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    sched.setdefault('enabled', True)
    sched.setdefault('stallThresholdSec', 600)
    sched.setdefault('maxRetry', 2)
    sched.setdefault('retryCount', 0)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('autoRollback', True)
    if not sched.get('lastProgressAt'):
        sched['lastProgressAt'] = task.get('updatedAt') or now_iso()
    if 'stallSince' not in sched:
        sched['stallSince'] = None
    if 'lastDispatchStatus' not in sched:
        sched['lastDispatchStatus'] = 'idle'
    if 'remindedAt' not in sched:
        sched['remindedAt'] = None
    if 'timeoutReportedAt' not in sched:
        sched['timeoutReportedAt'] = None
    if 'snapshot' not in sched:
        sched['snapshot'] = {
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'now': task.get('now', ''),
            'savedAt': now_iso(),
            'note': 'init',
        }
    return sched


def _scheduler_add_flow(task, remark, to=''):
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '太子调度',
        'to': to or task.get('org', ''),
        'remark': f'🧭 {remark}'
    })


def _scheduler_snapshot(task, note=''):
    sched = _ensure_scheduler(task)
    sched['snapshot'] = {
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'now': task.get('now', ''),
        'savedAt': now_iso(),
        'note': note or 'snapshot',
    }


def _scheduler_mark_progress(task, note=''):
    sched = _ensure_scheduler(task)
    sched['lastProgressAt'] = now_iso()
    sched['stallSince'] = None
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['lastEscalatedAt'] = None
    if note:
        _scheduler_add_flow(task, f'进展确认：{note}')


def _update_task_scheduler(task_id, updater):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return False
    sched = _ensure_scheduler(task)
    updater(task, sched)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return True


def get_scheduler_state(task_id):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'scheduler': sched,
        'stalledSec': stalled_sec,
        'checkedAt': now_iso(),
    }


def handle_scheduler_retry(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    sched = _ensure_scheduler(task)
    sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
    sched['lastRetryAt'] = now_iso()
    sched['lastDispatchTrigger'] = 'taizi-retry'
    _scheduler_add_flow(task, f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    dispatch_for_state(task_id, task, state, trigger='taizi-retry')
    return {'ok': True, 'message': f'{task_id} 已触发重试派发', 'retryCount': sched['retryCount']}


def handle_scheduler_escalate(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = _ensure_scheduler(task)
    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = now_iso()
    _scheduler_add_flow(task, f'升级到{target_label}协调：{reason or "任务停滞"}', to=target_label)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    wake_agent(target, msg)

    return {'ok': True, 'message': f'{task_id} 已升级至{target_label}', 'escalationLevel': next_level}


def handle_scheduler_rollback(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    old_state = task.get('state', '')
    task['state'] = snap_state
    task['org'] = snapshot.get('org', task.get('org', ''))
    task['now'] = f'↩️ 太子调度自动回滚：{reason or "恢复到上个稳定节点"}'
    task['block'] = '无'
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['stallSince'] = None
    sched['lastProgressAt'] = now_iso()
    _scheduler_add_flow(task, f'执行回滚：{old_state} → {snap_state}，原因：{reason or "停滞恢复"}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    if snap_state not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, snap_state, trigger='taizi-rollback')

    return {'ok': True, 'message': f'{task_id} 已回滚到 {snap_state}'}


def handle_scheduler_scan(threshold_sec=600):
    """增强版调度扫描：集成分级超时监督（三省15min + 六部5min催办）。

    规则：
    - 三省（中书/门下/尚书）：10分钟催办（上级催），15分钟超时上报太子
    - 六部（Doing/Next）：5分钟催办（尚书催），催办后仍无响应则检查原因修复汇报
    - 催办由直接上级部门执行，太子总揽全局兜底
    """
    threshold_sec = max(60, int(threshold_sec or 600))
    tasks = load_tasks()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    pending_retries = []
    pending_escalates = []
    pending_rollbacks = []
    # 新增：分级催办和超时上报
    pending_reminds = []
    pending_timeouts = []
    actions = []
    changed = False

    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        if state == 'Blocked':
            continue

        sched = _ensure_scheduler(task)
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))

        # ── 分级超时监督逻辑 ──
        sup_cfg = _SUPERVISION_CONFIG.get(state)
        if sup_cfg and stalled_sec > 0:
            remind_sec = sup_cfg['remind_sec']
            timeout_sec = sup_cfg['timeout_sec']
            parent_agent = sup_cfg['parent_agent']
            state_label = sup_cfg['label']

            # 确定催办者（parent_agent）
            # 三省催办规则：中书催门下/尚书，尚书催六部
            remind_agent = parent_agent

            # ① 催办阶段
            if stalled_sec >= remind_sec:
                reminded = sched.get('remindedAt')
                if not reminded:
                    sched['remindedAt'] = now_iso()
                    sched['stallSince'] = now_iso()
                    _scheduler_add_flow(task, f'{state_label}停滞{stalled_sec}秒，{remind_agent}发送催办')
                    pending_reminds.append((task_id, state, remind_agent, state_label, stalled_sec, remind_sec))
                    actions.append({'taskId': task_id, 'action': 'remind', 'by': remind_agent, 'stalledSec': stalled_sec})
                    changed = True

            # ② 超时上报阶段（仅对有 timeout_sec > 0 的状态生效，即三省）
            if timeout_sec > 0 and stalled_sec >= timeout_sec:
                reported = sched.get('timeoutReportedAt')
                if not reported:
                    sched['timeoutReportedAt'] = now_iso()
                    _scheduler_add_flow(task, f'{state_label}超时{stalled_sec}秒，上报太子协调')
                    pending_timeouts.append((task_id, state, state_label, stalled_sec, parent_agent))
                    actions.append({'taskId': task_id, 'action': 'timeout_report', 'stalledSec': stalled_sec})
                    changed = True

        # ── 原有逻辑：停滞检测阈值 ──
        task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
        if stalled_sec < task_threshold:
            # 即使未达到停滞阈值，也要保存催办/超时标记
            if changed:
                pass  # 后面统一保存
            continue

        if not sched.get('stallSince'):
            sched['stallSince'] = now_iso()
            changed = True

        # ── 跳过不应自动介入的任务 ──
        # 1. 太子手动操作的任务（准奏/叫停/恢复等）
        # 2. 派发已成功（lastDispatchStatus=success），说明 Agent 已收到任务
        # 3. 派发正在进行中（lastDispatchStatus=queued/dispatching）
        _dispatch_status = sched.get('lastDispatchStatus', '')
        if sched.get('_taiziManual'):
            continue
        if _dispatch_status in ('success', 'queued', 'dispatching', 'gateway-offline'):
            continue

        retry_count = int(sched.get('retryCount') or 0)
        max_retry = max(0, int(sched.get('maxRetry') or 1))
        level = int(sched.get('escalationLevel') or 0)

        if retry_count < max_retry:
            sched['retryCount'] = retry_count + 1
            sched['lastRetryAt'] = now_iso()
            sched['lastDispatchTrigger'] = 'taizi-scan-retry'
            _scheduler_add_flow(task, f'停滞{stalled_sec}秒，触发自动重试第{sched["retryCount"]}次')
            pending_retries.append((task_id, state))
            actions.append({'taskId': task_id, 'action': 'retry', 'stalledSec': stalled_sec})
            changed = True
            continue

        if level < 2:
            next_level = level + 1
            target = 'menxia' if next_level == 1 else 'shangshu'
            target_label = '门下省' if next_level == 1 else '尚书省'
            sched['escalationLevel'] = next_level
            sched['lastEscalatedAt'] = now_iso()
            _scheduler_add_flow(task, f'停滞{stalled_sec}秒，升级至{target_label}协调', to=target_label)
            pending_escalates.append((task_id, state, target, target_label, stalled_sec))
            actions.append({'taskId': task_id, 'action': 'escalate', 'to': target_label, 'stalledSec': stalled_sec})
            changed = True
            continue

        if sched.get('autoRollback', True) and not sched.get('_taiziManual'):
            snapshot = sched.get('snapshot') or {}
            snap_state = snapshot.get('state')
            if snap_state and snap_state != state:
                # ── BUG FIX #4: 回滚前检查六部是否已在执行 ──
                # 原代码直接回滚 Doing→Zhongshu 并重新派发，导致六部收到重复派发。
                # 修复：检查 flow_log 中是否有尚书省→六部的派发记录，
                # 且该记录之后没有六部→尚书省的完成记录，说明六部正在执行中。
                _LIU_BU_AGENT_SET = {'gongbu', 'bingbu', 'hubu', 'libu', 'xingbu', 'libu_hr'}
                flow_log_entries = task.get('flow_log', [])

                # 遍历 flow_log 统计六部派发和完成
                _dispatched_to_liubu = False
                _liubu_reported_back = False
                _last_dispatch_dept = ''
                for _fl_entry in flow_log_entries:
                    _fl_from_raw = (_fl_entry.get('from', '') or '').strip()
                    _fl_to_raw = (_fl_entry.get('to', '') or '').strip()
                    _fl_from_id = _ORG_AGENT_MAP.get(_fl_from_raw, _fl_from_raw.lower())
                    _fl_to_id = _ORG_AGENT_MAP.get(_fl_to_raw, _fl_to_raw.lower())
                    if _fl_from_id and _fl_from_id == 'shangshu' and _fl_to_id in _LIU_BU_AGENT_SET:
                        _dispatched_to_liubu = True
                        _last_dispatch_dept = _fl_to_id
                    if _fl_to_id and _fl_to_id == 'shangshu' and _fl_from_id in _LIU_BU_AGENT_SET:
                        _liubu_reported_back = True

                if _dispatched_to_liubu and not _liubu_reported_back:
                    # 六部已被派发但尚未回报，不应回滚，改为催办六部
                    log(f'⚠️ {task_id} 回滚跳过：{_last_dispatch_dept}已在执行中，改为催办')
                    _scheduler_add_flow(task, f'回滚跳过：{_last_dispatch_dept}已在执行，改为催办')
                    # 重置停滞计数器，避免重复触发
                    sched['retryCount'] = 0
                    sched['escalationLevel'] = 0
                    sched['stallSince'] = None
                    sched['lastProgressAt'] = now_iso()
                    # 催办正在执行的六部
                    if _last_dispatch_dept:
                        msg = (
                            f'⏰ 太子调度催办通知\n'
                            f'任务ID: {task_id}\n'
                            f'任务标题: {task.get("title", "")}\n'
                            f'已停滞: {stalled_sec} 秒\n'
                            f'系统检测到你已收到尚书省的派发并正在执行。\n'
                            f'请尽快完成并上报尚书省。\n'
                            f'⚠️ 看板已有此任务，请勿重复创建。'
                        )
                        wake_agent(_last_dispatch_dept, msg)
                    changed = True
                    continue

                old_state = state
                task['state'] = snap_state
                task['org'] = snapshot.get('org', task.get('org', ''))
                task['now'] = '↩️ 太子调度自动回滚到稳定节点'
                task['block'] = '无'
                sched['retryCount'] = 0
                sched['escalationLevel'] = 0
                sched['stallSince'] = None
                sched['lastProgressAt'] = now_iso()
                _scheduler_add_flow(task, f'连续停滞，自动回滚：{old_state} → {snap_state}')
                pending_rollbacks.append((task_id, snap_state))
                actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                changed = True
        elif sched.get('_taiziManual'):
            # 太子手动推进的任务不自动回退，但重置标记以允许下次监控
            sched['_taiziManual'] = False

    if changed:
        save_tasks(tasks)

    for task_id, state in pending_retries:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task:
            dispatch_for_state(task_id, retry_task, state, trigger='taizi-scan-retry')

    for task_id, state, target, target_label, stalled_sec in pending_escalates:
        msg = (
            f'🧭 太子调度升级通知\n'
            f'任务ID: {task_id}\n'
            f'当前状态: {state}\n'
            f'已停滞: {stalled_sec} 秒\n'
            f'请立即介入协调推进\n'
            f'⚠️ 看板已有任务，请勿重复创建。'
        )
        wake_agent(target, msg)

    # ── 长期停滞严重警告：停滞超过 50 分钟且未升级 ──
    CRITICAL_STALL_SEC = 3000  # 50分钟
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = _ensure_scheduler(task)
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        if stalled_sec < CRITICAL_STALL_SEC:
            continue
        # 检查是否已发送过严重停滞通知（防止重复通知）
        last_critical = sched.get('lastCriticalNotifyAt')
        if last_critical:
            last_critical_dt = _parse_iso(last_critical)
            if last_critical_dt and (now_dt - last_critical_dt).total_seconds() < _PERIODIC_RENOTIFY_SEC:
                continue
        sched['lastCriticalNotifyAt'] = now_iso()
        sched['escalationLevel'] = 2  # 强制升级到最高
        task_title = task.get('title', '(无标题)')
        state_label = _SUPERVISION_CONFIG.get(state, {}).get('label', state)
        _scheduler_add_flow(task, f'严重停滞{stalled_sec // 60}分钟，强制升级通知太子', to=state_label)
        changed = True
        critical_msg = (
            f'🚨 严重停滞警告\n'
            f'任务ID: {task_id}\n'
            f'任务标题: {task_title}\n'
            f'停滞部门: {state_label}\n'
            f'已停滞: {stalled_sec // 60} 分钟（超过2小时阈值）\n\n'
            f'太子请立即介入：\n'
            f'1. 检查 {state_label} 当前状态和在线情况\n'
            f'2. 考虑使用 scheduler-rollback 回滚任务\n'
            f'3. 或使用 scheduler-escalate 手动升级处理\n'
            f'4. 必要时直接唤醒停滞部门\n\n'
            f'⚠️ 看板已有此任务，请勿重复创建。'
        )
        wake_agent('taizi', critical_msg)
        actions.append({'taskId': task_id, 'action': 'critical_stall', 'stalledSec': stalled_sec})

    # ── 周期性重报：对超时上报后仍无进展的任务，每 _PERIODIC_RENOTIFY_SEC 重新通知 ──
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        reported = sched.get('timeoutReportedAt')
        if not reported:
            continue
        reported_dt = _parse_iso(reported)
        if not reported_dt:
            continue
        elapsed_since_report = (now_dt - reported_dt).total_seconds()
        if elapsed_since_report < _PERIODIC_RENOTIFY_SEC:
            continue
        # 检查是否有新进展（如果有则不需要重报）
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if last_progress and last_progress > reported_dt:
            continue  # 有新进展，不需要重报
        # 重置超时标记，允许下一轮重新触发超时上报
        sched['timeoutReportedAt'] = None
        sched['remindedAt'] = None
        task['updatedAt'] = now_iso()
        changed = True
        task_title = task.get('title', '(无标题)')
        state_label = _SUPERVISION_CONFIG.get(state, {}).get('label', state)
        log.info(f'🔄 周期性重报: {task_id} ({state_label}) 停滞超过 {_PERIODIC_RENOTIFY_SEC // 60} 分钟，重置通知标记')

    # ── 新增：分级催办（针对性消息，直接发送到停滞部门） ──
    for task_id, state, remind_agent, state_label, stalled_sec, remind_sec in pending_reminds:
        task = next((t for t in tasks if t.get('id') == task_id), None)
        task_title = task.get('title', '(无标题)') if task else '(无标题)'

        # 确定实际停滞的 agent（谁需要收到催办）
        stalled_agent = _STATE_AGENT_MAP.get(state, '')
        if not stalled_agent and state in ('Doing', 'Next'):
            stalled_agent = _ORG_AGENT_MAP.get(task.get('org', ''), '') if task else ''

        # 🎯 使用该部门的针对性催办模板
        remind_label = _AGENT_LABELS.get(remind_agent, remind_agent)
        template = _AGENT_REMIND_TEMPLATES.get(stalled_agent, _DEFAULT_REMIND_TEMPLATE)
        msg = template.format(
            task_id=task_id,
            task_title=task_title,
            stalled_min=stalled_sec // 60,
            parent_dept=remind_label,
        )

        # 直接向停滞部门发送针对性催办
        if stalled_agent:
            wake_agent(stalled_agent, msg)
            log.info(f'🎯 已发送【针对性催办】给 {_AGENT_LABELS.get(stalled_agent, stalled_agent)} | 任务 {task_id}')
        else:
            # 回退：发送给催办负责人
            wake_agent(remind_agent, msg)

    # ── 新增：超时上报太子（针对性模板） ──
    for task_id, state, state_label, stalled_sec, parent_agent in pending_timeouts:
        task = next((t for t in tasks if t.get('id') == task_id), None)
        task_title = task.get('title', '(无标题)') if task else '(无标题)'
        parent_label = _AGENT_LABELS.get(parent_agent, parent_agent)
        msg = _TIMEOUT_REPORT_TEMPLATE.format(
            task_id=task_id,
            task_title=task_title,
            state_label=state_label,
            parent_agent_label=parent_label,
            stalled_min=stalled_sec // 60,
        )
        wake_agent('taizi', msg)

    for task_id, state in pending_rollbacks:
        rollback_task = next((t for t in tasks if t.get('id') == task_id), None)
        if rollback_task and state not in _TERMINAL_STATES:
            dispatch_for_state(task_id, rollback_task, state, trigger='taizi-auto-rollback')

    # ── F1 修复：queued-shangshu-busy 重试 ──
    # 尚书省忙碌时任务被标记为 queued-shangshu-busy，但原逻辑只恢复 'queued'，
    # 导致这些任务永久卡住。超过 120 秒后自动重试派发。
    _QUEUED_SHANGSHU_BUSY_TIMEOUT = 120
    _now_dt = datetime.datetime.now(datetime.timezone.utc)
    pending_busy_retries = []
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') != 'queued-shangshu-busy':
            continue
        last_dispatch = sched.get('lastDispatchAt', '')
        if not last_dispatch:
            continue
        last_dt = _parse_iso(last_dispatch)
        if not last_dt:
            continue
        busy_sec = int((_now_dt - last_dt).total_seconds())
        if busy_sec < _QUEUED_SHANGSHU_BUSY_TIMEOUT:
            continue
        log.info(f'🔄 F1重试: {task_id} 尚书省排队超时{busy_sec}秒，重新派发')
        sched['lastDispatchStatus'] = 'retry-queued'
        sched['lastDispatchTrigger'] = 'scheduler-retry-queued'
        task['updatedAt'] = now_iso()
        changed = True
        actions.append({'taskId': task_id, 'action': 'retry-queued-busy', 'busySec': busy_sec})
        pending_busy_retries.append((task_id, state))

    if changed:
        save_tasks(tasks)

    # 处理 F1 产生的重试（独立列表，不影响之前的 pending_retries）
    for task_id, state in pending_busy_retries:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task:
            dispatch_for_state(task_id, retry_task, state, trigger='scheduler-retry-queued')

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'count': len(actions),
        'checkedAt': now_iso(),
    }


def _startup_recover_queued_dispatches():
    """服务启动后扫描 lastDispatchStatus=queued 的任务，重新派发。
    解决：kill -9 重启导致派发线程中断、任务永久卡住的问题。"""
    tasks = load_tasks()
    recovered = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') == 'queued':
            log.info(f'🔄 启动恢复: {task_id} 状态={state} 上次派发未完成，重新派发')
            sched['lastDispatchTrigger'] = 'startup-recovery'
            dispatch_for_state(task_id, task, state, trigger='startup-recovery')
            recovered += 1
    if recovered:
        log.info(f'✅ 启动恢复完成: 重新派发 {recovered} 个任务')
    else:
        log.info(f'✅ 启动恢复: 无需恢复')

    # 启动时扫描长期停滞任务（>2小时），立即通知太子
    try:
        _startup_check_long_stalled()
    except Exception as e:
        log.warning(f'启动停滞扫描异常: {e}')


def _startup_check_long_stalled():
    """服务启动后扫描长期停滞任务（>2小时），通知太子介入。"""
    CRITICAL_SEC = 7200  # 2 小时
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    tasks = load_tasks()
    notified = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        if stalled_sec < CRITICAL_SEC:
            continue
        # 避免重复通知
        if sched.get('lastCriticalNotifyAt'):
            continue
        sched['lastCriticalNotifyAt'] = now_iso()
        task['updatedAt'] = now_iso()
        task_title = task.get('title', '(无标题)')
        state_label = _SUPERVISION_CONFIG.get(state, {}).get('label', state)
        msg = (
            f'🚨 启动检测：严重停滞警告\n'
            f'任务ID: {task_id}\n'
            f'任务标题: {task_title}\n'
            f'停滞部门: {state_label}\n'
            f'已停滞: {stalled_sec // 60} 分钟\n\n'
            f'此任务在服务重启前已长期停滞，请太子立即介入处理。\n'
            f'⚠️ 看板已有此任务，请勿重复创建。'
        )
        wake_agent('taizi', msg)
        notified += 1
    if notified:
        save_tasks(tasks)
        log.info(f'🚨 启动停滞扫描: 发现 {notified} 个长期停滞任务，已通知太子')


def handle_repair_flow_order():
    """修复历史任务中首条流转为“皇上->中书省”的错序问题。"""
    tasks = load_tasks()
    fixed = 0
    fixed_ids = []

    for task in tasks:
        task_id = task.get('id', '')
        if not task_id.startswith('JJC-'):
            continue
        flow_log = task.get('flow_log') or []
        if not flow_log:
            continue

        first = flow_log[0]
        if first.get('from') != '皇上' or first.get('to') != '中书省':
            continue

        first['to'] = '太子'
        remark = first.get('remark', '')
        if isinstance(remark, str) and remark.startswith('下旨：'):
            first['remark'] = remark

        if task.get('state') == 'Zhongshu' and task.get('org') == '中书省' and len(flow_log) == 1:
            task['state'] = 'Taizi'
            task['org'] = '太子'
            task['now'] = '等待太子接旨分拣'

        task['updatedAt'] = now_iso()
        fixed += 1
        fixed_ids.append(task_id)

    if fixed:
        save_tasks(tasks)

    return {
        'ok': True,
        'count': fixed,
        'taskIds': fixed_ids[:80],
        'more': max(0, fixed - 80),
        'checkedAt': now_iso(),
    }


def _collect_message_text(msg):
    """收集消息中的可检索文本，用于 task_id/关键词过滤。"""
    parts = []
    for c in msg.get('content', []) or []:
        ctype = c.get('type')
        if ctype == 'text' and c.get('text'):
            parts.append(str(c.get('text', '')))
        elif ctype == 'thinking' and c.get('thinking'):
            parts.append(str(c.get('thinking', '')))
        elif ctype == 'tool_use':
            parts.append(json.dumps(c.get('input', {}), ensure_ascii=False))
    details = msg.get('details') or {}
    for key in ('output', 'stdout', 'stderr', 'message'):
        val = details.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return ''.join(parts)


def _parse_activity_entry(item, agent_id=''):
    """将 session jsonl 的 message 统一解析成看板活动条目。"""
    msg = item.get('message') or {}
    role = str(msg.get('role', '')).strip().lower()
    ts = item.get('timestamp', '')

    if role == 'assistant':
        text = ''
        thinking = ''
        tool_calls = []
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text') and not text:
                text = str(c.get('text', '')).strip()
            elif c.get('type') == 'thinking' and c.get('thinking') and not thinking:
                thinking = str(c.get('thinking', '')).strip()[:200]
            elif c.get('type') == 'tool_use':
                tool_calls.append({
                    'name': c.get('name', ''),
                    'input_preview': json.dumps(c.get('input', {}), ensure_ascii=False)[:100]
                })
        if not (text or thinking or tool_calls):
            return None
        entry = {'at': ts, 'kind': 'assistant', 'agent': agent_id}
        if text:
            entry['text'] = text[:300]
        if thinking:
            entry['thinking'] = thinking
        if tool_calls:
            entry['tools'] = tool_calls
        return entry

    if role in ('toolresult', 'tool_result'):
        details = msg.get('details') or {}
        code = details.get('exitCode')
        if code is None:
            code = details.get('code', details.get('status'))
        output = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                output = str(c.get('text', '')).strip()[:200]
                break
        if not output:
            for key in ('output', 'stdout', 'stderr', 'message'):
                val = details.get(key)
                if isinstance(val, str) and val.strip():
                    output = val.strip()[:200]
                    break

        entry = {
            'at': ts,
            'kind': 'tool_result',
            'agent': agent_id,
            'tool': msg.get('toolName', msg.get('name', '')),
            'exitCode': code,
            'output': output,
        }
        duration_ms = details.get('durationMs')
        if isinstance(duration_ms, (int, float)):
            entry['durationMs'] = int(duration_ms)
        return entry

    if role == 'user':
        text = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                text = str(c.get('text', '')).strip()
                break
        if not text:
            return None
        return {'at': ts, 'kind': 'user', 'agent': agent_id, 'text': text[:200]}

    return None


def get_agent_activity(agent_id, limit=30, task_id=None):
    """从 Agent 的 session jsonl 读取最近活动。
    如果 task_id 不为空，只返回提及该 task_id 的相关条目。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    # 扫描所有 jsonl（按修改时间倒序），优先最新
    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    entries = []
    # 如果需要按 task_id 过滤，可能需要扫描多个文件
    files_to_scan = jsonl_files[:3] if task_id else jsonl_files[:1]

    for session_file in files_to_scan:
        try:
            lines = session_file.read_text(errors='ignore').splitlines()
        except Exception:
            continue

        # 正向扫描以保持时间顺序；如果有 task_id，收集提及 task_id 的条目
        for ln in lines:
            try:
                item = json.loads(ln)
            except Exception:
                continue
            msg = item.get('message') or {}
            all_text = _collect_message_text(msg)

            # task_id 过滤：只保留提及 task_id 的条目
            if task_id and task_id not in all_text:
                continue
            entry = _parse_activity_entry(item, agent_id)
            if entry:
                entries.append(entry)

            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    # 只保留最后 limit 条
    return entries[-limit:]


def _extract_keywords(title):
    """从任务标题中提取有意义的关键词（用于 session 内容匹配）。"""
    stop = {'的', '了', '在', '是', '有', '和', '与', '或', '一个', '一篇', '关于', '进行',
            '写', '做', '请', '把', '给', '用', '要', '需要', '面向', '风格', '包含',
            '出', '个', '不', '可以', '应该', '如何', '怎么', '什么', '这个', '那个'}
    # 提取英文词
    en_words = re.findall(r'[a-zA-Z][\w.-]{1,}', title)
    # 提取 2-4 字中文词组（更短的颗粒度）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', title)
    all_words = en_words + cn_words
    kws = [w for w in all_words if w not in stop and len(w) >= 2]
    # 去重保序
    seen = set()
    unique = []
    for w in kws:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique.append(w)
    return unique[:8]  # 最多 8 个关键词


def get_agent_activity_by_keywords(agent_id, keywords, limit=20):
    """从 agent session 中按关键词匹配获取活动条目。
    找到包含关键词的 session 文件，只读该文件的活动。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 找到包含关键词的 session 文件
    target_file = None
    for sf in jsonl_files[:5]:
        try:
            content = sf.read_text(errors='ignore')
        except Exception:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in content.lower())
        if hits >= min(2, len(keywords)):
            target_file = sf
            break

    if not target_file:
        return []

    # 解析 session 文件，按 user 消息分割为对话段
    # 找到包含关键词的对话段，只返回该段的活动
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 第一遍：找到关键词匹配的 user 消息位置
    user_msg_indices = []  # (line_index, user_text)
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            text = ''
            for c in msg.get('content', []):
                if c.get('type') == 'text' and c.get('text'):
                    text += c['text']
            user_msg_indices.append((i, text))

    # 找到与关键词匹配度最高的 user 消息
    best_idx = -1
    best_hits = 0
    for line_idx, utext in user_msg_indices:
        hits = sum(1 for kw in keywords if kw.lower() in utext.lower())
        if hits > best_hits:
            best_hits = hits
            best_idx = line_idx

    # 确定对话段的行范围：从匹配的 user 消息到下一个 user 消息之前
    if best_idx >= 0 and best_hits >= min(2, len(keywords)):
        # 找下一个 user 消息的位置
        next_user_idx = len(lines)
        for line_idx, _ in user_msg_indices:
            if line_idx > best_idx:
                next_user_idx = line_idx
                break
        start_line = best_idx
        end_line = next_user_idx
    else:
        # 没找到匹配的对话段，返回空
        return []

    # 第二遍：只解析对话段内的行
    entries = []
    for ln in lines[start_line:end_line]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item, agent_id)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def get_agent_latest_segment(agent_id, limit=20):
    """获取 Agent 最新一轮对话段（最后一条 user 消息起的所有内容）。
    用于活跃任务没有精确匹配时，展示 Agent 的实时工作状态。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 读取最新的 session 文件
    target_file = jsonl_files[0]
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 找到最后一条 user 消息的行号
    last_user_idx = -1
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            last_user_idx = i

    if last_user_idx < 0:
        return []

    # 从最后一条 user 消息开始，解析到文件末尾
    entries = []
    for ln in lines[last_user_idx:]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item, agent_id)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def _compute_phase_durations(flow_log, task_state=''):
    """从 flow_log 计算每个阶段的停留时长。"""
    if not flow_log or len(flow_log) < 1:
        return []
    phases = []
    for i, fl in enumerate(flow_log):
        start_at = fl.get('at', '')
        to_dept = fl.get('to', '')
        remark = fl.get('remark', '')
        # 下一阶段的起始时间就是本阶段的结束时间
        if i + 1 < len(flow_log):
            end_at = flow_log[i + 1].get('at', '')
            ongoing = False
        elif task_state in ('Done', 'Cancelled'):
            # 终态任务：最后一条流转不再计时
            end_at = start_at
            ongoing = False
        else:
            end_at = now_iso()
            ongoing = True
        # 计算时长
        dur_sec = 0
        try:
            from_dt = datetime.datetime.fromisoformat(start_at.replace('Z', '+00:00'))
            to_dt = datetime.datetime.fromisoformat(end_at.replace('Z', '+00:00'))
            dur_sec = max(0, int((to_dt - from_dt).total_seconds()))
        except Exception:
            pass
        # 人类可读时长
        if dur_sec < 60:
            dur_text = f'{dur_sec}秒'
        elif dur_sec < 3600:
            dur_text = f'{dur_sec // 60}分{dur_sec % 60}秒'
        elif dur_sec < 86400:
            h, rem = divmod(dur_sec, 3600)
            dur_text = f'{h}小时{rem // 60}分'
        else:
            d, rem = divmod(dur_sec, 86400)
            dur_text = f'{d}天{rem // 3600}小时'
        phases.append({
            'phase': to_dept,
            'from': start_at,
            'to': end_at,
            'durationSec': dur_sec,
            'durationText': dur_text,
            'ongoing': ongoing,
            'remark': remark,
        })
    return phases


def _compute_todos_summary(todos):
    """计算 todos 完成率汇总。"""
    if not todos:
        return None
    total = len(todos)
    completed = sum(1 for t in todos if t.get('status') == 'completed')
    in_progress = sum(1 for t in todos if t.get('status') == 'in-progress')
    not_started = total - completed - in_progress
    percent = round(completed / total * 100) if total else 0
    return {
        'total': total,
        'completed': completed,
        'inProgress': in_progress,
        'notStarted': not_started,
        'percent': percent,
    }


def _compute_todos_diff(prev_todos, curr_todos):
    """计算两个 todos 快照之间的差异。"""
    prev_map = {str(t.get('id', '')): t for t in (prev_todos or [])}
    curr_map = {str(t.get('id', '')): t for t in (curr_todos or [])}
    changed, added, removed = [], [], []
    for tid, ct in curr_map.items():
        if tid in prev_map:
            pt = prev_map[tid]
            if pt.get('status') != ct.get('status'):
                changed.append({
                    'id': tid, 'title': ct.get('title', ''),
                    'from': pt.get('status', ''), 'to': ct.get('status', ''),
                })
        else:
            added.append({'id': tid, 'title': ct.get('title', '')})
    for tid, pt in prev_map.items():
        if tid not in curr_map:
            removed.append({'id': tid, 'title': pt.get('title', '')})
    if not changed and not added and not removed:
        return None
    return {'changed': changed, 'added': added, 'removed': removed}


def get_task_activity(task_id):
    """获取任务的实时进展数据。
    数据来源：
    1. 任务自身的 now / todos / flow_log 字段（由 Agent 通过 progress 命令主动上报）
    2. Agent session JSONL 中的对话日志（thinking / tool_result / user，用于展示思考过程）

    增强字段:
    - taskMeta: 任务元信息 (title/state/org/output/block/priority/reviewRound/archived)
    - phaseDurations: 各阶段停留时长
    - todosSummary: todos 完成率汇总
    - resourceSummary: Agent 资源消耗汇总 (tokens/cost/elapsed)
    - activity 条目中 progress/todos 保留 state/org 快照
    - activity 中 todos 条目含 diff 字段
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    state = task.get('state', '')
    org = task.get('org', '')
    now_text = task.get('now', '')
    todos = task.get('todos', [])
    updated_at = task.get('updatedAt', '')

    # ── 任务元信息 ──
    task_meta = {
        'title': task.get('title', ''),
        'state': state,
        'org': org,
        'output': task.get('output', ''),
        'block': task.get('block', ''),
        'priority': task.get('priority', 'normal'),
        'reviewRound': task.get('review_round', 0),
        'archived': task.get('archived', False),
    }

    # 当前负责 Agent（兼容旧逻辑）
    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        agent_id = _ORG_AGENT_MAP.get(org)

    # ── 构建活动条目列表（flow_log + progress_log）──
    activity = []
    flow_log = task.get('flow_log', [])

    # 1. flow_log 转为活动条目
    for fl in flow_log:
        activity.append({
            'at': fl.get('at', ''),
            'kind': 'flow',
            'from': fl.get('from', ''),
            'to': fl.get('to', ''),
            'remark': fl.get('remark', ''),
        })

    progress_log = task.get('progress_log', [])
    related_agents = set()
    # 【G3】从 flow_log 补充历史参与部门，防止转交后 session 日志消失
    _FLOW_DEPT_MAP = {'太子': 'taizi', '皇上': 'huangshang', **_ORG_AGENT_MAP}
    for fl in flow_log:
        for _name, _aid in _FLOW_DEPT_MAP.items():
            if _aid and _name in (fl.get('from', ''), fl.get('to', '')):
                related_agents.add(_aid)

    # 资源消耗累加
    total_tokens = 0
    total_cost = 0.0
    total_elapsed = 0
    has_resource_data = False

    # 用于 todos diff 计算
    prev_todos_snapshot = None

    if progress_log:
        # 2. 多 Agent 实时进展日志（每条 progress 都保留自己的 todo 快照）
        for pl in progress_log:
            p_at = pl.get('at', '')
            p_agent = pl.get('agent', '')
            p_text = pl.get('text', '')
            p_todos = pl.get('todos', [])
            p_state = pl.get('state', '')
            p_org = pl.get('org', '')
            if p_agent:
                related_agents.add(p_agent)
            # 累加资源消耗
            if pl.get('tokens'):
                total_tokens += pl['tokens']
                has_resource_data = True
            if pl.get('cost'):
                total_cost += pl['cost']
                has_resource_data = True
            if pl.get('elapsed'):
                total_elapsed += pl['elapsed']
                has_resource_data = True
            if p_text:
                entry = {
                    'at': p_at,
                    'kind': 'progress',
                    'text': p_text,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 单条资源数据
                if pl.get('tokens'):
                    entry['tokens'] = pl['tokens']
                if pl.get('cost'):
                    entry['cost'] = pl['cost']
                if pl.get('elapsed'):
                    entry['elapsed'] = pl['elapsed']
                activity.append(entry)
            if p_todos:
                todos_entry = {
                    'at': p_at,
                    'kind': 'todos',
                    'items': p_todos,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 计算 diff
                diff = _compute_todos_diff(prev_todos_snapshot, p_todos)
                if diff:
                    todos_entry['diff'] = diff
                activity.append(todos_entry)
                prev_todos_snapshot = p_todos

        # 仅当无法通过状态确定 Agent 时，才回退到最后一次上报的 Agent
        if not agent_id:
            last_pl = progress_log[-1]
            if last_pl.get('agent'):
                agent_id = last_pl.get('agent')
    else:
        # 兼容旧数据：仅使用 now/todos
        if now_text:
            activity.append({
                'at': updated_at,
                'kind': 'progress',
                'text': now_text,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })
        if todos:
            activity.append({
                'at': updated_at,
                'kind': 'todos',
                'items': todos,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })

    # 按时间排序，保证流转/进展穿插正确
    activity.sort(key=lambda x: x.get('at', ''))

    if agent_id:
        related_agents.add(agent_id)

    # ── 融合 Agent Session 活动（thinking / tool_result / user）──
    # 从 session JSONL 中提取 Agent 的思考过程和工具调用记录
    try:
        session_entries = []
        # 活跃任务：尝试按 task_id 精确匹配
        if state not in ('Done', 'Cancelled'):
            if agent_id:
                entries = get_agent_activity(agent_id, limit=30, task_id=task_id)
                session_entries.extend(entries)
            # 也从其他相关 Agent 获取
            for ra in related_agents:
                if ra != agent_id:
                    entries = get_agent_activity(ra, limit=20, task_id=task_id)
                    session_entries.extend(entries)
        else:
            # 已完成任务：基于关键词匹配
            title = task.get('title', '')
            keywords = _extract_keywords(title)
            if keywords:
                agents_to_scan = list(related_agents) if related_agents else ([agent_id] if agent_id else [])
                for ra in agents_to_scan[:5]:
                    entries = get_agent_activity_by_keywords(ra, keywords, limit=15)
                    session_entries.extend(entries)
        # 去重（通过 at+kind 去重避免重复）
        existing_keys = {(a.get('at', ''), a.get('kind', '')) for a in activity}
        for se in session_entries:
            key = (se.get('at', ''), se.get('kind', ''))
            if key not in existing_keys:
                activity.append(se)
                existing_keys.add(key)
        # 重新排序
        activity.sort(key=lambda x: x.get('at', ''))
    except Exception as e:
        log.warning(f'Session JSONL 融合失败 (task={task_id}): {e}')

    # ── 阶段耗时统计 ──
    phase_durations = _compute_phase_durations(flow_log, state)

    # ── Todos 汇总 ──
    todos_summary = _compute_todos_summary(todos)

    # ── 总耗时（首条 flow_log 到最后一条/当前） ──
    total_duration = None
    if flow_log:
        try:
            first_at = datetime.datetime.fromisoformat(flow_log[0].get('at', '').replace('Z', '+00:00'))
            if state in ('Done', 'Cancelled') and len(flow_log) >= 2:
                last_at = datetime.datetime.fromisoformat(flow_log[-1].get('at', '').replace('Z', '+00:00'))
            else:
                last_at = datetime.datetime.now(datetime.timezone.utc)
            dur = max(0, int((last_at - first_at).total_seconds()))
            if dur < 60:
                total_duration = f'{dur}秒'
            elif dur < 3600:
                total_duration = f'{dur // 60}分{dur % 60}秒'
            elif dur < 86400:
                h, rem = divmod(dur, 3600)
                total_duration = f'{h}小时{rem // 60}分'
            else:
                d, rem = divmod(dur, 86400)
                total_duration = f'{d}天{rem // 3600}小时'
        except Exception:
            pass

    result = {
        'ok': True,
        'taskId': task_id,
        'taskMeta': task_meta,
        'agentId': agent_id,
        'agentLabel': _STATE_LABELS.get(state, state),
        'lastActive': updated_at[:19].replace('T', ' ') if updated_at else None,
        'activity': activity,
        'activitySource': 'progress+session',
        'relatedAgents': sorted(list(related_agents)),
        'phaseDurations': phase_durations,
        'totalDuration': total_duration,
    }
    if todos_summary:
        result['todosSummary'] = todos_summary
    if has_resource_data:
        result['resourceSummary'] = {
            'totalTokens': total_tokens,
            'totalCost': round(total_cost, 4),
            'totalElapsedSec': total_elapsed,
        }
    return result


# 状态推进顺序（手动推进用）
_STATE_FLOW = {
    'Pending':  ('Taizi', '皇上', '太子', '待处理旨意转交太子分拣'),
    'Taizi':    ('Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'),
    'Zhongshu': ('Menxia', '中书省', '门下省', '中书省方案提交门下省审议'),
    'Menxia':   ('Assigned', '门下省', '尚书省', '门下省准奏，转尚书省派发'),
    'Assigned': ('Doing', '尚书省', '六部', '尚书省开始派发执行'),
    'Next':     ('Doing', '尚书省', '六部', '待执行任务开始执行'),
    'Doing':    ('Review', '六部', '尚书省', '各部完成，进入汇总'),
    'Review':   ('Done', '尚书省', '太子', '全流程完成，回奏太子转报皇上'),
    # 【V7 修复】Blocked 状态的手动推进路径
    'Blocked':  ('Doing', '太子', '解除阻塞，恢复执行'),
}
_STATE_LABELS = {
    'Pending': '待处理', 'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
    'Assigned': '尚书省', 'Next': '待执行', 'Doing': '执行中', 'Review': '审查', 'Done': '完成',
}


def _build_shangshu_dispatch_msg(task_id, title, target_dept=''):
    """构造尚书省派发消息，包含 dispatch_plan 中的完整方案。"""
    msg_parts = [
        f'📮 门下省已准奏（中书省已确认），请派发执行',
        f'任务ID: {task_id}',
        f'旨意: {title}',
    ]
    if target_dept:
        msg_parts.append(f'建议派发部门: {target_dept}')
    msg_parts.append(f'⚠️ 看板已有此任务，请勿重复创建。')
    msg_parts.append(f'请分析方案并通过 sessions_spawn 派发给六部执行。')

    # 从 dispatch_plan 读取完整方案
    try:
        tasks = load_tasks()
        t = next((t for t in tasks if t.get('id') == task_id), None)
        if t:
            plan = t.get('dispatch_plan', {})
            full_plan = plan.get('full_plan', '')
            if full_plan:
                if len(full_plan) > 4000:
                    full_plan = full_plan[:4000] + '\n...(方案过长，请通过 kanban_update.py dispatch-plan lookup 查看完整方案)'
                msg_parts.append(f'\n📋 完整方案：\n{full_plan}')
            else:
                msg_parts.append(f'\n⚠️ 看板中未找到完整方案，请执行：kanban_update.py dispatch-plan lookup {task_id}')
    except Exception:
        pass

    return '\n'.join(msg_parts)


def dispatch_for_state(task_id, task, new_state, trigger='state-transition'):
    """推进/审批后自动派发对应 Agent（后台异步，不阻塞响应）。"""
    agent_id = _STATE_AGENT_MAP.get(new_state)
    if agent_id is None and new_state in ('Doing', 'Next'):
        org = task.get('org', '')
        agent_id = _ORG_AGENT_MAP.get(org)
        # Fix: if org is generic '六部' or '执行中', try targetDept
        if not agent_id:
            target_dept = task.get('targetDept', '')
            if target_dept:
                agent_id = _ORG_AGENT_MAP.get(target_dept)
    if not agent_id:
        # Issue #1 fix: 当无法确定具体六部 agent 时，记录详细日志帮助排查
        log.warning(f'⚠️ {task_id} 新状态 {new_state} 无法确定目标 Agent（org={task.get("org","")}, targetDept={task.get("targetDept","")}），跳过自动派发。尚书省需在旨意中明确指定 targetDept。')
        return

    # 【F5】尚书省分流：如果任务活跃Agent是六部，说明六部正在执行，
    # 不应向尚书省发完整方案消息。改为轻量提醒。
    _LIU_BU_IDS = ('libu', 'hubu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr')
    if agent_id == 'shangshu' and task.get('activeAgent', '') in _LIU_BU_IDS:
        _active_label = {'libu':'礼部','hubu':'户部','bingbu':'兵部','xingbu':'刑部','gongbu':'工部','libu_hr':'吏部'}.get(task['activeAgent'], task['activeAgent'])
        log.info(f'🔗 F5分流: {task_id} dispatch_for_state 跳过尚书省完整派发（{_active_label}工作中），改为轻量提醒')
        try:
            subprocess.Popen(
                ['openclaw', 'agent', '--agent', 'shangshu', '-m',
                 f'【{task_id}】{_active_label}有进展，请关注任务进度', '--timeout', '60'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except Exception as e:
            log.warning(f'F5轻量通知失败: {e}')
        return

    # 【R1 尚书省空闲检测】
    # 尚书省是 main agent，同一时间只能处理一个任务。
    # 如果尚书省已有活跃任务（Assigned/Doing/Review 状态），延迟/排队不并发通知。
    if agent_id == 'shangshu':
        try:
            all_tasks = load_tasks()
            shangshu_active = [
                t for t in all_tasks
                if t.get('id') != task_id
                and t.get('state') in ('Assigned', 'Doing', 'Review')
                and t.get('org') == '尚书省'
                and not t.get('archived')
            ]
            if shangshu_active:
                active_ids = [t.get('id', '') for t in shangshu_active]
                log.info(f'⏳ {task_id} 尚书省正在处理其他任务（{active_ids}），排队等待')
                _update_task_scheduler(task_id, lambda t, s: s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'queued-shangshu-busy',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                }))
                return
        except Exception as e:
            log.warning(f'尚书省空闲检测异常: {e}')

    _update_task_scheduler(task_id, lambda t, s: (
        s.update({
            'lastDispatchAt': now_iso(),
            'lastDispatchStatus': 'queued',
            'lastDispatchAgent': agent_id,
            'lastDispatchTrigger': trigger,
        }),
        _scheduler_add_flow(t, f'已入队派发：{new_state} → {agent_id}（{trigger}）', to=_STATE_LABELS.get(new_state, new_state))
    ))

    title = task.get('title', '(无标题)')
    target_dept = task.get('targetDept', '')

    # 根据 agent_id 构造针对性消息
    _msgs = {
        'taizi': (
            f'📜 皇上旨意需要你处理\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。\n'
            f'请立即转交中书省起草执行方案。'
        ),
        'zhongshu': (
            f'📜 旨意已到中书省，请起草方案\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务记录，请勿重复创建。直接用 kanban_update.py state 更新状态。\n'
            f'请立即起草执行方案，走完完整三省流程（中书起草→门下审议→尚书派发→六部执行）。'
        ),
        'menxia': (
            f'📋 中书省方案提交审议\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'请审议中书省方案，给出准奏或封驳意见。'
        ),
        'shangshu': _build_shangshu_dispatch_msg(task_id, title, target_dept),
    }
    msg = _msgs.get(agent_id, (
        f'📌 请处理任务\n'
        f'任务ID: {task_id}\n'
        f'旨意: {title}\n'
        f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。'
    ))

    def _do_dispatch():
        try:
            if not _check_gateway_alive():
                log.warning(f'⚠️ {task_id} 自动派发跳过: Gateway 未启动')
                _update_task_scheduler(task_id, lambda t, s: s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'gateway-offline',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                }))
                return
            # Fix #139/#182: dispatch channel 可配置；未配置时不传 --deliver 避免
            # "unknown channel: feishu" 错误（非飞书用户）
            _agent_cfg = read_json(DATA / 'agent_config.json', {})
            _channel = (_agent_cfg.get('dispatchChannel') or '').strip()
            cmd = ['openclaw', 'agent', '--agent', agent_id, '-m', msg, '--timeout', '300']
            if _channel:
                cmd.extend(['--deliver', '--channel', _channel])
            max_retries = 2
            err = ''
            for attempt in range(1, max_retries + 1):
                log.info(f'🔄 自动派发 {task_id} → {agent_id} (第{attempt}次)...')
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=310)
                if result.returncode == 0:
                    log.info(f'✅ {task_id} 自动派发成功 → {agent_id}')
                    _update_task_scheduler(task_id, lambda t, s: (
                        s.update({
                            'lastDispatchAt': now_iso(),
                            'lastDispatchStatus': 'success',
                            'lastDispatchAgent': agent_id,
                            'lastDispatchTrigger': trigger,
                            'lastDispatchError': '',
                        }),
                        _scheduler_add_flow(t, f'派发成功：{agent_id}（{trigger}）', to=t.get('org', ''))
                    ))
                    return
                err = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {task_id} 自动派发失败(第{attempt}次): {err}')
                if attempt < max_retries:
                    import time
                    time.sleep(5)
            log.error(f'❌ {task_id} 自动派发最终失败 → {agent_id}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'failed',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': err,
                }),
                _scheduler_add_flow(t, f'派发失败：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))
            # Fix #11: 派发全部失败后，检查是否需要自动回滚
            try:
                def _check_rollback():
                    tasks = load_tasks()
                    t = next((t for t in tasks if t.get('id') == task_id), None)
                    if t:
                        sched = _ensure_scheduler(t)
                        if sched.get('autoRollback', True):
                            retry_count = int(sched.get('retryCount') or 0)
                            max_retry = max(0, int(sched.get('maxRetry') or 1))
                            if retry_count >= max_retry:
                                handle_scheduler_rollback(task_id, f'Agent {agent_id} 派发失败: {err[:100]}')
                threading.Thread(target=_check_rollback, daemon=True).start()
            except Exception as e:
                log.warning(f'派发失败后回滚检查异常: {e}')
        except subprocess.TimeoutExpired:
            log.error(f'❌ {task_id} 自动派发超时 → {agent_id}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'timeout',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': 'timeout',
                }),
                _scheduler_add_flow(t, f'派发超时：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))
        except Exception as e:
            log.warning(f'⚠️ {task_id} 自动派发异常: {e}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'error',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': str(e)[:200],
                }),
                _scheduler_add_flow(t, f'派发异常：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))

    def _dispatch_with_semaphore():
        _dispatch_semaphore.acquire()
        try:
            _do_dispatch()
        finally:
            _dispatch_semaphore.release()

    threading.Thread(target=_dispatch_with_semaphore, daemon=True).start()
    log.info(f'🚀 {task_id} 推进后自动派发 → {agent_id}')


def handle_advance_state(task_id, comment=''):
    """手动推进任务到下一阶段（解卡用），推进后自动派发对应 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    cur = task.get('state', '')
    if cur not in _STATE_FLOW:
        return {'ok': False, 'error': f'任务 {task_id} 状态为 {cur}，无法推进'}
    _ensure_scheduler(task)
    task['_scheduler']['_taiziManual'] = True
    _scheduler_snapshot(task, f'advance-before-{cur}')
    next_state, from_dept, to_dept, default_remark = _STATE_FLOW[cur]
    remark = comment or default_remark

    # Fix: resolve generic '六部' to specific department via targetDept
    if next_state in ('Doing', 'Next') and to_dept == '六部':
        target_dept = task.get('targetDept', '')
        if target_dept and target_dept in _ORG_AGENT_MAP:
            task['org'] = target_dept
            to_dept = target_dept
        else:
            task['org'] = '执行中'
    else:
        task['org'] = _STATE_LABELS.get(next_state, to_dept)

    task['state'] = next_state
    task['now'] = f'⬇️ 手动推进：{remark}'
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': from_dept,
        'to': to_dept,
        'remark': f'⬇️ 手动推进：{remark}'
    })
    _scheduler_mark_progress(task, f'手动推进 {cur} -> {next_state}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    # 🚀 推进后自动派发对应 Agent（Done 状态无需派发）
    if next_state != 'Done':
        dispatch_for_state(task_id, task, next_state)

    from_label = _STATE_LABELS.get(cur, cur)
    to_label = _STATE_LABELS.get(next_state, next_state)
    dispatched = ' (已自动派发 Agent)' if next_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {from_label} → {to_label}{dispatched}'}


# ══════════════════════════════════════════════════════════════════════════════
# [TaskOutput] 产出管理后端 — 函数定义
# 所有代码用 #[TaskOutput] 标注，方便定位和回滚
# ══════════════════════════════════════════════════════════════════════════════

# [TaskOutput] 产出存储根目录（改为 openclaw 统一目录，Docker 持久化）
_OUTPUTS_DIR = OCLAW_HOME / 'outputs'


def _get_output_dir(task_id: str) -> pathlib.Path:
    """获取任务的产出目录路径（自动创建）"""
    if not _SAFE_NAME_RE.match(task_id):
        raise ValueError(f'无效的 task_id: {task_id}')
    return _OUTPUTS_DIR / task_id


def _get_manifest_path(task_id: str) -> pathlib.Path:
    """获取任务的产出清单文件路径"""
    return _get_output_dir(task_id) / 'manifest.json'


def _load_manifest(task_id: str) -> dict:
    """加载产出清单"""
    mpath = _get_manifest_path(task_id)
    if not mpath.exists():
        return {'taskId': task_id, 'artifacts': [], 'totalSize': 0}
    try:
        return atomic_json_read(mpath, {'taskId': task_id, 'artifacts': [], 'totalSize': 0})
    except Exception:
        return {'taskId': task_id, 'artifacts': [], 'totalSize': 0}


def _save_manifest(task_id: str, manifest: dict):
    """保存产出清单"""
    mpath = _get_manifest_path(task_id)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(mpath, manifest)


def _safe_filename(filename: str) -> str:
    """清理文件名，防止路径遍历（仅清理危险字符，保留路径分隔符以支持子目录）"""
    filename = filename.replace('\\', '/')
    parts = pathlib.PurePosixPath(filename).parts
    clean_parts = []
    for p in parts:
        p = p.replace('..', '').strip()
        if p and p != '.' and p != '..':
            clean_parts.append(p)
    clean_name = '/'.join(clean_parts)
    if len(clean_name) > 200:
        clean_name = clean_name[:200]
    return clean_name or 'unnamed'


def _safe_path_search(filename: str) -> str:
    """将用户传入的文件名/路径标准化，用于搜索（保留路径结构）"""
    return filename.replace('\\', '/').strip('./')


def _scan_output_dir(task_id: str) -> list:
    """[TaskOutput] 递归扫描产出目录（支持嵌套子目录），自动生成文件清单"""
    output_dir = _get_output_dir(task_id)
    if not output_dir.exists():
        return []
    artifacts = []
    for f in output_dir.rglob('*'):
        if not f.is_file():
            continue
        if f.name.startswith('.') or f.name == 'manifest.json':
            continue
        rel_path = str(f.relative_to(output_dir))
        parts = rel_path.split(os.sep)
        # dept = 第一级子目录名，根目录文件归为 '未分类'
        dept = parts[0] if len(parts) > 1 else '未分类'
        # subfolder = dept 之后的子目录路径（用于前端树状展示）
        subfolder = '/'.join(parts[1:-1]) if len(parts) > 2 else ''
        artifacts.append({
            'name': f.name,
            'dept': dept,
            'type': f.suffix.lstrip('.').lower(),
            'size': f.stat().st_size,
            'path': rel_path,
            'subfolder': subfolder,
            'uploadedAt': datetime.datetime.fromtimestamp(f.stat().st_mtime, tz=datetime.timezone.utc).isoformat(),
        })
    return artifacts


def handle_output_list(task_id: str) -> dict:
    """获取任务的产出文件列表"""
    try:
        tasks = load_tasks()
        task = next((t for t in tasks if t.get('id') == task_id), None)
        if not task:
            return {'ok': False, 'error': f'任务 {task_id} 不存在'}
        # [TaskOutput] 每次都扫描目录，确保始终返回最新文件列表
        artifacts = _scan_output_dir(task_id)
        totalSize = sum(a.get('size', 0) for a in artifacts)
        if artifacts:
            _save_manifest(task_id, {'taskId': task_id, 'artifacts': artifacts, 'totalSize': totalSize})
        return {
            'ok': True,
            'taskId': task_id,
            'taskTitle': task.get('title', ''),
            'artifacts': artifacts,
            'totalSize': totalSize,
        }
    except Exception as e:
        log.error(f'[TaskOutput] 列表失败: {e}')
        return {'ok': False, 'error': str(e)[:200]}


def handle_output_download(task_id: str, filename: str, handler):
    """下载产出文件（直接返回文件内容）"""
    try:
        if not _SAFE_NAME_RE.match(task_id):
            handler.send_error(400, 'invalid task_id')
            return
        output_dir = _get_output_dir(task_id)
        if not output_dir.exists():
            handler.send_error(404, 'output directory not found')
            return
        search_name = _safe_path_search(filename)
        found_file = None
        # 1. 尝试直接按路径查找（支持嵌套子目录）
        candidate = output_dir / search_name
        if candidate.is_file():
            found_file = candidate
        else:
            # 2. 递归搜索（按文件名匹配，兼容旧路径）
            base_name = pathlib.Path(search_name).name
            for f in output_dir.rglob(base_name):
                if f.is_file():
                    found_file = f
                    break
        if not found_file:
            handler.send_error(404, 'file not found')
            return
        ext = found_file.suffix.lower()
        mime = _MIME_TYPES.get(ext, 'application/octet-stream')
        data = found_file.read_bytes()
        handler.send_response(200)
        cors_headers(handler)
        handler.send_header('Content-Type', mime)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Content-Disposition', f'attachment; filename="{_url_quote(found_file.name)}"')
        handler.end_headers()
        handler.wfile.write(data)
    except Exception as e:
        log.error(f'[TaskOutput] 下载失败: {e}')
        handler.send_error(500, str(e))


def handle_output_preview(task_id: str, filename: str) -> dict:
    """预览产出文件（支持嵌套子目录路径）"""
    try:
        if not _SAFE_NAME_RE.match(task_id):
            return {'ok': False, 'error': 'invalid task_id'}
        output_dir = _get_output_dir(task_id)
        if not output_dir.exists():
            return {'ok': True, 'content': '', 'exists': False}
        search_name = _safe_path_search(filename)
        found_file = None
        # 1. 尝试直接按路径查找
        candidate = output_dir / search_name
        if candidate.is_file():
            found_file = candidate
        else:
            # 2. 递归搜索
            base_name = pathlib.Path(search_name).name
            for f in output_dir.rglob(base_name):
                if f.is_file():
                    found_file = f
                    break
        if not found_file:
            return {'ok': True, 'content': '', 'exists': False}
        size = found_file.stat().st_size
        if size > 100 * 1024:
            return {'ok': False, 'error': f'文件过大（{size/1024:.0f}KB），仅支持预览 100KB 以内的文本文件'}
        content = found_file.read_text(encoding='utf-8', errors='replace')
        return {
            'ok': True,
            'content': content,
            'filename': found_file.name,
            'path': str(found_file.relative_to(output_dir)),
            'size': size,
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}


def handle_output_upload(task_id: str, handler) -> dict:
    """上传产出文件（multipart/form-data）"""
    try:
        if not _SAFE_NAME_RE.match(task_id):
            return {'ok': False, 'error': 'invalid task_id'}
        tasks = load_tasks()
        task = next((t for t in tasks if t.get('id') == task_id), None)
        if not task:
            return {'ok': False, 'error': f'任务 {task_id} 不存在'}
        content_type = handler.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return {'ok': False, 'error': '需要 multipart/form-data 格式'}
        boundary = None
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part.split('=', 1)[1].strip('"')
                break
        if not boundary:
            return {'ok': False, 'error': '缺少 boundary'}
        length = int(handler.headers.get('Content-Length', 0))
        if length > 50 * 1024 * 1024:
            return {'ok': False, 'error': '文件过大（最大 50MB）'}
        body = handler.rfile.read(length) if length else b''
        form = _cgi.FieldStorage(
            fp=_io.BytesIO(body),
            headers=handler.headers,
            environ={
                'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': content_type,
            }
        )
        file_item = form['file']
        dept = form.getvalue('dept', '尚书省')
        if not file_item.filename:
            return {'ok': False, 'error': '未选择文件'}
        output_dir = _get_output_dir(task_id)
        dept_dir = output_dir / dept
        dept_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(file_item.filename)
        file_path = dept_dir / safe_name
        if file_path.exists():
            name, ext = os.path.splitext(safe_name)
            counter = 1
            while file_path.exists():
                file_path = dept_dir / f'{name}_{counter}{ext}'
                counter += 1
            safe_name = file_path.name
        file_size = 0
        if isinstance(file_item.file, (bytes, bytearray)):
            file_data = file_item.file
        else:
            file_data = file_item.file.read()
        with open(file_path, 'wb') as f:
            if isinstance(file_data, bytes):
                f.write(file_data)
                file_size = len(file_data)
            else:
                file_data.seek(0)
                while True:
                    chunk = file_data.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    file_size += len(chunk)
        manifest = _load_manifest(task_id)
        ext = file_path.suffix.lower()
        new_artifact = {
            'name': safe_name,
            'dept': dept,
            'type': ext.lstrip('.'),
            'size': file_size,
            'path': str(file_path.relative_to(output_dir)),
            'uploadedAt': now_iso(),
        }
        manifest.setdefault('artifacts', []).append(new_artifact)
        manifest['totalSize'] = sum(a.get('size', 0) for a in manifest['artifacts'])
        _save_manifest(task_id, manifest)
        if not task.get('output') or task['output'] == '-':
            task['output'] = str(output_dir)
            save_tasks(tasks)
        log.info(f'[TaskOutput] 上传成功: {task_id}/{dept}/{safe_name} ({file_size} bytes)')
        return {'ok': True, 'message': f'文件已上传到 {dept}/{safe_name}'}
    except Exception as e:
        log.error(f'[TaskOutput] 上传失败: {e}')
        return {'ok': False, 'error': str(e)[:200]}


def handle_output_delete(task_id: str, filename: str) -> dict:
    """删除产出文件（支持嵌套子目录路径）"""
    try:
        if not _SAFE_NAME_RE.match(task_id):
            return {'ok': False, 'error': 'invalid task_id'}
        search_name = _safe_path_search(filename)
        output_dir = _get_output_dir(task_id)
        if not output_dir.exists():
            return {'ok': False, 'error': '产出目录不存在'}
        # 查找文件（支持嵌套路径）
        found_file = None
        found_path = None
        candidate = output_dir / search_name
        if candidate.is_file():
            found_file = candidate
            found_path = str(candidate.relative_to(output_dir))
        else:
            base_name = pathlib.Path(search_name).name
            for f in output_dir.rglob(base_name):
                if f.is_file():
                    found_file = f
                    found_path = str(f.relative_to(output_dir))
                    break
        if not found_file:
            return {'ok': False, 'error': f'文件 {filename} 不存在'}
        found_file.unlink()
        # 更新 manifest（按 path 匹配删除）
        manifest = _load_manifest(task_id)
        manifest['artifacts'] = [
            a for a in manifest.get('artifacts', [])
            if a.get('path', '') != found_path and a.get('name', '') != found_file.name
        ]
        manifest['totalSize'] = sum(a.get('size', 0) for a in manifest['artifacts'])
        _save_manifest(task_id, manifest)
        log.info(f'[TaskOutput] 删除成功: {task_id}/{found_path}')
        return {'ok': True, 'message': f'已删除 {found_path}'}
    except Exception as e:
        log.error(f'[TaskOutput] 删除失败: {e}')
        return {'ok': False, 'error': str(e)[:200]}

# ══════════════════════════════════════════════════════════════════════════════


class Handler(BaseHTTPRequestHandler):
    # Problem 4: 连接超时，避免死连接占用线程
    timeout = 30

    def log_message(self, fmt, *args):
        # 只记录 4xx/5xx 错误请求
        if args and len(args) >= 1:
            status = str(args[0]) if args else ''
            if status.startswith('4') or status.startswith('5'):
                log.warning(f'{self.client_address[0]} {fmt % args}')

    def handle_error(self):
        log.warning(f'连接错误: {self.client_address} - {sys.exc_info()[1]}')

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass  # 客户端断开连接或超时，忽略
        except Exception as e:
            log.warning(f'请求处理异常: {e}')

    def do_OPTIONS(self):
        try:
            self.send_response(200)
            cors_headers(self)
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def send_file(self, path: pathlib.Path, mime='text/html; charset=utf-8'):
        if not path.exists():
            self.send_error(404)
            return
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def _serve_static(self, rel_path):
        """从 dist/ 目录提供静态文件。"""
        safe = rel_path.replace('\\', '/').lstrip('/')
        if '..' in safe:
            self.send_error(403)
            return True
        fp = DIST / safe
        if fp.is_file():
            mime = _MIME_TYPES.get(fp.suffix.lower(), 'application/octet-stream')
            self.send_file(fp, mime)
            return True
        return False

    def do_GET(self):
        p = urlparse(self.path).path.rstrip('/')
        if p in ('', '/dashboard', '/dashboard.html'):
            self.send_file(DIST / 'index.html')
        elif p == '/healthz':
            task_data_dir = get_task_data_dir()
            checks = {'dataDir': task_data_dir.is_dir(), 'tasksReadable': (task_data_dir / 'tasks_source.json').exists()}
            checks['dataWritable'] = os.access(str(task_data_dir), os.W_OK)
            all_ok = all(checks.values())
            self.send_json({'status': 'ok' if all_ok else 'degraded', 'ts': now_iso(), 'checks': checks})
        elif p == '/api/live-status':
            task_data_dir = get_task_data_dir()
            self.send_json(read_json(task_data_dir / 'live_status.json'))
        elif p == '/api/agent-config':
            self.send_json(read_json(DATA / 'agent_config.json'))
        elif p == '/api/model-change-log':
            self.send_json(read_json(DATA / 'model_change_log.json', []))
        elif p == '/api/last-result':
            self.send_json(read_json(DATA / 'last_model_change_result.json', {}))
        elif p == '/api/officials-stats':
            self.send_json(read_json(DATA / 'officials_stats.json', {}))
        elif p == '/api/morning-brief':
            self.send_json(read_json(DATA / 'morning_brief.json', {}))
        elif p == '/api/pipeline-audit':
            # 使用文件锁读取（防止与 watchdog 并发写入冲突）
            audit = atomic_json_read(DATA / 'pipeline_audit.json', {
                'last_check': '', 'violations': [], 'watched_tasks': [],
                'watched_count': 0, 'check_count': 0, 'total_violations': 0,
                'notifications': [], 'archived_violations': [],
                'archived_notifications': [],
            })
            # 过滤已取消监察任务的违规、通知和正在监察列表
            try:
                exclude_data = json.loads((DATA / 'audit_exclude.json').read_text())
                excluded = set(exclude_data.get('excluded_tasks', []))
            except Exception:
                excluded = set()
            # 收集已归档任务 ID（用于过滤已归档任务的违规）
            archived_task_ids = set()
            try:
                all_tasks = load_tasks()
                for t in all_tasks:
                    if t.get('archived') and t.get('state') in ('Done', 'Cancelled'):
                        archived_task_ids.add(t.get('id', ''))
            except Exception:
                pass
            if excluded or archived_task_ids:
                filter_ids = excluded | archived_task_ids
                # ── 违规记录：分离活跃与归档，归档记录合并进 archived_violations ──
                # 修复时序空白：归档后 watchdog 尚未运行时，违规记录同时不在
                # violations[] 和 archived_violations[] 中导致记录不可见
                _active_v = []
                _newly_archived_v = []
                for v in audit.get('violations', []):
                    if v.get('task_id', '') in filter_ids:
                        _newly_archived_v.append(v)
                    else:
                        _active_v.append(v)
                audit['violations'] = _active_v
                if _newly_archived_v:
                    _existing_archived_v = audit.get('archived_violations', [])
                    # 去重：避免重复加入已在 archived_violations 中的记录
                    _archived_v_keys = set()
                    for _av in _existing_archived_v:
                        _archived_v_keys.add((_av.get('task_id', ''), _av.get('type', ''), _av.get('flow_index', -1), _av.get('detail', '')))
                    _deduped = []
                    for _nv in _newly_archived_v:
                        _nk = (_nv.get('task_id', ''), _nv.get('type', ''), _nv.get('flow_index', -1), _nv.get('detail', ''))
                        if _nk not in _archived_v_keys:
                            _deduped.append(_nv)
                            _archived_v_keys.add(_nk)
                    if _deduped:
                        audit['archived_violations'] = _existing_archived_v + _deduped

                # ── 通知记录：分离活跃与归档，归档记录合并进 archived_notifications ──
                _active_n = []
                _newly_archived_n = []
                for n in audit.get('notifications', []):
                    _n_tid = n.get('task_id', '')
                    _n_tids = n.get('task_ids') or []
                    # 保留条件：无 task_id（系统通知）或 task_id 不在过滤列表
                    _keep = (not _n_tid or _n_tid not in filter_ids)
                    # 如果有 task_ids 列表，至少有一个不在过滤列表中才保留
                    if _keep and _n_tids:
                        _keep = any(tid not in filter_ids for tid in _n_tids)
                    if _keep:
                        _active_n.append(n)
                    else:
                        _newly_archived_n.append(n)
                audit['notifications'] = _active_n
                if _newly_archived_n:
                    _existing_archived_n = audit.get('archived_notifications', [])
                    # 去重：避免重复加入已在 archived_notifications 中的记录
                    _archived_n_keys = set()
                    for _an in _existing_archived_n:
                        _archived_n_keys.add((_an.get('type', ''), _an.get('detail', ''), _an.get('sent_at', '')))
                    _deduped_n = []
                    for _nn in _newly_archived_n:
                        _nk2 = (_nn.get('type', ''), _nn.get('detail', ''), _nn.get('sent_at', ''))
                        if _nk2 not in _archived_n_keys:
                            _deduped_n.append(_nn)
                            _archived_n_keys.add(_nk2)
                    if _deduped_n:
                        audit['archived_notifications'] = _existing_archived_n + _deduped_n

                # 同时过滤 watched_tasks，确保停止监察后立即从列表中移除
                audit['watched_tasks'] = [
                    w for w in audit.get('watched_tasks', [])
                    if w.get('task_id', '') not in excluded
                ]
                # 重新计算 watched_count
                audit['watched_count'] = len(audit['watched_tasks'])
            self.send_json(audit)
        elif p == '/api/morning-config':
            migrate_notification_config()
            self.send_json(read_json(DATA / 'morning_brief_config.json', {
                'categories': [
                    {'name': '政治', 'enabled': True},
                    {'name': '军事', 'enabled': True},
                    {'name': '经济', 'enabled': True},
                    {'name': 'AI大模型', 'enabled': True},
                ],
                'keywords': [], 'custom_feeds': [],
                'notification': {'enabled': True, 'channel': 'feishu', 'webhook': ''},
            }))
        elif p == '/api/notification-channels':
            self.send_json({'ok': True, 'channels': get_channel_info()})
        elif p.startswith('/api/morning-brief/'):
            date = p.split('/')[-1]
            # 标准化日期格式为 YYYYMMDD（兼容 YYYY-MM-DD 输入）
            date_clean = date.replace('-', '')
            if not date_clean.isdigit() or len(date_clean) != 8:
                self.send_json({'ok': False, 'error': f'日期格式无效: {date}，请使用 YYYYMMDD'}, 400)
                return
            self.send_json(read_json(DATA / f'morning_brief_{date_clean}.json', {}))
        elif p == '/api/remote-skills-list':
            self.send_json(get_remote_skills_list())
        elif p.startswith('/api/skill-content/'):
            # /api/skill-content/{agentId}/{skillName}
            parts = p.replace('/api/skill-content/', '').split('/', 1)
            if len(parts) == 2:
                self.send_json(read_skill_content(parts[0], parts[1]))
            else:
                self.send_json({'ok': False, 'error': 'Usage: /api/skill-content/{agentId}/{skillName}'}, 400)
        elif p.startswith('/api/task-activity/'):
            task_id = p.replace('/api/task-activity/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_task_activity(task_id))
        elif p.startswith('/api/scheduler-state/'):
            task_id = p.replace('/api/scheduler-state/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_state(task_id))
        elif p == '/api/agents-status':
            self.send_json(get_agents_status())
        # [TaskOutput] 产出管理 GET 路由
        elif p.startswith('/api/outputs/'):
            parts = p.split('/')
            # parts: ['', 'api', 'outputs', :taskId, 'download'/'preview', :filename]
            #         [0]    [1]     [2]      [3]        [4]              [5]
            if len(parts) >= 6 and parts[4] == 'download':
                # GET /api/outputs/:taskId/download/:filename
                task_id = unquote(parts[3])
                filename = unquote('/'.join(parts[5:]))
                handle_output_download(task_id, filename, self)
                return
            elif len(parts) >= 6 and parts[4] == 'preview':
                # GET /api/outputs/:taskId/preview/:filename
                task_id = unquote(parts[3])
                filename = unquote('/'.join(parts[5:]))
                self.send_json(handle_output_preview(task_id, filename))
            elif len(parts) >= 4:
                # GET /api/outputs/:taskId
                task_id = unquote(parts[3])
                if not task_id:
                    self.send_json({'ok': False, 'error': 'task_id required'}, 400)
                else:
                    self.send_json(handle_output_list(task_id))
            else:
                self.send_json({'ok': False, 'error': 'invalid path'}, 400)
        elif p.startswith('/api/task-output/'):
            task_id = p.replace('/api/task-output/', '')
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid task_id'}, 400)
            else:
                tasks = load_tasks()
                task = next((t for t in tasks if t.get('id') == task_id), None)
                if not task:
                    self.send_json({'ok': False, 'error': 'task not found'}, 404)
                else:
                    output_path = task.get('output', '')
                    if not output_path or output_path == '-':
                        self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                    else:
                        p_out = pathlib.Path(output_path)
                        if not p_out.exists():
                            self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                        else:
                            try:
                                content = p_out.read_text(encoding='utf-8', errors='replace')[:50000]
                                self.send_json({'ok': True, 'taskId': task_id, 'content': content, 'exists': True})
                            except Exception as e:
                                self.send_json({'ok': False, 'error': f'读取失败: {e}'}, 500)
        elif p.startswith('/api/agent-activity/'):
            agent_id = p.replace('/api/agent-activity/', '')
            if not agent_id or not _SAFE_NAME_RE.match(agent_id):
                self.send_json({'ok': False, 'error': 'invalid agent_id'}, 400)
            else:
                self.send_json({'ok': True, 'agentId': agent_id, 'activity': get_agent_activity(agent_id)})
        # ── 朝堂议政 ──
        elif p == '/api/court-discuss/list':
            self.send_json({'ok': True, 'sessions': cd_list()})
        elif p == '/api/court-discuss/officials':
            self.send_json({'ok': True, 'officials': CD_PROFILES})
        elif p.startswith('/api/court-discuss/session/'):
            sid = p.replace('/api/court-discuss/session/', '')
            data = cd_get(sid)
            self.send_json(data if data else {'ok': False, 'error': 'session not found'}, 200 if data else 404)
        elif p == '/api/court-discuss/fate':
            self.send_json({'ok': True, 'event': cd_fate()})
        elif self._serve_static(p):
            pass  # 已由 _serve_static 处理 (JS/CSS/图片等)
        else:
            # SPA fallback：非 /api/ 路径返回 index.html
            if not p.startswith('/api/'):
                idx = DIST / 'index.html'
                if idx.exists():
                    self.send_file(idx)
                    return
            self.send_error(404)

    def do_POST(self):
        # Issue #6#5: POST 端点 Token 认证
        if not _check_api_auth(self):
            self.send_json({'ok': False, 'error': 'Unauthorized'}, 401)
            return
        p = urlparse(self.path).path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_REQUEST_BODY:
            self.send_json({'ok': False, 'error': f'Request body too large (max {MAX_REQUEST_BODY} bytes)'}, 413)
            return
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            self.send_json({'ok': False, 'error': 'invalid JSON'}, 400)
            return

        if p == '/api/morning-config':
            if not isinstance(body, dict):
                self.send_json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
                return
            allowed_keys = {'categories', 'keywords', 'custom_feeds', 'notification', 'feishu_webhook'}
            unknown = set(body.keys()) - allowed_keys
            if unknown:
                self.send_json({'ok': False, 'error': f'未知字段: {", ".join(unknown)}'}, 400)
                return
            if 'categories' in body and not isinstance(body['categories'], list):
                self.send_json({'ok': False, 'error': 'categories 必须是数组'}, 400)
                return
            if 'keywords' in body and not isinstance(body['keywords'], list):
                self.send_json({'ok': False, 'error': 'keywords 必须是数组'}, 400)
                return
            if 'notification' in body:
                noti = body['notification']
                if not isinstance(noti, dict):
                    self.send_json({'ok': False, 'error': 'notification 必须是对象'}, 400)
                    return
                channel_type = noti.get('channel', 'feishu')
                if channel_type not in NOTIFICATION_CHANNELS:
                    self.send_json({'ok': False, 'error': f'不支持的渠道: {channel_type}'}, 400)
                    return
                webhook = noti.get('webhook', '').strip()
                if webhook:
                    channel_cls = get_channel(channel_type)
                    if channel_cls and not channel_cls.validate_webhook(webhook):
                        self.send_json({'ok': False, 'error': f'{channel_cls.label} Webhook URL 无效'}, 400)
                        return
            webhook_legacy = body.get('feishu_webhook', '').strip()
            if webhook_legacy and 'notification' not in body:
                body['notification'] = {'enabled': True, 'channel': 'feishu', 'webhook': webhook_legacy}
            cfg_path = DATA / 'morning_brief_config.json'
            cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            self.send_json({'ok': True, 'message': '订阅配置已保存'})
            return

        if p == '/api/scheduler-scan':
            threshold_sec = body.get('thresholdSec', 180)
            try:
                result = handle_scheduler_scan(threshold_sec)
                self.send_json(result)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'scheduler scan failed: {e}'}, 500)
            return

        if p == '/api/repair-flow-order':
            try:
                self.send_json(handle_repair_flow_order())
            except Exception as e:
                self.send_json({'ok': False, 'error': f'repair flow order failed: {e}'}, 500)
            return

        if p == '/api/scheduler-retry':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_retry(task_id, reason))
            return

        if p == '/api/scheduler-escalate':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_escalate(task_id, reason))
            return

        if p == '/api/scheduler-rollback':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_rollback(task_id, reason))
            return

        if p == '/api/morning-brief/refresh':
            force = body.get('force', True)  # 从看板手动触发默认强制
            def do_refresh():
                try:
                    cmd = ['python3', str(SCRIPTS / 'fetch_morning_news.py')]
                    if force:
                        cmd.append('--force')
                    subprocess.run(cmd, timeout=120)
                    push_to_feishu()
                except Exception as e:
                    print(f'[refresh error] {e}', file=sys.stderr)
            threading.Thread(target=do_refresh, daemon=True).start()
            self.send_json({'ok': True, 'message': '采集已触发，约30-60秒后刷新'})
            return

        if p == '/api/add-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', body.get('name', '')).strip()
            desc = body.get('description', '').strip() or skill_name
            trigger = body.get('trigger', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = add_skill_to_agent(agent_id, skill_name, desc, trigger)
            self.send_json(result)
            return

        if p == '/api/add-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            source_url = body.get('sourceUrl', '').strip()
            description = body.get('description', '').strip()
            if not agent_id or not skill_name or not source_url:
                self.send_json({'ok': False, 'error': 'agentId, skillName, and sourceUrl required'}, 400)
                return
            result = add_remote_skill(agent_id, skill_name, source_url, description)
            self.send_json(result)
            return

        if p == '/api/remote-skills-list':
            result = get_remote_skills_list()
            self.send_json(result)
            return

        if p == '/api/update-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = update_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/remove-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = remove_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/task-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # stop, cancel, resume
            reason = body.get('reason', '').strip() or f'皇上从看板{action}'
            if not task_id or action not in ('stop', 'cancel', 'resume'):
                self.send_json({'ok': False, 'error': 'taskId and action(stop/cancel/resume) required'}, 400)
                return
            result = handle_task_action(task_id, action, reason)
            self.send_json(result)
            return

        if p == '/api/delete-task':
            task_id = body.get('taskId', '').strip()
            confirm_id = body.get('confirmId', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_delete_task(task_id, confirm_id)
            self.send_json(result)
            return

        # ── Gateway 会话管理 API（基于 OpenClaw 本地文件系统，不依赖 REST API） ──
        if p == '/api/gateway/conversations':
            """列出所有 Agent 的会话（从本地 sessions.json 文件读取）"""
            try:
                from pathlib import Path as _Path
                agents_dir = _Path('/root/.openclaw/agents')
                conversations = []
                for sessions_file in sorted(agents_dir.glob('*/sessions/sessions.json')):
                    agent_id = sessions_file.parent.parent.name
                    try:
                        sessions_data = json.loads(sessions_file.read_text())
                    except Exception:
                        continue
                    if not isinstance(sessions_data, dict):
                        continue
                    for skey, sval in sessions_data.items():
                        if not isinstance(sval, dict):
                            continue
                        sid = sval.get('sessionId', skey)
                        updated = sval.get('updatedAt', 0)
                        session_file = sval.get('sessionFile', '')
                        is_main = ':main' in skey.lower() or 'main' in str(sid).lower()
                        conversations.append({
                            'id': sid,
                            'agent_id': agent_id,
                            'title': skey,
                            'updatedAt': updated,
                            'sessionFile': session_file,
                            'isMain': is_main,
                            'key': skey,
                        })
                conversations.sort(key=lambda x: x.get('updatedAt', 0), reverse=True)
                self.send_json({
                    'ok': True,
                    'conversations': conversations,
                    'total': len(conversations),
                })
            except Exception as e:
                self.send_json({'ok': False, 'error': f'读取会话失败: {str(e)[:100]}'}, 500)
            return

        if p.startswith('/api/gateway/conversation/') and p.endswith('/delete'):
            """删除指定会话（从本地 sessions.json 中移除并删除 .jsonl 文件）"""
            conv_id = p.replace('/api/gateway/conversation/', '').replace('/delete', '').strip()
            if not conv_id:
                self.send_json({'ok': False, 'error': 'conversationId required'}, 400)
                return
            try:
                from pathlib import Path as _Path
                agents_dir = _Path('/root/.openclaw/agents')
                deleted = False
                for sessions_file in sorted(agents_dir.glob('*/sessions/sessions.json')):
                    try:
                        sessions_data = json.loads(sessions_file.read_text())
                    except Exception:
                        continue
                    if not isinstance(sessions_data, dict):
                        continue
                    found_key = None
                    for skey, sval in sessions_data.items():
                        if not isinstance(sval, dict):
                            continue
                        if sval.get('sessionId', '') == conv_id or skey == conv_id:
                            found_key = skey
                            # 删除物理文件
                            sf = sval.get('sessionFile', '')
                            if sf:
                                try:
                                    _Path(sf).unlink(missing_ok=True)
                                    _Path(sf.replace('.jsonl', '.lock')).unlink(missing_ok=True)
                                except Exception:
                                    pass
                            break
                    if found_key:
                        del sessions_data[found_key]
                        try:
                            sessions_file.write_text(json.dumps(sessions_data, indent=2, ensure_ascii=False))
                        except Exception:
                            pass
                        deleted = True
                        break
                if deleted:
                    self.send_json({'ok': True, 'message': f'会话 {conv_id} 已删除'})
                else:
                    self.send_json({'ok': False, 'error': f'未找到会话 {conv_id}'}, 404)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'删除失败: {str(e)[:100]}'}, 500)
            return

        if p == '/api/gateway/clear-agent-sessions':
            """清空指定 Agent 的会话（通过本地文件系统操作）

            agentId 支持的值：
              'all'                   → 清除所有 Agent 的非 main 会话（保留所有主会话）
              'all-including-main'    → 清除所有 Agent 的所有会话（仅保留太子的 main）
              具体 agentId（如 'zhongshu'）→ 清除该 Agent 的所有会话（含 main；太子除外）
            """
            agent_id = body.get('agentId', '').strip() if body else ''
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId 无效'}, 400)
                return
            try:
                from pathlib import Path as _Path
                agents_dir = _Path('/root/.openclaw/agents')

                # 判断是否为「含 main」清理模式
                include_main = (agent_id == 'all-including-main') or (agent_id not in ('all',))

                # 确定要清理的目标 Agent 列表
                target_agents = []
                if agent_id in ('all', 'all-including-main'):
                    for d in sorted(agents_dir.iterdir()):
                        if d.is_dir() and (d / 'sessions' / 'sessions.json').exists():
                            target_agents.append(d.name)
                else:
                    if (agents_dir / agent_id / 'sessions' / 'sessions.json').exists():
                        target_agents = [agent_id]

                # 逐个 Agent 清理会话
                cleared = 0
                skipped_main = 0
                for aid in target_agents:
                    sessions_file = agents_dir / aid / 'sessions' / 'sessions.json'
                    try:
                        sessions_data = json.loads(sessions_file.read_text())
                    except Exception:
                        continue
                    if not isinstance(sessions_data, dict):
                        continue
                    to_delete = []
                    for skey, sval in sessions_data.items():
                        if not isinstance(sval, dict):
                            continue
                        is_main = ':main' in skey.lower()
                        if is_main:
                            if include_main:
                                # 含 main 模式：仅保留太子的 main
                                if aid == 'taizi':
                                    skipped_main += 1
                                    continue
                            else:
                                # 非 main 模式：保留所有 main
                                skipped_main += 1
                                continue
                        to_delete.append(skey)
                    for skey in to_delete:
                        # 删除物理文件
                        sf = sessions_data[skey].get('sessionFile', '')
                        if sf:
                            try:
                                _Path(sf).unlink(missing_ok=True)
                                _Path(sf.replace('.jsonl', '.lock')).unlink(missing_ok=True)
                            except Exception:
                                pass
                        del sessions_data[skey]
                        cleared += 1
                    # 写回 sessions.json
                    if to_delete:
                        try:
                            sessions_file.write_text(json.dumps(sessions_data, indent=2, ensure_ascii=False))
                        except Exception:
                            pass
                mode_desc = '所有会话（保留太子主会话）' if include_main else '非 main 会话'
                self.send_json({
                    'ok': True,
                    'message': f'已清理 {cleared} 个{mode_desc}（涉及 {len(target_agents)} 个 Agent）'
                              + (f'，跳过 {skipped_main} 个主会话' if skipped_main else ''),
                    'cleared': cleared,
                })
            except Exception as e:
                self.send_json({'ok': False, 'error': f'清理失败: {str(e)[:100]}'}, 500)
            return

        if p == '/api/gateway/sessions-url':
            """返回 Gateway 会话管理页面 URL（兼容性保留端点）"""
            try:
                gw_ext_url = _get_external_gateway_url(self)
                sessions_url = f'{gw_ext_url}/sessions'
            except Exception:
                sessions_url = ''
            self.send_json({
                'ok': True,
                'url': sessions_url,
                'reachable': True,
                'hint': '',
            })
            return

        if p == '/api/archive-task':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            archived = body.get('archived', True)
            archive_all = body.get('archiveAllDone', False)
            if not task_id and not archive_all:
                self.send_json({'ok': False, 'error': 'taskId or archiveAllDone required'}, 400)
                return
            result = handle_archive_task(task_id, archived, archive_all)
            self.send_json(result)
            return

        if p == '/api/audit-exclude':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', 'exclude')  # 'exclude' or 'include'
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            if not _SAFE_NAME_RE.match(task_id) and not task_id.startswith('JJC-'):
                self.send_json({'ok': False, 'error': 'taskId 含非法字符'}, 400)
                return
            exclude_file = DATA / 'audit_exclude.json'
            try:
                data = json.loads(exclude_file.read_text()) if exclude_file.exists() else {}
            except Exception:
                data = {}
            excluded = set(data.get('excluded_tasks', []))
            if action == 'include':
                excluded.discard(task_id)
                msg = f'{task_id} 已恢复监察'
            else:
                excluded.add(task_id)
                msg = f'{task_id} 已停止监察'
            data['excluded_tasks'] = sorted(excluded)
            exclude_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            # 即时清理 pipeline_audit.json 中的 watched_tasks/violations/notifications
            try:
                audit_file = DATA / 'pipeline_audit.json'
                if audit_file.exists():
                    audit_data = atomic_json_read(audit_file, {})
                    if action == 'exclude':
                        audit_data['watched_tasks'] = [
                            w for w in audit_data.get('watched_tasks', [])
                            if w.get('task_id', '') != task_id
                        ]
                        audit_data['watched_count'] = len(audit_data.get('watched_tasks', []))
                    atomic_json_write(audit_file, audit_data)
            except Exception:
                pass  # 清理失败不影响主流程

            self.send_json({'ok': True, 'message': msg, 'excluded_count': len(excluded)})
            return

        if p == '/api/audit-clear-resolved':
            # 清除已归档/已完成任务的违规和通知记录
            try:
                audit_file = DATA / 'pipeline_audit.json'
                if not audit_file.exists():
                    self.send_json({'ok': True, 'message': '无审计数据需要清理', 'cleared': 0})
                    return
                audit_data = atomic_json_read(audit_file, {})
                # 收集已归档任务 ID
                archived_ids = set()
                all_tasks = load_tasks()
                for t in all_tasks:
                    if t.get('archived') and t.get('state') in ('Done', 'Cancelled'):
                        archived_ids.add(t.get('id', ''))
                # 同时收集 Done/Cancelled 但未监察的任务（已不在 watched_tasks 中）
                watched_ids = set(w.get('task_id', '') for w in audit_data.get('watched_tasks', []))
                all_tasks_set = set(t.get('id', '') for t in all_tasks)
                # 非活跃任务 = 不在 watched_tasks 中 且 已完成/取消
                non_active_ids = set()
                for t in all_tasks:
                    tid = t.get('id', '')
                    if tid not in watched_ids and t.get('state') in ('Done', 'Cancelled'):
                        non_active_ids.add(tid)
                clear_ids = archived_ids | non_active_ids
                if not clear_ids:
                    self.send_json({'ok': True, 'message': '无已归档任务需要清理', 'cleared': 0})
                    return
                # 清理违规记录
                old_violations = audit_data.get('violations', [])
                new_violations = [v for v in old_violations if v.get('task_id', '') not in clear_ids]
                cleared_violations = len(old_violations) - len(new_violations)
                audit_data['violations'] = new_violations
                # 清理通知记录
                old_notifs = audit_data.get('notifications', [])
                new_notifs = [
                    n for n in old_notifs
                    if n.get('task_id', '') not in clear_ids
                    and not any(tid in clear_ids for tid in (n.get('task_ids') or []))
                ]
                cleared_notifs = len(old_notifs) - len(new_notifs)
                audit_data['notifications'] = new_notifs
                atomic_json_write(audit_file, audit_data)
                self.send_json({
                    'ok': True,
                    'message': f'已清理 {cleared_violations} 条违规、{cleared_notifs} 条通知',
                    'cleared_violations': cleared_violations,
                    'cleared_notifications': cleared_notifs,
                    'cleared_task_count': len(clear_ids),
                })
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)[:200]}, 500)
            return

        if p == '/api/task-todos':
            task_id = body.get('taskId', '').strip()
            todos = body.get('todos', [])  # [{id, title, status}]
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            # todos 输入校验
            if not isinstance(todos, list) or len(todos) > 200:
                self.send_json({'ok': False, 'error': 'todos must be a list (max 200 items)'}, 400)
                return
            valid_statuses = {'not-started', 'in-progress', 'completed'}
            for td in todos:
                if not isinstance(td, dict) or 'id' not in td or 'title' not in td:
                    self.send_json({'ok': False, 'error': 'each todo must have id and title'}, 400)
                    return
                if td.get('status', 'not-started') not in valid_statuses:
                    td['status'] = 'not-started'
            result = update_task_todos(task_id, todos)
            self.send_json(result)
            return

        if p == '/api/create-task':
            title = body.get('title', '').strip()
            org = body.get('org', '中书省').strip()
            official = body.get('official', '中书令').strip()
            priority = body.get('priority', 'normal').strip()
            template_id = body.get('templateId', '')
            params = body.get('params', {})
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            target_dept = body.get('targetDept', '').strip()
            result = handle_create_task(title, org, official, priority, template_id, params, target_dept)
            self.send_json(result)
            return

        if p == '/api/review-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # approve, reject
            comment = body.get('comment', '').strip()
            if not task_id or action not in ('approve', 'reject'):
                self.send_json({'ok': False, 'error': 'taskId and action(approve/reject) required'}, 400)
                return
            result = handle_review_action(task_id, action, comment)
            self.send_json(result)
            return

        if p == '/api/advance-state':
            task_id = body.get('taskId', '').strip()
            comment = body.get('comment', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_advance_state(task_id, comment)
            self.send_json(result)
            return

        if p == '/api/agent-wake':
            agent_id = body.get('agentId', '').strip()
            message = body.get('message', '').strip()
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId required'}, 400)
                return
            result = wake_agent(agent_id, message)
            self.send_json(result)
            return

        if p == '/api/set-model':
            agent_id = body.get('agentId', '').strip()
            model = body.get('model', '').strip()
            if not agent_id or not model:
                self.send_json({'ok': False, 'error': 'agentId and model required'}, 400)
                return

            # Write to pending (atomic)
            pending_path = DATA / 'pending_model_changes.json'
            def update_pending(current):
                current = [x for x in current if x.get('agentId') != agent_id]
                current.append({'agentId': agent_id, 'model': model})
                return current
            atomic_json_update(pending_path, update_pending, [])

            # Async apply
            def apply_async():
                try:
                    subprocess.run(['python3', str(SCRIPTS / 'apply_model_changes.py')], timeout=30)
                    subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
                except Exception as e:
                    print(f'[apply error] {e}', file=sys.stderr)

            threading.Thread(target=apply_async, daemon=True).start()
            self.send_json({'ok': True, 'message': f'Queued: {agent_id} → {model}'})

        # Fix #139: 设置派发渠道（feishu/telegram/wecom/signal/tui）
        elif p == '/api/set-dispatch-channel':
            channel = body.get('channel', '').strip()
            allowed = {'feishu', 'telegram', 'wecom', 'signal', 'tui', 'discord', 'slack'}
            if not channel or channel not in allowed:
                self.send_json({'ok': False, 'error': f'channel must be one of: {", ".join(sorted(allowed))}'}, 400)
                return
            def _set_channel(cfg):
                cfg['dispatchChannel'] = channel
                return cfg
            atomic_json_update(DATA / 'agent_config.json', _set_channel, {})
            self.send_json({'ok': True, 'message': f'派发渠道已切换为 {channel}'})

        # ── 朝堂议政 POST ──
        elif p == '/api/court-discuss/start':
            topic = body.get('topic', '').strip()
            officials = body.get('officials', [])
            task_id = body.get('taskId', '').strip()
            if not topic:
                self.send_json({'ok': False, 'error': 'topic required'}, 400)
                return
            if not officials or not isinstance(officials, list):
                self.send_json({'ok': False, 'error': 'officials list required'}, 400)
                return
            # 校验官员 ID
            valid_ids = set(CD_PROFILES.keys())
            officials = [o for o in officials if o in valid_ids]
            if len(officials) < 2:
                self.send_json({'ok': False, 'error': '至少选择2位官员'}, 400)
                return
            self.send_json(cd_create(topic, officials, task_id))

        elif p == '/api/court-discuss/advance':
            sid = body.get('sessionId', '').strip()
            user_msg = body.get('userMessage', '').strip() or None
            decree = body.get('decree', '').strip() or None
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_advance(sid, user_msg, decree))

        elif p == '/api/court-discuss/conclude':
            sid = body.get('sessionId', '').strip()
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_conclude(sid))

        elif p == '/api/court-discuss/destroy':
            sid = body.get('sessionId', '').strip()
            if sid:
                cd_destroy(sid)
            self.send_json({'ok': True})

        # [TaskOutput] 产出管理 POST 路由
        elif p.startswith('/api/outputs/') and '/upload' in p:
            task_id = unquote(p.replace('/api/outputs/', '').replace('/upload', ''))
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                result = handle_output_upload(task_id, self)
                self.send_json(result)
        elif p.startswith('/api/outputs/') and '/delete' in p:
            task_id = unquote(p.replace('/api/outputs/', '').replace('/delete', ''))
            filename = (body or {}).get('filename', '')
            if not task_id or not filename:
                self.send_json({'ok': False, 'error': 'task_id and filename required'}, 400)
            else:
                self.send_json(handle_output_delete(task_id, filename))

        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description='三省六部看板服务器')
    parser.add_argument('--port', type=int, default=7891)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--cors', default=None, help='Allowed CORS origin (default: reflect request Origin header)')
    args = parser.parse_args()

    global ALLOWED_ORIGIN, _DASHBOARD_PORT, _DEFAULT_ORIGINS
    ALLOWED_ORIGIN = args.cors
    _DASHBOARD_PORT = args.port
    _check_api_auth._port = args.port  # 供同源放行判断使用
    # Fix #6: 将 EDICT_EXTERNAL_URL 对应的 origin 也加入白名单
    if _EDICT_EXTERNAL_URL:
        _DEFAULT_ORIGINS = _DEFAULT_ORIGINS | {_EDICT_EXTERNAL_URL}
    _DEFAULT_ORIGINS = _DEFAULT_ORIGINS | {
        f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}',
    }

    # Problem 4: 使用 ThreadingMixIn 支持并发连接，避免单线程阻塞
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = False  # 避免多实例冲突
        daemon_threads = True
        # socket 超时：清理僵死连接
        timeout = 30

    server = ThreadedHTTPServer(('0.0.0.0', args.port), Handler)
    log.info(f'三省六部看板启动 → http://{args.host}:{args.port} (线程模式)')
    print(f'   按 Ctrl+C 停止')

    # Problem 4: 定期清理僵死连接线程
    def _cleanup_dead_threads():
        while True:
            _time.sleep(60)
            # ThreadingMixIn 自动管理线程，这里只做日志
            active = threading.active_count()
            if active > 20:
                log.info(f'活跃线程数: {active}')

    threading.Thread(target=_cleanup_dead_threads, daemon=True).start()

    migrate_notification_config()

    # 启动恢复：重新派发上次被 kill 中断的 queued 任务
    threading.Timer(3.0, _startup_recover_queued_dispatches).start()

    # Problem 4: 优雅关闭
    def _graceful_shutdown(signum, frame):
        log.info('收到关闭信号，正在优雅关闭服务器...')
        server.shutdown()
        log.info('服务器已关闭')
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
