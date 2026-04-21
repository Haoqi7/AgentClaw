# 翰林院检讨 · 审核任务模板

## Prompt 模板

```
sessions_spawn hanlin_jiantao {
  【角色】翰林院检讨，从七品
  【任务】审核《{小说名}》第{N}章「{章节标题}」
  【背景】
    - 本章大纲要求: {summary}
    - 前文摘要: {前章摘要}
    - 本章正文: {chapter文件全文}
  【要求】
    审核维度：文笔质量、情节逻辑、角色一致性、情感张力、叙事节奏
    问题分级：
    - 致命(🔴): 必须修改，如情节自相矛盾、角色严重OOC
    - 重要(🟡): 建议修改，如节奏拖沓、逻辑瑕疵
    - 建议(🟢): 优化项，如文笔提升、细节补充
  【格式】输出JSON到 chapter_{NNN}.review.json
}
```

## 审核报告格式

```json
{
  "chapter": 1,
  "title": "第1章 章名",
  "wordCount": 2034,
  "overall": "pass | revision_required | reject",
  "score": {
    "writing": 8,
    "logic": 9,
    "character": 8,
    "emotion": 7,
    "pacing": 8
  },
  "issues": [
    {
      "level": "致命|重要|建议",
      "dimension": "文笔质量|情节逻辑|角色一致性|情感张力|叙事节奏",
      "location": "段落位置",
      "detail": "问题描述",
      "suggestion": "修改建议"
    }
  ],
  "highlights": ["亮点1", "亮点2"],
  "reviewedAt": "ISO时间"
}
```

## 判定规则

| 条件 | overall |
|------|---------|
| 致命问题 > 0 | reject |
| 无致命但 重要 >= 4 | revision_required |
| 无致命且 重要 <= 3 | pass |
