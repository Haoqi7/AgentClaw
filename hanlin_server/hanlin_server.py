#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翰林院独立HTTP服务
端口: 7892
纯Python标准库 http.server 实现
提供小说项目管理、章节内容、审核结果、实时进度等API
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote

# ── 配置 ──────────────────────────────────────────────

PORT = 7892
REPO_DIR = Path(__file__).resolve().parent.parent
HANLIN_DATA_DIR = REPO_DIR / 'data' / 'hanlin'
PROJECTS_DIR = HANLIN_DATA_DIR / 'projects'
CONFIG_FILE = HANLIN_DATA_DIR / 'config.json'

BJT = timezone(timedelta(hours=8))

# 确保目录存在
HANLIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 工具函数 ──────────────────────────────────────────

def json_response(handler, data, status=200):
    """发送JSON响应，带CORS头"""
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def read_json(path, default=None):
    """安全读取JSON文件"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, IOError):
        pass
    return default if default is not None else {}

def write_json(path, data):
    """安全写入JSON文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def read_text(path):
    """安全读取文本文件"""
    try:
        if path.exists():
            return path.read_text(encoding='utf-8')
    except IOError:
        pass
    return ''

def write_text(path, content):
    """安全写入文本文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')

def count_chinese_chars(text):
    """统计中文字符数（含标点）"""
    return len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))

def now_iso():
    """当前北京时间ISO格式"""
    return datetime.now(BJT).strftime('%Y-%m-%dT%H:%M:%S')

def list_projects():
    """列出所有项目"""
    projects = []
    if not PROJECTS_DIR.exists():
        return projects
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir():
            meta = read_json(d / 'meta.json')
            if meta:
                progress = read_json(d / 'progress.json', {})
                projects.append({
                    'name': meta.get('title', d.name),
                    'genre': meta.get('genre', ''),
                    'style': meta.get('style', ''),
                    'status': meta.get('status', 'unknown'),
                    'currentChapter': progress.get('currentChapter', 0),
                    'totalChapters': meta.get('totalChapters', 0),
                    'totalWords': progress.get('totalWords', 0),
                    'createdAt': meta.get('createdAt', ''),
                    'updatedAt': meta.get('updatedAt', ''),
                })
    return projects

def get_project_dir(name):
    """获取项目目录"""
    # 支持带《》和不带的名称
    if name.startswith('《') and name.endswith('》'):
        return PROJECTS_DIR / name
    return PROJECTS_DIR / f'《{name}》'

def safe_name(name):
    """安全化项目名称"""
    # 移除危险字符
    name = re.sub(r'[<>:"/\\|?*]', '', name.strip())
    if not name.startswith('《'):
        name = f'《{name}》'
    if not name.endswith('》'):
        name = f'{name}》'
    return name

# ── 路由处理器 ────────────────────────────────────────

def handle_projects(handler, method):
    """GET /api/projects — 列出所有项目"""
    if method == 'GET':
        projects = list_projects()
        stats = {
            'totalProjects': len(projects),
            'totalWords': sum(p['totalWords'] for p in projects),
            'activeProjects': sum(1 for p in projects if p['status'] not in ('completed', 'cancelled')),
        }
        json_response(handler, {'projects': projects, 'stats': stats})

    elif method == 'POST':
        # 创建新项目
        content_len = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(content_len).decode('utf-8')
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            json_response(handler, {'error': '无效的JSON'}, 400)
            return

        title = data.get('title', '').strip()
        if not title:
            json_response(handler, {'error': '标题不能为空'}, 400)
            return

        safe_title = safe_name(title)
        project_dir = PROJECTS_DIR / safe_title
        if project_dir.exists():
            json_response(handler, {'error': f'项目 {safe_title} 已存在'}, 409)
            return

        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / 'chapters').mkdir(exist_ok=True)

        ts = now_iso()
        meta = {
            'title': safe_title,
            'genre': data.get('genre', ''),
            'style': data.get('style', ''),
            'requirements': data.get('requirements', ''),
            'status': 'planning',
            'currentPhase': 'architecture',
            'currentChapter': 0,
            'totalChapters': 0,
            'totalWords': 0,
            'createdAt': ts,
            'updatedAt': ts,
            'outlineEditedAt': None,
        }
        write_json(project_dir / 'meta.json', meta)
        write_json(project_dir / 'progress.json', {
            'currentPhase': 'architecture',
            'currentChapter': 0,
            'totalChapters': 0,
            'currentAgent': '',
            'currentTask': '等待掌院学士初始化',
            'totalWords': 0,
            'chapterStatus': {},
        })

        json_response(handler, {'ok': True, 'project': safe_title, 'path': str(project_dir)}, 201)


def handle_project_detail(handler, method, name):
    """GET /api/projects/{name} — 项目详情"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    meta = read_json(project_dir / 'meta.json')
    progress = read_json(project_dir / 'progress.json', {})
    chapter_plan = read_json(project_dir / 'chapter_plan.json', [])

    # 收集章节信息
    chapters_dir = project_dir / 'chapters'
    chapters = []
    if chapters_dir.exists():
        for f in sorted(chapters_dir.glob('chapter_*.md')):
            ch_num = int(re.search(r'chapter_(\d+)', f.stem).group(1))
            content = read_text(f)
            review = read_json(f.parent / f'{f.stem}.review.json')
            chapters.append({
                'id': ch_num,
                'title': f'第{ch_num}章',
                'wordCount': count_chinese_chars(content),
                'status': review.get('overall', 'done') if review else ('done' if content else 'pending'),
                'hasReview': bool(review),
                'reviewResult': review.get('overall', '') if review else '',
                'issueCount': {
                    'fatal': len([i for i in review.get('issues', []) if i.get('level') == '致命']),
                    'important': len([i for i in review.get('issues', []) if i.get('level') == '重要']),
                    'suggestion': len([i for i in review.get('issues', []) if i.get('level') == '建议']),
                } if review else None,
            })

    # 审核汇总
    all_reviews = []
    for ch in chapters:
        review_file = chapters_dir / f'chapter_{ch["id"]:03d}.review.json'
        review = read_json(review_file)
        if review:
            all_reviews.append(review)

    review_summary = {
        'totalReviewed': len(all_reviews),
        'totalFatal': sum(len([i for i in r.get('issues', []) if i.get('level') == '致命']) for r in all_reviews),
        'totalImportant': sum(len([i for i in r.get('issues', []) if i.get('level') == '重要']) for r in all_reviews),
        'totalSuggestion': sum(len([i for i in r.get('issues', []) if i.get('level') == '建议']) for r in all_reviews),
    }

    result = {
        'meta': meta,
        'progress': progress,
        'chapters': chapters,
        'chapterPlan': chapter_plan,
        'reviewSummary': review_summary,
        'hasOutline': (project_dir / 'outline.md').exists(),
        'hasWorldview': (project_dir / 'worldview.md').exists(),
        'hasCharacters': (project_dir / 'characters.md').exists(),
    }
    json_response(handler, result)


