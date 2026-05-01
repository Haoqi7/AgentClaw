#!/usr/bin/env python3
"""
三省六部 · Skill 管理工具
支持从本地或远程 URL 添加、更新、查看和移除 skills
集成 ClawHub 官方技能商店 (https://clawhub.ai/)

Usage:
  # 远程 URL 添加（单个 SKILL.md 文件）
  python3 scripts/skill_manager.py add-remote --agent zhongshu --name code_review \\
    --source https://raw.githubusercontent.com/org/skills/main/code_review/SKILL.md \\
    --description "代码审查"

  # ClawHub 商店操作（搜索 → 下载 zip → 解压安装）
  python3 scripts/skill_manager.py search-hub --query calendar
  python3 scripts/skill_manager.py install-hub --agent menxia --slug caldav-calendar
  python3 scripts/skill_manager.py import-official-hub --agents zhongshu,menxia

  # 通用操作
  python3 scripts/skill_manager.py list-remote
  python3 scripts/skill_manager.py update-remote --agent zhongshu --name code_review
  python3 scripts/skill_manager.py remove-remote --agent zhongshu --name code_review
  python3 scripts/skill_manager.py check-updates
"""
import sys
import json
import pathlib
import argparse
import os
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import now_iso, safe_name, read_json

OCLAW_HOME = Path(os.environ.get('OPENCLAW_HOME', Path.home() / '.openclaw'))


def _download_file(url: str, timeout: int = 30, retries: int = 3) -> str:
    """从 URL 下载文件内容（文本格式），支持重试"""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read(10 * 1024 * 1024)  # 最多 10MB
                return content.decode('utf-8')
        except urllib.error.HTTPError as e:
            last_error = f'HTTP {e.code}: {e.reason}'
            if e.code in (404, 403):
                break  # 不重试 4xx
        except urllib.error.URLError as e:
            last_error = f'网络错误: {e.reason}'
        except Exception as e:
            last_error = f'{type(e).__name__}: {e}'

        if attempt < retries:
            import time
            wait = attempt * 3  # 3s, 6s
            print(f'   ⚠️ 第 {attempt} 次下载失败({last_error})，{wait}秒后重试...')
            time.sleep(wait)

    # 所有重试失败
    hint = ''
    if 'timed out' in str(last_error).lower() or '超时' in str(last_error):
        hint = '\n   💡 提示: 如果在中国大陆，请设置代理 export https_proxy=http://proxy:port'
    elif '404' in str(last_error):
        hint = '\n   💡 提示: 请检查 ClawHub (https://clawhub.ai/) 是否有该 skill，或检查 URL 是否正确'
    raise Exception(f'{last_error} (已重试 {retries} 次){hint}')


def _compute_checksum(content: str) -> str:
    """计算文本内容的校验和"""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _compute_binary_checksum(data: bytes) -> str:
    """计算二进制数据的校验和"""
    import hashlib
    return hashlib.sha256(data).hexdigest()[:16]


