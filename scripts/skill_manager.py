#!/usr/bin/env python3
"""
三省六部 · Skill 管理工具
支持从本地或远程 URL 添加、更新、查看和移除 skills

Usage:
  python3 scripts/skill_manager.py add-remote --agent zhongshu --name code_review \\
    --source https://raw.githubusercontent.com/org/skills/main/code_review/SKILL.md \\
    --description "代码审查"
  
  python3 scripts/skill_manager.py list-remote
  
  python3 scripts/skill_manager.py update-remote --agent zhongshu --name code_review
  
  python3 scripts/skill_manager.py remove-remote --agent zhongshu --name code_review
  
  python3 scripts/skill_manager.py import-official-hub --agents zhongshu,menxia,shangshu
"""
import sys
import json
import pathlib
import argparse
import os
import urllib.request
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
    """计算内容的简单校验和"""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ClawHub 可用性预检
def _check_hub_available(timeout: int = 10) -> bool:
    """检测 ClawHub 是否可达（请求 API 健康检查端点）"""
    try:
        health_url = _get_clawhub_api_url('/skills')
        req = urllib.request.Request(health_url, method='HEAD',
                                      headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # 404 也说明服务端可达（只是该端点不存在）
        if e.code < 500:
            return True
        return False
    except Exception:
        return False


def add_remote(agent_id: str, name: str, source_url: str, description: str = '') -> bool:
    """从远程 URL 为 Agent 添加 skill"""
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
                remote_skills.append({
                    'agent': agent_id,
                    'skill': skill_name,
                    'source': source_info.get('sourceUrl', 'N/A'),
                    'desc': source_info.get('description', ''),
                    'added': source_info.get('addedAt', 'N/A'),
                })
            except Exception:
                pass
    
    if not remote_skills:
        print('📭 暂无远程 skills')
        return True
    
    print(f'📋 共 {len(remote_skills)} 个远程 skills：\n')
    print(f'{"Agent":<12} | {"Skill 名称":<20} | {"描述":<30} | 添加时间')
    print('-' * 100)
    
    for sk in remote_skills:
        desc = (sk['desc'] or sk['source'])[:30].ljust(30)
        print(f"{sk['agent']:<12} | {sk['skill']:<20} | {desc} | {sk['added'][:10]}")
    
    print()
    return True


# [Fix 2] update_remote 不再委托给 add_remote，独立下载并保留原始 addedAt
def update_remote(agent_id: str, name: str) -> bool:
    """更新远程 skill 为最新版本（保留原始 addedAt）"""
    if not safe_name(agent_id) or not safe_name(name):
        print(f'❌ 错误：agent_id 或 skill 名称含非法字符')
        return False
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / name
    source_json = workspace / '.source.json'
    skill_md = workspace / 'SKILL.md'
    
    if not source_json.exists():
        print(f'❌ 技能不存在或不是远程 skill: {name}')
        return False
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl')
        if not source_url:
            print(f'❌ 无效的源 URL')
            return False
        
        original_added_at = source_info.get('addedAt', now_iso())
        description = source_info.get('description', '')
        old_checksum = source_info.get('checksum', '')
        
        # 下载最新版本
        print(f'⏳ 正在从 {source_url} 更新...')
        content = _download_file(source_url)
        
        # 基础验证
        if len(content.strip()) < 10:
            print(f'❌ 下载内容过短或为空')
            return False
        
        new_checksum = _compute_checksum(content)
        
        # 保存 SKILL.md
        skill_md.write_text(content)
        
        # 保留原始 addedAt，只更新 lastUpdated 和 checksum
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


# ── 官方 Skill 商店 ─────────────────────────────────────────────────
# ClawHub: https://clawhub.ai/
CLAWHUB_API_BASE = 'https://clawhub.ai'

# 支持通过环境变量覆盖 ClawHub 地址
_CLAWHUB_ENV = 'OPENCLAW_CLAWHUB_BASE'

def _get_clawhub_url(skill_name):
    """获取 skill 在 ClawHub 上的 SKILL.md 文件内容 URL
    
    端点: GET /api/v1/skills/{slug}/file?path=SKILL.md
    优先级: 本地配置文件 > 环境变量 > 默认值
    """
    base = (OCLAW_HOME / 'clawhub-url').read_text().strip() \
        if (OCLAW_HOME / 'clawhub-url').exists() else None
    base = base or os.environ.get(_CLAWHUB_ENV) or CLAWHUB_API_BASE
    return f'{base.rstrip("/")}/api/v1/skills/{skill_name}/file?path=SKILL.md'


def _get_clawhub_api_url(path=''):
    """获取 ClawHub API URL"""
    base = (OCLAW_HOME / 'clawhub-url').read_text().strip() \
        if (OCLAW_HOME / 'clawhub-url').exists() else None
    base = base or os.environ.get(_CLAWHUB_ENV) or CLAWHUB_API_BASE
    return f'{base.rstrip("/")}/api/v1{path}'


# 官方内置 skill 列表（可从 ClawHub API 动态获取，此处为默认值）
OFFICIAL_SKILLS_HUB = {
    'code_review': _get_clawhub_url('code_review'),
    'api_design': _get_clawhub_url('api_design'),
    'security_audit': _get_clawhub_url('security_audit'),
    'data_analysis': _get_clawhub_url('data_analysis'),
    'doc_generation': _get_clawhub_url('doc_generation'),
    'test_framework': _get_clawhub_url('test_framework'),
}

SKILL_AGENT_MAPPING = {
    'code_review': ('bingbu', 'xingbu', 'menxia'),
    'api_design': ('bingbu', 'gongbu', 'menxia'),
    'security_audit': ('xingbu', 'menxia'),
    'data_analysis': ('hubu', 'menxia'),
    'doc_generation': ('libu', 'menxia'),
    'test_framework': ('gongbu', 'xingbu', 'menxia'),
}


def import_official_hub(agent_ids: list) -> bool:
    """从 ClawHub 官方商店 (https://clawhub.ai/) 导入 skills"""
    use_recommended = not agent_ids

    if use_recommended:
        print('📋 未指定 agent，使用推荐配置...\n')

    # 预检 ClawHub 可用性
    print('🔍 正在检测 ClawHub (https://clawhub.ai/) 可用性...')
    hub_reachable = _check_hub_available()

    if not hub_reachable:
        print(f'\n⚠️ ClawHub (https://clawhub.ai/) 当前不可达')
        print(f'   可能原因：')
        print(f'   1. 网络问题（需要代理访问外网）')
        print(f'   2. ClawHub 服务暂时不可用')
        print(f'\n   建议：')
        print(f'   1. 检查网络: curl -I https://clawhub.ai/')
        print(f'   2. 设置代理: export https_proxy=http://your-proxy:port')
        print(f'   3. 自定义地址: export {_CLAWHUB_ENV}=https://your-mirror')
        print(f'   4. 或写文件: echo "https://your-mirror" > ~/.openclaw/clawhub-url')
        print(f'   5. 手动添加: python3 scripts/skill_manager.py add-remote --agent <agent> --name <skill> --source <url>')
        return False

    print(f'   ✅ ClawHub 可达，开始导入...\n')

    # 尝试从 ClawHub API 动态获取 skill 列表
    skills_to_import = {}
    try:
        list_url = _get_clawhub_api_url('/skills')
        req = urllib.request.Request(list_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            api_data = json.loads(resp.read().decode('utf-8'))
        # API 返回格式: {"skills": [{"slug": "...", "name": "...", "description": "..."}]}
        # 使用 /api/v1/skills/{slug}/file?path=SKILL.md 获取文件内容
        api_skills = api_data.get('skills', [])
        if api_skills:
            print(f'   📦 从 ClawHub 获取到 {len(api_skills)} 个官方 skills\n')
            for sk in api_skills:
                slug = sk.get('slug', '') or sk.get('name', '')
                if slug:
                    skills_to_import[slug] = _get_clawhub_url(slug)
    except Exception:
        print(f'   ⚠️ ClawHub API 获取列表失败，使用内置默认列表\n')

    # 如果 API 未返回数据，使用内置列表
    if not skills_to_import:
        skills_to_import = dict(OFFICIAL_SKILLS_HUB)

    total = 0
    success = 0
    failed = []

    for skill_name, url in skills_to_import.items():
        if use_recommended:
            target_agents = SKILL_AGENT_MAPPING.get(skill_name, ['menxia'])
        else:
            target_agents = agent_ids

        print(f'📥 正在导入 skill: {skill_name}')
        print(f'   目标 agents: {", ".join(target_agents)}')

        for agent_id in target_agents:
            total += 1
            ok = add_remote(agent_id, skill_name, url, f'官方 skill (ClawHub): {skill_name}')
            if ok:
                success += 1
            else:
                failed.append(f'{agent_id}/{skill_name}')

    print(f'\n📊 导入完成：{success}/{total} 个 skills 成功')
    if failed:
        print(f'\n❌ 失败列表:')
        for f in failed:
            print(f'   - {f}')
        print(f'\n💡 排查建议:')
        print(f'   1. 浏览 ClawHub: https://clawhub.ai/')
        print(f'   2. 设置代理: export https_proxy=http://your-proxy:port')
        print(f'   3. 自定义地址: export {_CLAWHUB_ENV}=https://your-mirror')
        print(f'   4. 单独重试: python3 scripts/skill_manager.py add-remote --agent <agent> --name <skill> --source <url>')
    return success == total


# [Fix 6] 实现 check-updates 子命令：遍历所有远程 skill，下载最新版本对比 checksum
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
        try:
            content = _download_file(sk['url'], timeout=15, retries=1)
            new_checksum = _compute_checksum(content)
            key = f"{sk['agent']}/{sk['name']}"
            if new_checksum != sk['checksum']:
                has_update = True
                print(f'  🔄 {key:<30} 有更新 (checksum: {sk["checksum"]} → {new_checksum})')
            else:
                print(f'  ✅ {key:<30} 已是最新')
        except Exception as e:
            errors.append(f"{sk['agent']}/{sk['name']}: {e}")
            print(f'  ⚠️  {sk["agent"]}/{sk["name"]:<28} 检查失败: {e}')
    
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
    parser = argparse.ArgumentParser(description='三省六部 Skill 管理工具', 
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest='cmd', help='命令')
    
    # add-remote
    add_parser = subparsers.add_parser('add-remote', help='从远程 URL 添加 skill')
    add_parser.add_argument('--agent', required=True, help='目标 Agent ID')
    add_parser.add_argument('--name', required=True, help='Skill 内部名称')
    add_parser.add_argument('--source', required=True, help='远程 URL 或本地路径')
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
    
    # import-official-hub
    import_parser = subparsers.add_parser('import-official-hub', help='从官方库导入 skills')
    import_parser.add_argument('--agents', default='', help='逗号分隔的 Agent IDs（可选）')
    
    # check-updates
    check_parser = subparsers.add_parser('check-updates', help='检查所有远程 skills 是否有更新')
    
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
    
    elif args.cmd == 'import-official-hub':
        agent_list = [a.strip() for a in args.agents.split(',') if a.strip()] if args.agents else []
        success = import_official_hub(agent_list)
        sys.exit(0 if success else 1)
    
    elif args.cmd == 'check-updates':
        success = check_updates()
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