def handle_outline(handler, method, name):
    """GET/PUT /api/projects/{name}/outline — 大纲读写"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    outline_path = project_dir / 'outline.md'

    if method == 'GET':
        content = read_text(outline_path)
        meta = read_json(project_dir / 'meta.json')
        json_response(handler, {
            'content': content,
            'editedAt': meta.get('outlineEditedAt'),
            'lastModified': os.path.getmtime(outline_path) if outline_path.exists() else None,
        })

    elif method == 'PUT':
        content_len = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(content_len).decode('utf-8')
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            json_response(handler, {'error': '无效的JSON'}, 400)
            return

        new_content = data.get('content', '')
        write_text(outline_path, new_content)

        # 更新meta标记大纲被编辑
        meta = read_json(project_dir / 'meta.json')
        ts = now_iso()
        meta['outlineEditedAt'] = ts
        meta['updatedAt'] = ts
        write_json(project_dir / 'meta.json', meta)

        json_response(handler, {'ok': True, 'editedAt': ts})


def handle_worldview(handler, method, name):
    """GET /api/projects/{name}/worldview — 世界观"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    content = read_text(project_dir / 'worldview.md')
    json_response(handler, {'content': content})


def handle_characters(handler, method, name):
    """GET /api/projects/{name}/characters — 人物档案"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    content = read_text(project_dir / 'characters.md')
    json_response(handler, {'content': content})


def handle_chapter_plan(handler, method, name):
    """GET /api/projects/{name}/chapter_plan — 章节规划"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    plan = read_json(project_dir / 'chapter_plan.json', [])
    json_response(handler, {'plan': plan})


def handle_chapters(handler, method, name):
    """GET /api/projects/{name}/chapters — 章节列表"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    chapters_dir = project_dir / 'chapters'
    chapters = []
    chapter_plan = read_json(project_dir / 'chapter_plan.json', [])

    # 构建章节规划映射
    plan_map = {}
    for p in chapter_plan:
        plan_map[p.get('id', 0)] = p

    if chapters_dir.exists():
        for f in sorted(chapters_dir.glob('chapter_*.md')):
            match = re.search(r'chapter_(\d+)', f.stem)
            if match:
                ch_num = int(match.group(1))
                content = read_text(f)
                review = read_json(f.parent / f'{f.stem}.review.json')
                plan_info = plan_map.get(ch_num, {})
                chapters.append({
                    'id': ch_num,
                    'title': plan_info.get('title', f'第{ch_num}章'),
                    'summary': plan_info.get('summary', ''),
                    'wordCount': count_chinese_chars(content),
                    'status': review.get('overall', 'done') if review else ('writing' if content else 'pending'),
                    'hasReview': bool(review),
                    'reviewResult': review.get('overall', '') if review else '',
                })

    json_response(handler, {'chapters': chapters})


def handle_chapter_content(handler, method, name, ch_num):
    """GET /api/projects/{name}/chapters/{n} — 章节全文"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    chapter_file = project_dir / 'chapters' / f'chapter_{int(ch_num):03d}.md'
    content = read_text(chapter_file)
    review = read_json(project_dir / 'chapters' / f'chapter_{int(ch_num):03d}.review.json')

    # 获取章节标题
    chapter_plan = read_json(project_dir / 'chapter_plan.json', [])
    plan_info = {}
    for p in chapter_plan:
        if p.get('id') == int(ch_num):
            plan_info = p
            break

    json_response(handler, {
        'id': int(ch_num),
        'title': plan_info.get('title', f'第{ch_num}章'),
        'content': content,
        'wordCount': count_chinese_chars(content),
        'review': review,
        'plan': plan_info,
    })