def add_remote(agent_id: str, name: str, source_url: str, description: str = '') -> bool:
    """从远程 URL 为 Agent 添加 skill（下载单个文件，适用于 GitHub raw 等 URL）"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False

    # URL scheme 验证：仅允许 http/https
    if not source_url.startswith(('http://', 'https://')):
        print(f'❌ 错误: 不支持的 URL scheme，仅允许 http/https', file=sys.stderr)
        return False

    # 设置 workspace
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'

    # 下载文件
    print(f'⏳ 正在从 {source_url} 下载...')
    try:
        content = _download_file(source_url)
    except Exception as e:
        print(f'❌ 下载失败：{e}')
        print(f'   URL: {source_url}')
        return False

    # 基础验证（放宽检查：有些 skill 不以 --- 开头）
    if len(content.strip()) < 10:
        print(f'❌ 文件内容过短或为空')
        return False

    # 保存 SKILL.md
    skill_md.write_text(content)

    # 保存源信息
    source_info = {
        'skillName': name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))

    print(f'✅ 技能 {name} 已添加到 {agent_id}')
    print(f'   路径: {skill_md}')
    print(f'   大小: {len(content)} 字节')
    return True


def list_remote() -> bool:
    """列出所有已添加的远程 skills"""
    if not OCLAW_HOME.exists():
        print('❌ OCLAW_HOME 不存在')
        return False

    remote_skills = []

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

            if not source_json.exists():
                continue

            try:
                source_info = json.loads(source_json.read_text())
                slug = source_info.get('sourceSlug', '')
                source_label = f'ClawHub:{slug}' if slug else source_info.get('sourceUrl', 'N/A')
                remote_skills.append({
                    'agent': agent_id,
                    'skill': skill_name,
                    'source': source_label,
                    'slug': slug,
                    'desc': source_info.get('description', ''),
                    'added': source_info.get('addedAt', 'N/A'),
                })
            except Exception:
                pass

    if not remote_skills:
        print('📭 暂无远程 skills')
        return True

    print(f'📋 共 {len(remote_skills)} 个远程 skills：\n')
    print(f'{"Agent":<12} | {"Skill 名称":<20} | {"来源":<30} | 添加时间')
    print('-' * 100)

    for sk in remote_skills:
        desc = (sk['desc'] or sk['source'])[:28].ljust(28)
        print(f"{sk['agent']:<12} | {sk['skill']:<20} | {desc} | {sk['added'][:10]}")

    print()
    return True


# [Fix 2] update_remote 不再委托给 add_remote，独立下载并保留原始 addedAt
def update_remote(agent_id: str, name: str) -> bool:
    """更新远程 skill 为最新版本（保留原始 addedAt）

    自动识别来源:
    - ClawHub 技能 (sourceSlug): 重新下载 zip 并解压
    - 通用 URL 技能: 重新下载单个文件
    """
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False

    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    source_json = workspace / '.source.json'

    if not source_json.exists():
        print(f'❌ 技能不存在或不是远程 skill: {name}')
        return False

    try:
        source_info = json.loads(source_json.read_text())
        source_slug = source_info.get('sourceSlug')

        if source_slug:
            # ── ClawHub 技能：重新下载 zip 包并解压 ──
            original_added_at = source_info.get('addedAt', now_iso())
            old_checksum = source_info.get('checksum', '')
            ok = _install_from_hub_zip(
                agent_id, source_slug, skill_name=name,
                description=source_info.get('description', ''),
                preserve_added_at=original_added_at,
            )
            if ok:
                # 检查是否有变化
                try:
                    new_info = json.loads(source_json.read_text())
                    if new_info.get('checksum') == old_checksum:
                        print(f'   ℹ️ 内容未变化（已是最新版本）')
                except Exception:
                    pass
            return ok

        # ── 通用 URL 技能：重新下载单个文件 ──
        source_url = source_info.get('sourceUrl')
        if not source_url:
            print(f'❌ 无效的源 URL')
            return False

        original_added_at = source_info.get('addedAt', now_iso())
        description = source_info.get('description', '')
        old_checksum = source_info.get('checksum', '')

        print(f'⏳ 正在从 {source_url} 更新...')
        content = _download_file(source_url)

        if len(content.strip()) < 10:
            print(f'❌ 下载内容过短或为空')
            return False

        new_checksum = _compute_checksum(content)

        skill_md = workspace / 'SKILL.md'
        skill_md.write_text(content)

        source_info['lastUpdated'] = now_iso()
        source_info['checksum'] = new_checksum
        source_info['status'] = 'valid'
        source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))

        if new_checksum == old_checksum:
            print(f'✅ 技能 {name} 已是最新版本')
        else:
            print(f'✅ 技能 {name} 已更新')
            print(f'   大小: {len(content)} 字节')

        return True
    except Exception as e:
        print(f'❌ 更新失败：{e}')
        return False


def remove_remote(agent_id: str, name: str) -> bool:
    """移除远程 skill"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False

    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    source_json = workspace / '.source.json'

    if not source_json.exists():
        print(f'❌ 技能不存在或不是远程 skill: {name}')
        return False

    try:
        import shutil
        shutil.rmtree(workspace)
        print(f'✅ 技能 {name} 已从 {agent_id} 移除')
        return True
    except Exception as e:
        print(f'❌ 移除失败：{e}')
        return False


