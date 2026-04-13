# ═══════════════════════════════════════════════════════════════════════════
# [TaskOutput] kanban_update.py 修改说明
# ═══════════════════════════════════════════════════════════════════════════
#
# 修改位置：scripts/kanban_update.py
#
# 在文件顶部的 import 区域之后（约第 46 行 log = ... 之后），
# 添加以下常量定义：
#
# ── [TaskOutput] 新增 ──
# 产出存储根目录
# _OUTPUTS_DIR = _BASE / 'data' / 'outputs'
#
#
# 然后在 cmd_create() 函数中（搜索 "def cmd_create"），
# 在 "tasks.insert(0, new_task)" 这行之前，添加：
#
#     # [TaskOutput] 创建任务时自动初始化产出目录
#     try:
#         output_dir = _OUTPUTS_DIR / task_id
#         output_dir.mkdir(parents=True, exist_ok=True)
#         # 初始化 manifest
#         import json as _json
#         manifest = {
#             'taskId': task_id,
#             'taskTitle': title,
#             'createdAt': now_iso(),
#             'artifacts': [],
#             'totalSize': 0,
#         }
#         (output_dir / 'manifest.json').write_text(
#             _json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
#         )
#     except Exception as e:
#         log.warning(f'创建产出目录失败: {e}')
#
# 效果：每个新任务创建时自动在 data/outputs/{taskId}/ 下建好目录和 manifest.json，
# Agent 执行时只需把产出文件放入对应部门子目录即可。
