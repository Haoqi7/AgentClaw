#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
[TaskOutput] 产出管理后端 — 独立模块，追加到 dashboard/server.py 即可使用
═══════════════════════════════════════════════════════════════════════════════

安装说明：
1. 将此文件内容追加到 dashboard/server.py 文件末尾（在 class Handler 定义之前）
2. 在 Handler.do_GET 中添加 /api/outputs/ 路由（见下方注释）
3. 在 Handler.do_POST 中添加 /api/outputs/ 路由（见下方注释）

所有新增代码都用 #[TaskOutput] 开始 和 # [TaskOutput] 结束 标注，方便定位和移除。

API 端点：
  GET  /api/outputs/:taskId                   → 获取任务产出文件列表
  GET  /api/outputs/:taskId/download/:file    → 下载产出文件
  GET  /api/outputs/:taskId/preview/:file     → 预览产出文件（文本文件）
  POST /api/outputs/:taskId/upload            → 上传产出文件（multipart/form-data）
  POST /api/outputs/:taskId/delete            → 删除产出文件

存储结构：
  data/outputs/{taskId}/
  ├── manifest.json          # 产出清单（自动维护）
  ├── 中书省/
  │   └── 方案.md
  ├── 工部/
  │   └── main.py
  └── 礼部/
      └── 文档.docx
"""

import shutil as _shutil
import cgi as _cgi
import io as _io

# [TaskOutput] 产出存储根目录（改为 openclaw 统一目录，Docker 持久化）
_OUTPUTS_DIR = OCLAW_HOME / 'outputs'


# [TaskOutput] 辅助函数

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
    # 防止路径遍历
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


# [TaskOutput] API 处理函数

def handle_output_list(task_id: str) -> dict:
    """获取任务的产出文件列表（递归扫描文件系统，支持嵌套子目录）"""
    try:
        tasks = load_tasks()
        task = next((t for t in tasks if t.get('id') == task_id), None)
        if not task:
            return {'ok': False, 'error': f'任务 {task_id} 不存在'}
        
        output_dir = _get_output_dir(task_id)
        if not output_dir.exists():
            return {
                'ok': True, 'taskId': task_id,
                'taskTitle': task.get('title', ''),
                'artifacts': [], 'totalSize': 0,
            }

        # 尝试加载 manifest 以获取 uploadedAt 等元数据
        manifest = _load_manifest(task_id)
        manifest_map = {}
        for a in manifest.get('artifacts', []):
            key = a.get('path', '')
            if key:
                manifest_map[key] = a

        artifacts = []
        # 递归扫描所有子目录
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

            # 优先使用 manifest 中的元数据
            meta = manifest_map.get(rel_path) or manifest_map.get(rel_path.replace(os.sep, '/'))
            artifacts.append({
                'name': f.name,
                'dept': dept,
                'type': f.suffix.lstrip('.').lower(),
                'size': f.stat().st_size,
                'path': rel_path,
                'subfolder': subfolder,
                'uploadedAt': meta.get('uploadedAt', '') if meta else datetime.datetime.fromtimestamp(f.stat().st_mtime, tz=datetime.timezone.utc).isoformat(),
            })

        totalSize = sum(a.get('size', 0) for a in artifacts)
        # 回写 manifest 保持同步
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
    """下载产出文件（支持嵌套子目录路径）"""
    try:
        if not _SAFE_NAME_RE.match(task_id):
            handler.send_error(400, 'invalid task_id')
            return
        
        output_dir = _get_output_dir(task_id)
        if not output_dir.exists():
            handler.send_error(404, 'output directory not found')
            return
        
        # 支持嵌套路径: '工部/subfolder/file.py' 或 'file.py'
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
        
        # 推断 MIME 类型
        ext = found_file.suffix.lower()
        mime = _MIME_TYPES.get(ext, 'application/octet-stream')
        
        # 读取文件
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
        
        # 文本预览，限制 100KB
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
        
        # 解析 multipart/form-data
        content_type = handler.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return {'ok': False, 'error': '需要 multipart/form-data 格式'}
        
        # 获取 boundary
        boundary = None
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part.split('=', 1)[1].strip('"')
                break
        
        if not boundary:
            return {'ok': False, 'error': '缺少 boundary'}
        
        # 读取请求体
        length = int(handler.headers.get('Content-Length', 0))
        if length > 50 * 1024 * 1024:  # 50MB 上限
            return {'ok': False, 'error': '文件过大（最大 50MB）'}
        
        body = handler.rfile.read(length) if length else b''
        
        # 解析表单数据
        form = _cgi.FieldStorage(
            fp=_io.BytesIO(body),
            headers=handler.headers,
            environ={
                'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': content_type,
            }
        )
        
        # 提取文件和部门
        file_item = form['file']
        dept = form.getvalue('dept', '尚书省')
        
        if not file_item.filename:
            return {'ok': False, 'error': '未选择文件'}
        
        # 准备存储路径
        output_dir = _get_output_dir(task_id)
        dept_dir = output_dir / dept
        dept_dir.mkdir(parents=True, exist_ok=True)
        
        safe_name = _safe_filename(file_item.filename)
        file_path = dept_dir / safe_name
        
        # 避免重名
        if file_path.exists():
            name, ext = os.path.splitext(safe_name)
            counter = 1
            while file_path.exists():
                file_path = dept_dir / f'{name}_{counter}{ext}'
                counter += 1
            safe_name = file_path.name
        
        # 写入文件
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
        
        # 更新 manifest
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
        # 计算总大小
        manifest['totalSize'] = sum(a.get('size', 0) for a in manifest['artifacts'])
        _save_manifest(task_id, manifest)
        
        # 同步更新任务的 output 字段
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


# [TaskOutput] 安装说明 — 请将以下路由代码添加到 server.py 对应位置

_INSTALL_NOTES = """
===============================================================================
安装步骤（共 3 处修改）：
===============================================================================