# ═══════════════════════════════════════════════════════════════════════
# ClawHub 官方技能商店
# https://clawhub.ai/
# 国内镜像: https://mirror-cn.clawhub.com/ (推荐，速度快 10 倍+)
#
# 安装流程: 搜索获取 slug → /api/v1/download 下载 zip → 解压到 skills 目录
#
# API 端点:
#   GET /api/v1/search?q=xxx          搜索技能
#   GET /api/v1/skills                列出技能（支持分页、排序）
#   GET /api/v1/skills/{slug}         获取技能详情
#   GET /api/v1/skills/{slug}/file    获取技能文件内容（需 path 参数）
#   GET /api/v1/download?slug=xxx     下载技能压缩包
#   GET /api/v1/skills/{slug}/versions  版本历史
# ═══════════════════════════════════════════════════════════════════════

CLAWHUB_MIRROR = 'https://mirror-cn.clawhub.com'  # 国内镜像
CLAWHUB_MAIN = 'https://clawhub.ai'                  # 主站

# 支持通过环境变量或配置文件覆盖 ClawHub 地址
_CLAWHUB_ENV = 'OPENCLAW_CLAWHUB_BASE'

# 两个地址互补：镜像优先，主站备用
CLAWHUB_FALLBACK_ORDER = [CLAWHUB_MIRROR, CLAWHUB_MAIN]


def _get_clawhub_base():
    """获取 ClawHub API 基础地址

    优先级: 本地配置文件 > 环境变量 > 国内镜像 > 主站
    配置方式:
      echo "https://clawhub.ai" > ~/.openclaw/clawhub-url
      export OPENCLAW_CLAWHUB_BASE=https://clawhub.ai
    """
    cfg_file = OCLAW_HOME / 'clawhub-url'
    if cfg_file.exists():
        base = cfg_file.read_text().strip()
        if base:
            return base
    env_base = os.environ.get(_CLAWHUB_ENV)
    if env_base:
        return env_base
    return CLAWHUB_MIRROR


def _clawhub_request_with_fallback(url_builder, timeout=15, retries=1, raw_text=False):
    """带镜像回退的 ClawHub API 请求

    优先使用镜像 (mirror-cn.clawhub.com)，失败后回退到主站 (clawhub.ai)。
    如果用户自定义了地址则不回退。

    Args:
        url_builder: 接受 base URL 并返回完整请求 URL 的函数
        timeout: 请求超时（秒）
        retries: 重试次数
        raw_text: 如果 True，返回原始文本而非 JSON 解析
    """
    # 判断是否使用用户自定义地址
    cfg_file = OCLAW_HOME / 'clawhub-url'
    env_base = os.environ.get(_CLAWHUB_ENV)
    user_custom = (cfg_file.exists() and cfg_file.read_text().strip()) or env_base

    bases_to_try = [_get_clawhub_base()]
    if not user_custom:
        # 未自定义时，尝试所有回退地址
        for b in CLAWHUB_FALLBACK_ORDER:
            if b not in bases_to_try:
                bases_to_try.append(b)

    last_error = None
    for base in bases_to_try:
        try:
            url = url_builder(base)
            req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode('utf-8')
                if raw_text:
                    return data
                return json.loads(data)
        except Exception as e:
            last_error = e
            continue

    raise Exception(f'ClawHub API 请求失败（已尝试 {len(bases_to_try)} 个地址）: {last_error}')


def _get_clawhub_download_url(slug, tag='latest'):
    """技能包下载 URL: GET /api/v1/download?slug=xxx&tag=latest"""
    return f'{_get_clawhub_base()}/api/v1/download?slug={urllib.parse.quote(slug)}&tag={tag}'


