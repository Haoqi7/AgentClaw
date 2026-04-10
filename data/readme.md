
  "break_timeout_sec": 90,           // 断链超时：90秒（可调）
  "auto_archive_minutes": 5,         // 自动归档：完成后5分钟
  "recent_done_minutes": 10,         // 最近完成的任务也检查（防速通逃逸）
  "extreme_stall_threshold_sec": 1800, // 极端停滞：30分钟
  "review_grace_periods": {
    "Menxia": 300,                  // 门下省审议宽限期：5分钟
    "Doing": 180,                   // 执行中宽限期：3分钟
    "Assigned": 120                 // 已派发宽限期：2分钟

  "max_notifications": 200,         // 通报记录上限
  "max_violations": 200,            // 违规记录上限
  "max_archived_violations": 500,   // 归档违规上限
  "max_archived_notifications": 100 // 归档通报上限