def handle_chapter_review(handler, method, name, ch_num):
    """GET /api/projects/{name}/chapters/{n}/review — 章节审核"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    review_file = project_dir / 'chapters' / f'chapter_{int(ch_num):03d}.review.json'
    review = read_json(review_file)
    if not review:
        json_response(handler, {'error': '审核报告不存在'}, 404)
        return

    json_response(handler, review)


def handle_progress(handler, method, name):
    """GET /api/projects/{name}/progress — 实时进度"""
    project_dir = get_project_dir(name)
    if not project_dir.exists():
        json_response(handler, {'error': f'项目 {name} 不存在'}, 404)
        return

    progress = read_json(project_dir / 'progress.json', {})
    meta = read_json(project_dir / 'meta.json', {})
    json_response(handler, {'progress': progress, 'meta': meta})


def handle_status(handler):
    """GET /api/status — 服务状态"""
    projects = list_projects()
    status = {
        'service': 'running',
        'port': PORT,
        'timestamp': now_iso(),
        'projects': {
            'total': len(projects),
            'active': sum(1 for p in projects if p['status'] not in ('completed', 'cancelled')),
            'totalWords': sum(p['totalWords'] for p in projects),
        },
        'dataDir': str(HANLIN_DATA_DIR),
    }
    json_response(handler, status)


# ── HTTP Handler ──────────────────────────────────────

class HanlinHandler(BaseHTTPRequestHandler):
    """翰林院API请求处理器"""

    def log_message(self, format, *args):
        """简化日志输出"""
        print(f'[hanlin:{PORT}] {args[0]}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_PUT(self):
        self._route('PUT')

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        parts = [unquote(p) for p in path.split('/') if p]

        # 去掉 api 前缀
        if parts and parts[0] == 'api':
            parts = parts[1:]

        try:
            # GET /api/status
            if len(parts) == 1 and parts[0] == 'status' and method == 'GET':
                handle_status(self)
                return

            # GET/POST /api/projects
            if len(parts) == 1 and parts[0] == 'projects':
                handle_projects(self, method)
                return

            # /api/projects/{name}/...
            if len(parts) >= 2 and parts[0] == 'projects':
                name = parts[1]

                # GET /api/projects/{name}
                if len(parts) == 2 and method == 'GET':
                    handle_project_detail(self, method, name)
                    return

                # /api/projects/{name}/outline
                if len(parts) == 3 and parts[2] == 'outline' and method in ('GET', 'PUT'):
                    handle_outline(self, method, name)
                    return

                # /api/projects/{name}/worldview
                if len(parts) == 3 and parts[2] == 'worldview' and method == 'GET':
                    handle_worldview(self, method, name)
                    return

                # /api/projects/{name}/characters
                if len(parts) == 3 and parts[2] == 'characters' and method == 'GET':
                    handle_characters(self, method, name)
                    return

                # /api/projects/{name}/chapter_plan
                if len(parts) == 3 and parts[2] == 'chapter_plan' and method == 'GET':
                    handle_chapter_plan(self, method, name)
                    return

                # /api/projects/{name}/chapters
                if len(parts) == 3 and parts[2] == 'chapters' and method == 'GET':
                    handle_chapters(self, method, name)
                    return

                # /api/projects/{name}/chapters/{n}
                if len(parts) == 4 and parts[2] == 'chapters' and method == 'GET':
                    handle_chapter_content(self, method, name, parts[3])
                    return

                # /api/projects/{name}/chapters/{n}/review
                if len(parts) == 5 and parts[2] == 'chapters' and parts[4] == 'review' and method == 'GET':
                    handle_chapter_review(self, method, name, parts[3])
                    return

                # /api/projects/{name}/progress
                if len(parts) == 3 and parts[2] == 'progress' and method == 'GET':
                    handle_progress(self, method, name)
                    return

            json_response(self, {'error': 'API not found', 'path': path}, 404)

        except Exception as e:
            json_response(self, {'error': str(e)}, 500)


# ── 启动服务 ──────────────────────────────────────────

def main():
    server = HTTPServer(('0.0.0.0', PORT), HanlinHandler)
    print(f'')
    print(f'  ╔══════════════════════════════════════╗')
    print(f'  ║  📚 翰林院独立服务已启动             ║')
    print(f'  ║  端口: {PORT:<31}║')
    print(f'  ║  数据目录: {str(HANLIN_DATA_DIR):<23}║')
    print(f'  ╚══════════════════════════════════════╝')
    print(f'')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n翰林院服务已停止')
        server.server_close()


if __name__ == '__main__':
    main()