def _get_clawhub_file_url(slug, path='SKILL.md'):
    """获取技能单个文件内容 URL: GET /api/v1/skills/{slug}/file?path=xxx

    注意: 此 URL 返回文本内容，用于 server.py 的 update_remote_skill（兼容无需改 server.py）
    """
    return f'{_get_clawhub_base()}/api/v1/skills/{urllib.parse.quote(slug)}/file?path={urllib.parse.quote(path)}'


def _get_clawhub_search_url(query):
    """技能搜索 URL: GET /api/v1/search?q=xxx"""
    return f'{_get_clawhub_base()}/api/v1/search?q={urllib.parse.quote(query)}'


def _get_clawhub_list_url():
    """技能列表 URL: GET /api/v1/skills"""
    return f'{_get_clawhub_base()}/api/v1/skills'


def _check_hub_available(timeout: int = 10) -> bool:
    """检测 ClawHub 是否可达"""
    try:
        url = _get_clawhub_list_url()
        req = urllib.request.Request(url, method='HEAD',
                                      headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # 4xx 说明服务端可达（只是该端点可能不存在或需要参数）
        if e.code < 500:
            return True
        return False
    except Exception:
        return False


def _download_bytes(url, timeout=60, retries=2):
    """下载二进制内容（用于 zip 包）"""
    import time
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_error = f'HTTP {e.code}'
            if e.code in (404, 403):
                break
        except Exception as e:
            last_error = str(e)
        if attempt < retries:
            time.sleep(attempt * 3)
    raise Exception(last_error)


def _install_from_hub_zip(agent_id, slug, skill_name=None, description='',
                           preserve_added_at=None):
    """从 ClawHub 下载技能 zip 并解压安装到 agent workspace

    完整流程:
    1. GET /api/v1/download?slug=xxx&tag=latest → 下载 zip
    2. 安全校验 (zip 格式、路径穿越防护)
    3. 解压到 ~/.openclaw/workspace-{agent_id}/skills/{skill_name}/
    4. 写入 .source.json (含 sourceUrl 指向 file 端点，兼容 server.py 更新)

    Args:
        agent_id: 目标 Agent ID
        slug: ClawHub 技能唯一标识 (从搜索 API 获取)
        skill_name: 本地技能目录名 (默认用 slug 转换)
        description: 技能描述
        preserve_added_at: 保留原始添加时间 (更新时使用)
    """
    import zipfile
    import tempfile
    import shutil
    import io

    skill_name = skill_name or slug.replace('-', '_')
    download_url = _get_clawhub_download_url(slug)

    print(f'⏳ 正在从 ClawHub 下载 {slug} (tag=latest) ...')
    print(f'   镜像: {_get_clawhub_base()}')

    # ── Step 1: 下载 zip 包 ──
    try:
        zip_data = _download_bytes(download_url, timeout=60)
    except Exception as e:
        last_err = str(e)
        hint = ''
        if 'timed out' in last_err.lower() or '超时' in last_err:
            hint = '\n   💡 提示: 超时，可切换主站:\n      echo "https://clawhub.ai" > ~/.openclaw/clawhub-url'
        elif '404' in last_err:
            hint = f'\n   💡 提示: 在 ClawHub 搜索确认 slug:\n      curl "{_get_clawhub_search_url(slug)}"'
        print(f'❌ 下载失败: {last_err}{hint}')
        return False

    if len(zip_data) < 100:
        print(f'❌ 下载内容过短 ({len(zip_data)} bytes)，无效')
        return False

    # ── Step 2: 校验 zip 格式 ──
    if not zipfile.is_zipfile(io.BytesIO(zip_data)):
        print(f'❌ 下载的文件不是有效的 zip 格式')
        return False

    zip_checksum = _compute_binary_checksum(zip_data)

    # ── Step 3: 安全解压 ──
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, f'{slug}.zip')
            extract_dir = os.path.join(tmpdir, 'extract')
            os.makedirs(extract_dir, exist_ok=True)

            with open(zip_path, 'wb') as f:
                f.write(zip_data)

            # 安全解压（防止路径穿越攻击）
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    member_path = pathlib.PurePosixPath(member)
                    if '..' in member_path.parts or str(member_path).startswith('/'):
                        print(f'⚠️ 跳过不安全路径: {member}')
                        continue
                    zf.extract(member, extract_dir)

            # 确定解压后的实际内容
            extracted = pathlib.Path(extract_dir)
            entries = [e for e in extracted.iterdir() if not e.name.startswith('.')]

            if not entries:
                print(f'❌ 解压后目录为空')
                return False

            # 如果只有一个顶层目录（如 slug-name/），使用其内容
            if len(entries) == 1 and entries[0].is_dir():
                content_dir = entries[0]
            else:
                content_dir = extracted

            # ── Step 4: 写入目标 workspace ──
            workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True, exist_ok=True)

            # 复制文件到目标目录
            for item in content_dir.iterdir():
                dest = workspace / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            # ── Step 5: 写入 .source.json ──
            added_at = preserve_added_at or now_iso()
            source_info = {
                'skillName': skill_name,
                'sourceSlug': slug,
                # sourceUrl 指向 file 端点（返回文本），兼容 server.py 的 update_remote_skill
                'sourceUrl': _get_clawhub_file_url(slug),
                'downloadUrl': download_url,
                'description': description or f'ClawHub: {slug}',
                'addedAt': added_at,
                'lastUpdated': now_iso(),
                'checksum': zip_checksum,
                'status': 'valid',
            }
            source_json = workspace / '.source.json'
            source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))

            # 统计
            file_count = sum(1 for _ in workspace.rglob('*')
                            if _.is_file() and _.name != '.source.json')
            print(f'✅ 技能 {skill_name} 已安装到 {agent_id} ({file_count} 个文件, {len(zip_data) // 1024}KB)')
            return True

    except Exception as e:
        print(f'❌ 安装失败: {e}')
        return False