步骤 1/3：将本文件的全部函数定义（从 _OUTPUTS_DIR 到 handle_output_delete）
         追加到 server.py 中 class Handler 定义之前。

步骤 2/3：在 Handler.do_GET 方法中，找到以下代码：
         elif p.startswith('/api/task-output/'):
         在它之前，添加以下路由（注意缩进 8 空格）：

        elif p.startswith('/api/outputs/'):
            parts = p.split('/')
            if len(parts) >= 5 and parts[3] == 'download':
                # GET /api/outputs/:taskId/download/:filename
                task_id = parts[2]
                filename = '/'.join(parts[4:])
                handle_output_download(task_id, filename, self)
                return
            elif len(parts) >= 5 and parts[3] == 'preview':
                # GET /api/outputs/:taskId/preview/:filename
                task_id = parts[2]
                filename = '/'.join(parts[4:])
                self.send_json(handle_output_preview(task_id, filename))
            elif len(parts) >= 4 and parts[3] not in ('download', 'preview'):
                # GET /api/outputs/:taskId
                task_id = parts[2]
                if not task_id:
                    self.send_json({'ok': False, 'error': 'task_id required'}, 400)
                else:
                    self.send_json(handle_output_list(task_id))
            else:
                self.send_json({'ok': False, 'error': 'invalid path'}, 400)

步骤 3/3：在 Handler.do_POST 方法中，找到路由分支的末尾（return 之前），
         添加以下路由（注意缩进 8 空格）：

        elif p.startswith('/api/outputs/') and '/upload' in p:
            task_id = p.replace('/api/outputs/', '').replace('/upload', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                result = handle_output_upload(task_id, self)
                self.send_json(result)
        elif p.startswith('/api/outputs/') and '/delete' in p:
            body_data = json.loads(raw) if raw else {}
            task_id = p.replace('/api/outputs/', '').replace('/delete', '')
            filename = body_data.get('filename', '')
            if not task_id or not filename:
                self.send_json({'ok': False, 'error': 'task_id and filename required'}, 400)
            else:
                self.send_json(handle_output_delete(task_id, filename))

注意：所有 #[TaskOutput] 标注的代码块都可以整块删除以回滚。
===============================================================================
"""
