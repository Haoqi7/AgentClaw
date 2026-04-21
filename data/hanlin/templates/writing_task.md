# 翰林院编修 · 写作任务模板

## Prompt 模板

```
sessions_spawn hanlin_bianxiu {
  【角色】翰林院编修，正五品
  【任务】根据大纲写作《{小说名}》第{N}章「{章节标题}」
  【背景】
    - 完整大纲内容: {outline.md全文}
    - 世界观摘要: {worldview.md关键设定}
    - 前章摘要: {前一章或前几章的剧情摘要}
    - 相关人物信息: {本章涉及角色的档案}
    - 本章大纲要求: {chapter_plan中本章的summary}
  【要求】
    1. 字数约2000字
    2. 严格遵循大纲中的本章内容摘要
    3. 保持与前文的连贯性
    4. 保持角色性格一致
    5. 输出为纯Markdown文本
  【格式】以 # 第{N}章 {章节标题} 开头，正文分段
  【输出路径】data/hanlin/projects/《小说名》/chapters/chapter_{NNN}.md
}
```