# 内置 skill slug 列表（当 ClawHub API 不可达时的兜底）
# 格式: slug → (推荐安装的 agents)
# 注意: 实际 slug 需从 ClawHub 搜索确认，此处为示例
BUILTIN_SKILL_SLUGS = {
    'code-review': ('bingbu', 'xingbu', 'menxia'),
    'api-design': ('bingbu', 'gongbu', 'menxia'),
    'security-audit': ('xingbu', 'menxia'),
    'data-analysis': ('hubu', 'menxia'),
    'doc-generation': ('libu', 'menxia'),
    'test-framework': ('gongbu', 'xingbu', 'menxia'),
}


def search_hub_dict(query, limit=20):
    """搜索 ClawHub 技能商店，返回 dict（供 server.py API 调用）

    API: GET /api/v1/search?q=xxx (官方文档)
    返回: skills 数组，每项含 slug/name/description/downloads 等
    带镜像回退：镜像不可达时自动使用主站
    """
    if not query:
        return {'ok': False, 'error': 'q 参数必填'}

    def build_url(base):
        return f'{base}/api/v1/search?q={urllib.parse.quote(query)}&limit={limit}'

    try:
        data = _clawhub_request_with_fallback(build_url, timeout=15)
        results = data.get('skills', data.get('results', []))[:limit]
        return {'ok': True, 'results': results, 'query': query, 'total': len(results)}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': f'ClawHub API HTTP {e.code}: {e.reason}'}
    except Exception as e:
        return {'ok': False, 'error': f'搜索失败: {str(e)[:100]}'}


def clawhub_preview_dict(slug):
    """预览 ClawHub 技能 SKILL.md 内容，返回 dict（供 server.py API 调用）

    API: GET /api/v1/skills/{slug}/file?path=SKILL.md
    返回 SKILL.md 原始文本内容
    """
    if not slug:
        return {'ok': False, 'error': 'slug 参数必填'}

    def build_url(base):
        return f'{base}/api/v1/skills/{urllib.parse.quote(slug)}/file?path=SKILL.md'

    try:
        content = _clawhub_request_with_fallback(build_url, timeout=15, raw_text=True)
        return {'ok': True, 'slug': slug, 'content': content}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': f'ClawHub API HTTP {e.code}: {e.reason}'}
    except Exception as e:
        return {'ok': False, 'error': f'预览失败: {str(e)[:100]}'}


