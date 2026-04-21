# 翰林院修撰 · 架构设计任务模板

## Prompt 模板

```
sessions_spawn hanlin_xiuzhuan {
  【角色】翰林院修撰，从五品
  【任务】为小说《{小说名}》设计完整创作架构
  【背景】{皇帝旨意摘要}
  【要求】
    1. 设计全书大纲 outline.md，含分卷分章规划，每章需有标题和内容摘要
    2. 设计世界观 worldview.md，含时代背景、力量体系、地理设定、社会制度
    3. 设计人物档案 characters.md，含所有主要角色的详细档案
    4. 输出章节规划 chapter_plan.json
  【格式】
    - outline.md: Markdown格式，用##表示卷，###表示章
    - worldview.md: Markdown格式，分模块组织
    - characters.md: Markdown格式，每个角色一个##标题
    - chapter_plan.json: JSON数组，每项含 {id, title, summary}
  【输出路径】data/hanlin/projects/《小说名》/
}
```

## 输出规范

### outline.md 格式
```markdown
# 《小说名》

## 第一卷 卷名
### 第1章 章名
内容摘要（100-200字，描述本章核心剧情）

### 第2章 章名
内容摘要...
```

### worldview.md 格式
```markdown
# 世界观设定

## 时代背景
...

## 力量/能力体系
...

## 地理设定
...

## 社会制度
...

## 核心矛盾
...
```

### characters.md 格式
```markdown
# 人物档案

## 主角名
- **身份**: ...
- **性格**: 关键词1 / 关键词2 / 关键词3
- **外貌**: ...
- **核心动机**: ...
- **人物弧光**: ...
- **人际关系**: ...

## 配角名
...
```

### chapter_plan.json 格式
```json
[
  {
    "id": 1,
    "volume": 1,
    "volumeName": "卷名",
    "title": "第1章 章名",
    "summary": "100-200字摘要",
    "pov": "视角角色",
    "keyCharacters": ["角色1", "角色2"],
    "estimatedWords": 2000
  }
]
```