def search_hub(query, limit=10):
    """搜索 ClawHub 技能商店（CLI 版本，打印到终端）

    API: GET /api/v1/search?q=xxx
    """
    if not query:
        print('❌ 请提供搜索关键词')
        return False

    print(f'🔍 正在搜索: {query}')
    print(f'   API: {_get_clawhub_base()}\n')

    result = search_hub_dict(query, limit)

    if not result.get('ok'):
        print(f'❌ 搜索失败: {result.get("error", "未知错误")}')
        print(f'   💡 提示: 检查网络或直接浏览 https://clawhub.ai/')
        return False

    results = result.get('results', [])

    if not results:
        print(f'📭 未找到匹配的技能')
        print(f'   💡 直接浏览商店: https://clawhub.ai/')
        return True

    print(f'找到 {len(results)} 个技能：\n')
    print(f'{"Slug":<30} | {"名称":<20} | 描述')
    print('-' * 100)

    for sk in results:
        slug = sk.get('slug', '')
        name = (sk.get('name', '') or slug)[:20].ljust(20)
        desc = (sk.get('description', '') or '')[:40]
        print(f'{slug:<30} | {name} | {desc}')

    print(f'\n💡 安装命令:')
    print(f'   python3 scripts/skill_manager.py install-hub --agent <agent> --slug <slug>')
    return True


def install_hub(agent_id, slug, skill_name=None):
    """从 ClawHub 安装指定技能

    流程: /api/v1/download?slug=xxx&tag=latest → 下载 zip → 解压安装
    """
    if not safe_name(agent_id):
        print(f'❌ agent_id 含非法字符')
        return False
    if not slug or not slug.strip():
        print(f'❌ 请提供 skill slug（通过 search-hub 获取）')
        return False
    slug = slug.strip()
    skill_name = skill_name or slug.replace('-', '_')

    return _install_from_hub_zip(agent_id, slug, skill_name=skill_name)


def import_official_hub(agent_ids=None):
    """从 ClawHub 官方商店导入 skills

    流程: /api/v1/skills 获取列表 → /api/v1/download?slug=xxx 下载 zip → 解压安装
    """
    if agent_ids is None:
        agent_ids = []
    use_recommended = not agent_ids

    if use_recommended:
        print('📋 未指定 agent，使用推荐配置...\n')

    # 预检 ClawHub 可用性
    print(f'🔍 正在检测 ClawHub ({_get_clawhub_base()}) ...')
    hub_reachable = _check_hub_available()

    if not hub_reachable:
        print(f'\n⚠️ ClawHub 当前不可达')
        print(f'   可能原因：')
        print(f'   1. 网络问题（需要代理访问外网）')
        print(f'   2. ClawHub 服务暂时不可用')
        print(f'\n   建议：')
        print(f'   1. 检查网络: curl -I {_get_clawhub_list_url()}')
        print(f'   2. 切换主站: echo "https://clawhub.ai" > ~/.openclaw/clawhub-url')
        print(f'   3. 设置代理: export https_proxy=http://your-proxy:port')
        print(f'   4. 搜索并手动安装:')
        print(f'      python3 scripts/skill_manager.py search-hub --query <关键词>')
        return False

    print(f'   ✅ ClawHub 可达\n')

    # ── 从 API 获取技能列表 ──
    skills_to_import = {}
    try:
        list_url = _get_clawhub_list_url()
        req = urllib.request.Request(list_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            api_data = json.loads(resp.read().decode('utf-8'))

        api_skills = api_data.get('skills', [])
        if api_skills:
            print(f'   📦 从 ClawHub 获取到 {len(api_skills)} 个技能\n')
            for sk in api_skills[:20]:  # 最多导入 20 个
                slug = sk.get('slug', '')
                if slug:
                    skills_to_import[slug] = sk.get('description', '')
    except Exception:
        print(f'   ⚠️ API 获取列表失败，使用内置推荐列表\n')

    # 兜底: 使用内置列表
    if not skills_to_import:
        skills_to_import = {slug: f'推荐 skill: {slug}' for slug in BUILTIN_SKILL_SLUGS}
        print(f'   📋 使用内置推荐列表: {len(skills_to_import)} 个技能\n')

    total = 0
    success = 0
    failed = []

    for slug, desc in skills_to_import.items():
        skill_name = slug.replace('-', '_')

        if use_recommended:
            target_agents = BUILTIN_SKILL_SLUGS.get(slug, ['menxia'])
        else:
            target_agents = agent_ids

        print(f'📥 [{slug}]')
        print(f'   目标 agents: {", ".join(target_agents)}')

        for ag_id in target_agents:
            total += 1
            ok = _install_from_hub_zip(ag_id, slug, skill_name=skill_name, description=desc)
            if ok:
                success += 1
            else:
                failed.append(f'{ag_id}/{slug}')
        print()

    print(f'📊 导入完成: {success}/{total} 个成功')
    if failed:
        print(f'\n❌ 失败列表:')
        for f_item in failed:
            print(f'   - {f_item}')
        print(f'\n💡 排查建议:')
        print(f'   1. 浏览 ClawHub: https://clawhub.ai/')
        print(f'   2. 搜索 slug: python3 scripts/skill_manager.py search-hub --query <关键词>')
        print(f'   3. 切换主站: echo "https://clawhub.ai" > ~/.openclaw/clawhub-url')
        print(f'   4. 单独安装: python3 scripts/skill_manager.py install-hub --agent <agent> --slug <slug>')
    return success == total


# [Fix 6] check-updates: 自动识别 ClawHub 技能，走 zip 下载对比 checksum
def check_updates() -> bool:
    """检查所有远程 skill 是否有更新"""
    if not OCLAW_HOME.exists():
        print('📭 OCLAW_HOME 不存在，无已安装的远程 skills')
        return True

    remote_skills = []

    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            source_json = skill_dir / '.source.json'
            if not source_json.exists():
                continue
            try:
                source_info = json.loads(source_json.read_text())
                source_url = source_info.get('sourceUrl', '')
                if source_url:
                    remote_skills.append({
                        'agent': agent_id,
                        'name': skill_dir.name,
                        'url': source_url,
                        'sourceSlug': source_info.get('sourceSlug', ''),
                        'checksum': source_info.get('checksum', ''),
                        'last_updated': source_info.get('lastUpdated', 'N/A'),
                    })
            except Exception:
                pass

    if not remote_skills:
        print('📭 暂无远程 skills，无需检查更新')
        return True

    print(f'🔍 正在检查 {len(remote_skills)} 个远程 skills 的更新...\n')

    has_update = False
    errors = []

    for sk in remote_skills:
        key = f"{sk['agent']}/{sk['name']}"
        try:
            if sk['sourceSlug']:
                # ClawHub 技能: 下载 zip 对比 checksum
                download_url = _get_clawhub_download_url(sk['sourceSlug'])
                zip_data = _download_bytes(download_url, timeout=30)
                new_checksum = _compute_binary_checksum(zip_data)
            else:
                # 通用 URL 技能: 下载文本对比 checksum
                content = _download_file(sk['url'], timeout=15, retries=1)
                new_checksum = _compute_checksum(content)

            if new_checksum != sk['checksum']:
                has_update = True
                print(f'  🔄 {key:<30} 有更新 (checksum: {sk["checksum"]} → {new_checksum})')
            else:
                print(f'  ✅ {key:<30} 已是最新')
        except Exception as e:
            errors.append(key)
            print(f'  ⚠️  {key:<28} 检查失败: {e}')

    print()
    if has_update:
        print(f'💡 发现更新！使用以下命令更新：')
        print(f'   python3 scripts/skill_manager.py update-remote --agent <agent> --name <skill>')
    else:
        print(f'✅ 所有 skills 均为最新版本')

    if errors:
        print(f'\n⚠️ {len(errors)} 个 skills 检查失败（可能是网络问题）')

    return True


def main():
    parser = argparse.ArgumentParser(
        description='三省六部 Skill 管理工具 (集成 ClawHub 商店)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s search-hub --query calendar     搜索 ClawHub 技能
  %(prog)s install-hub --agent menxia --slug caldav-calendar  安装技能
  %(prog)s import-official-hub             批量导入官方推荐技能
  %(prog)s add-remote --agent menxia --name my-skill --source <url>  从 URL 添加
  %(prog)s list-remote                     列出已安装的远程技能
  %(prog)s update-remote --agent menxia --name my-skill        更新技能
  %(prog)s check-updates                   检查所有技能更新
""",
    )
    subparsers = parser.add_subparsers(dest='cmd', help='命令')

    # add-remote (从 URL 下载单个 SKILL.md)
    add_parser = subparsers.add_parser('add-remote', help='从远程 URL 添加 skill')
    add_parser.add_argument('--agent', required=True, help='目标 Agent ID')
    add_parser.add_argument('--name', required=True, help='Skill 内部名称')
    add_parser.add_argument('--source', required=True, help='远程 URL (SKILL.md 文件地址)')
    add_parser.add_argument('--description', default='', help='Skill 描述')

    # list-remote
    subparsers.add_parser('list-remote', help='列出所有远程 skills')

    # update-remote
    update_parser = subparsers.add_parser('update-remote', help='更新远程 skill')
    update_parser.add_argument('--agent', required=True, help='Agent ID')
    update_parser.add_argument('--name', required=True, help='Skill 名称')

    # remove-remote
    remove_parser = subparsers.add_parser('remove-remote', help='移除远程 skill')
    remove_parser.add_argument('--agent', required=True, help='Agent ID')
    remove_parser.add_argument('--name', required=True, help='Skill 名称')

    # search-hub (搜索 ClawHub)
    search_parser = subparsers.add_parser('search-hub', help='搜索 ClawHub 技能商店')
    search_parser.add_argument('--query', required=True, help='搜索关键词')
    search_parser.add_argument('--limit', type=int, default=10, help='返回数量上限 (默认 10)')

    # install-hub (从 ClawHub 安装)
    install_parser = subparsers.add_parser('install-hub', help='从 ClawHub 安装技能 (slug)')
    install_parser.add_argument('--agent', required=True, help='目标 Agent ID')
    install_parser.add_argument('--slug', required=True, help='技能 slug (从 search-hub 获取)')
    install_parser.add_argument('--name', default='', help='本地技能名称 (默认从 slug 转换)')

    # import-official-hub (批量导入)
    import_parser = subparsers.add_parser('import-official-hub', help='从 ClawHub 批量导入推荐 skills')
    import_parser.add_argument('--agents', default='', help='逗号分隔的 Agent IDs（可选，不指定则用推荐配置）')

    # check-updates
    subparsers.add_parser('check-updates', help='检查所有远程 skills 是否有更新')

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == 'add-remote':
        success = add_remote(args.agent, args.name, args.source, args.description)
        sys.exit(0 if success else 1)

    elif args.cmd == 'list-remote':
        success = list_remote()
        sys.exit(0 if success else 1)

    elif args.cmd == 'update-remote':
        success = update_remote(args.agent, args.name)
        sys.exit(0 if success else 1)

    elif args.cmd == 'remove-remote':
        success = remove_remote(args.agent, args.name)
        sys.exit(0 if success else 1)

    elif args.cmd == 'search-hub':
        success = search_hub(args.query, args.limit)
        sys.exit(0 if success else 1)

    elif args.cmd == 'install-hub':
        success = install_hub(args.agent, args.slug, args.name or None)
        sys.exit(0 if success else 1)

    elif args.cmd == 'import-official-hub':
        agent_list = [a.strip() for a in args.agents.split(',') if a.strip()] if args.agents else []
        success = import_official_hub(agent_list)
        sys.exit(0 if success else 1)

    elif args.cmd == 'check-updates':
        success = check_updates()
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
