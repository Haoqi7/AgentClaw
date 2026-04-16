<h1 align="center">
  <br>
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/React-18.3-61DAFB?style=flat-square&logo=react" alt="React">
  <img src="https://img.shields.io/badge/TypeScript-5.6-3178C6?style=flat-square&logo=typescript" alt="TypeScript">
  <img src="https://img.shields.io/badge/Vite-6.0-646CFF?style=flat-square&logo=vite" alt="Vite">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
  <br><br>
  <img width="120" src="https://em-content.zobj.net/source/apple/391/scroll_1f4dc.png" alt="AgentClaw">
  <br><br>
  <b style="font-size:2.4em">三省六部 · AgentClaw</b>
  <br>
  <i style="font-size:1.1em;color:#888">以古代官僚体系为隐喻的多 Agent 协作编排系统</i>
</h1>

<p align="center">
  <b>你是皇帝，AI 是朝廷</b> — 一道旨意下发，三省六部自动流转、分工协作、汇报复命
</p>

<p align="center">
  <a href="#-功能特性">功能特性</a> ·
  <a href="#-系统架构">系统架构</a> ·
  <a href="#-快速上手">快速上手</a> ·
  <a href="#-仪表盘总控台">仪表盘</a> ·
  <a href="#-进阶用法">进阶用法</a> ·
  <a href="#docker-部署">Docker 部署</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Agents-12-purple?style=for-the-badge" alt="12 Agents">
  <img src="https://img.shields.io/badge/前端组件-18+-blue?style=for-the-badge" alt="18+ Components">
  <img src="https://img.shields.io/badge/消息渠道-7-green?style=for-the-badge" alt="7 Channels">
  <img src="https://img.shields.io/badge/圣旨模板-9-orange?style=for-the-badge" alt="9 Templates">
</p>

---

## 🌟 理念

> **与其造一个超级 AI，不如组一个朝廷。**

AgentClaw 将中国古代的「三省六部制」映射为多 Agent 协作架构。12 个 AI Agent 各司其职，通过严格的 9 步审批流程，将一道「旨意」（用户任务）从规划、审议、派发、执行到归档全链路自动化。

**不是 prompt 套 prompt 的简单链式调用，而是一套有编制、有流程、有监督、可回滚的真实组织体系。**

---

## ✨ 功能特性

### 🏛️ 三省六部协作体系

| 部门 | 职责 | 现实映射 |
|:---:|:---|:---|
| 🤴 **太子** | 旨意分拣、闲聊应答、任务分发入口 | CEO 助理 / 任务调度中心 |
| 📜 **中书省** | 需求分析、方案规划、任务拆解 | 产品经理 / 架构师 |
| 🔍 **门下省** | 方案审议、质量把关、驳回修正 | 技术评审委员会 / QA Lead |
| 📮 **尚书省** | 执行派发、六部调度、结果汇总 | 项目经理 / Scrum Master |
| 💰 **户部** | 数据分析、报表生成、竞品研究 | 数据分析师 |
| 📝 **礼部** | 文档撰写、UI 设计、内容创作 | 文案 / 设计师 |
| ⚔️ **兵部** | 代码开发、架构实现、技术攻坚 | 研发工程师 |
| ⚖️ **刑部** | 代码审查、质量检测、漏洞扫描 | 测试工程师 / SRE |
| 🔧 **工部** | 基础设施、部署运维、环境搭建 | DevOps / SRE |
| 👔 **吏部** | 人员管理、培训考核、组织优化 | HR / Team Lead |
| 📰 **钦天监** | 新闻采集、情报汇总、晨报推送 | 情报分析师 |
| 🛡️ **御史台** | 流程合规、越权检测、断链告警 | 独立审计 / 合规部门 |

### 🔁 严格的 9 步任务流转

```
皇帝(你) → 太子分拣 → 中书规划 → 门下审议(可驳回) → 尚书派发 → 六部执行 → 尚书汇总 → 太子复命 → 皇帝(你)
```

每个环节有独立的超时机制、催办策略和自动回滚能力。门下省可驳回不合理的方案（最多 3 轮），确保产出质量。

### 🧭 双引擎监督机制

| 引擎 | 频率 | 职责 |
|:---|:---:|:---|
| **太子巡检** | 每 60s | 任务停滞检测 → 分级催办 → 自动重试 → 升级协调 → 自动回滚 |
| **御史监察** | 每 60s | 越权调用检测 · 流程跳步检测 · 断链超时唤醒 · 假派发验证 · 极端停滞告警 |

两套系统协同工作：太子管「效率」（卡了就推），御史管「合规」（违规就记）。自研自适应算法，系统稳定时自动降噪，异常时全量告警。

### 📋 9 套圣旨模板

周报生成 · 代码审查 · API 设计 · 竞品分析 · 数据报告 · 博客文章 · 部署方案 · 邮件文案 · 站会摘要

填入参数一键下旨，无需手写复杂指令。

### 📰 天下要闻

钦天监每 4 小时自动采集 12+ RSS 源，覆盖 4 大类目：
- 🏛️ 政治 · ⚔️ 军事 · 💰 经济 · 🤖 AI 大模型

支持多渠道推送（飞书/Telegram/Slack/Webhook）。

### 📦 产出阁

所有 Agent 产出物按部门自动归档，支持在线预览（Markdown 渲染）和下载。无需上传，Agent 完工即归档。

### 🤖 灵活的模型配置

每个 Agent 独立配置 AI 模型，看板上一键切换，5 秒生效。可以为不同部门选择不同能力的模型。

---

## 🏗 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        皇帝 (用户)                              │
│                    飞书 / Telegram / Signal                     │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────────┐
│  🤴 太子 (Taizi) — 旨意分拣 · 闲聊应答 · 进度汇报              │
└────────────────────────────┬───────────────────────────────────┘
                             ▼
┌────────────────┐    ┌────────────────┐    ┌──────────────────┐
│ 📜 中书省      │───▶│ 🔍 门下省      │───▶│ 📮 尚书省        │
│ 规划 · 拆解    │◀───│ 审议 · 驳回    │    │ 派发 · 汇总      │
└────────────────┘    └────────────────┘    └───────┬──────────┘
                                                   │
              ┌────────┬────────┬────────┬─────────┼────────┐
              ▼        ▼        ▼        ▼         ▼        ▼
          ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
          │ 💰户部││ 📝礼部││ ⚔️兵部││ ⚖️刑部││ 🔧工部││ 👔吏部│
          └──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘
             └────────┴────────┴────────┴────────┴────────┘
                               │
                               ▼
                    ┌────────────────────┐
                    │  📦 产出阁 · 奏折阁  │
                    └────────────────────┘

  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  🛡️ 御史台 — 独立于行政体系之外的流程审计与合规监督
  🧭 太子巡检 — 嵌入任务流转全链路的停滞检测与自动修复
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
```

### 技术栈

| 层 | 技术 |
|:---|:---|
| **前端** | React 18 + TypeScript 5.6 + Vite 6 + Tailwind CSS + Zustand |
| **后端** | Python 3.10+（零依赖 HTTP Server）/ FastAPI（可选增强模式）|
| **Agent 运行时** | [OpenClaw](https://github.com/openclaw) — AI Agent 基础设施 |
| **数据库** | JSON 文件（零配置）/ PostgreSQL + Redis（可选）|
| **消息渠道** | 飞书 · Telegram · Slack · Discord · 企业微信 · Webhook |
| **部署** | Docker Compose 一键部署 / 裸机脚本安装 |

---

## 🚀 快速上手

### 前置条件

- **Node.js** >= 22（前端构建）
- **Python** >= 3.10
- **OpenClaw CLI** 已安装并初始化

### 第 1 步：安装 OpenClaw

```bash
npm install -g @qingchencloud/openclaw-zh@latest
openclaw onboard --install-daemon
openclaw gateway
```

初始化向导会引导你选择 AI 模型、配置 API 密钥、设置聊天通道。

### 第 2 步：安装三省六部

```bash
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw
chmod +x install.sh && ./install.sh
```

安装脚本会自动完成：
- ✅ 创建 12 个 Agent Workspace
- ✅ 写入各省部 SOUL.md 人格文件
- ✅ 注册 Agent 及权限矩阵
- ✅ 构建 React 前端
- ✅ 初始化数据目录

### 第 3 步：配置消息渠道

```bash
# 以飞书为例，太子作为旨意入口
openclaw channels add --type feishu --agent taizi
```
- 太子会自动识别闲聊和任务指令，指令类消息提炼标题后转发中书省。
- 可参考`openclaw.example.json`和`exec-approvals.json`来配置。

### 第 4 步：启动服务

```bash
# 终端 1：数据刷新循环（每 15 秒同步）
bash scripts/run_loop.sh &

# 终端 2：看板服务器
python3 dashboard/server.py
```

浏览器打开 `http://127.0.0.1:7891`，总控台就绪。

### 第 5 步：发送你的第一道旨意

通过消息渠道发送：

```
请帮我用 Python 写一个文本分类器：
1. 使用 scikit-learn
2. 支持多分类
3. 输出混淆矩阵
4. 写完整的文档
```

然后在总控台观察 12 位「官员」如何自动协作完成。

---

## 📊 仪表盘总控台

总控台提供 12 个功能面板：

| 面板 | 功能 |
|:---|:---|
| 📜 **旨意看板** | 任务全生命周期看板，支持手动派发、太子巡检、状态流转 |
| 🏛️ **朝堂议政** | 多 Agent 实时讨论，朝堂仪式化交互 |
| 🔌 **省部调度** | 各部门工作负载、任务分布、性能概览 |
| 📋 **旨库** | 9 套预设圣旨模板，填参数一键下旨 |
| 🛡️ **流程监察** | 御史台审计面板，查看违规记录、告警通知、巡检结果 |
| 📦 **产出阁** | 任务产出物按部门归档，在线预览（Markdown 渲染）+ 下载 |
| 📜 **奏折阁** | 已完成任务自动归档，随时回溯 |
| 💬 **小任务** | Agent 会话管理，轻量级子任务 |
| 🌅 **天下要闻** | 钦天监新闻简报，分类订阅 + 多渠道推送 |
| 👔 **官员总览** | 12 位 Agent 的能力画像、在线状态、绩效统计 |
| 🤖 **模型配置** | 每个 Agent 独立模型切换，一键应用 |
| 🎯 **技能配置** | Agent 技能管理，安装/卸载/配置 |

---

## 🔧 进阶用法

### 圣旨模板

> 看板 → 📋 旨库 → 选择模板 → 填写参数 → 下旨

### 叫停 / 取消任务

> 旨意看板 → 选择任务 → ⏸ 叫停 / 🚫 取消

### 模型热切换

> 看板 → 🤖 模型配置 → 选择模型 → 应用

约 5 秒后自动生效，无需重启。

### 新闻订阅

> 看板 → 🌅 天下要闻 → ⚙️ 订阅管理 → 配置分类和推送渠道

---


## 🐳 Docker 部署

本项目已发布至 Docker Hub，无需手动构建：

```bash
docker pull haoqi7/openclaw:latest
```

### 方式一：Docker Compose（推荐）

从 [Release](https://github.com/Haoqi7/AgentClaw/releases) 下载 `docker-compose.yml`，或直接创建：

```bash
docker compose -f docker/docker-compose.yml up -d
```

### 方式二：Docker 直接运行

```bash
docker run -d \
  --name agentclaw \
  -p 7891:7891 \
  -p 18789:18789 \
  -v /root/.openclaw:/root/.openclaw \
 haoqi7/openclaw:latest
```

### 配置说明

| 配置项 | 值 |
|:---|:---|
| 端口 | `7891`（总控台）、`18789`（OpenClaw Gateway）|
| 挂载卷 | `/root/.openclaw`（持久化存储）|
| 健康检查 | 每 15 秒双端点检测，自动恢复 |
| 重启策略 | `unless-stopped` |

### 环境变量（可选）

| 变量 | 说明 |
|:---|:---|
| `EDICT_EXTERNAL_URL` | 外部访问地址（如 `https://your-domain.com`）|
| `EDICT_CORS_ORIGINS` | 跨域白名单（逗号分隔）|



---

## 📁 项目结构

```
AgentClaw/
├── agents/                  # 12 个 Agent 人格定义
│   ├── taizi/SOUL.md       #   太子 — 旨意分拣入口
│   ├── zhongshu/SOUL.md    #   中书省 — 方案规划
│   ├── menxia/SOUL.md      #   门下省 — 方案审议
│   ├── shangshu/SOUL.md    #   尚书省 — 执行调度
│   ├── hubu/SOUL.md        #   户部 — 数据分析
│   ├── libu/SOUL.md        #   礼部 — 文档设计
│   ├── bingbu/SOUL.md      #   兵部 — 代码开发
│   ├── xingbu/SOUL.md      #   刑部 — 质量审查
│   ├── gongbu/SOUL.md      #   工部 — 运维部署
│   ├── libu_hr/SOUL.md     #   吏部 — 人事管理
│   ├── zaochao/SOUL.md     #   钦天监 — 新闻采集
│   └── jiancha/SOUL.md     #   御史台 — 流程审计
├── frontend/                # React + TypeScript 前端
│   └── src/components/      #   18 个组件
├── dashboard/               # 看板服务器 + API
│   ├── server.py           #   主服务（Python HTTP）
│   └── task_output_api.py  #   产出管理 API
├── scripts/                 # 自动化脚本
│   ├── run_loop.sh         #   主循环（数据同步 + 巡检）
│   ├── pipeline_watchdog.py#   流程合规审计引擎
│   ├── kanban_update.py    #   Agent 看板 CLI
│   └── fetch_morning_news.py# 新闻 RSS 采集
├── docker/                  # Docker 部署
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── install.sh               # 一键安装
└── uninstall.sh             # 一键卸载
```
---

## 🤝 衍生声明

本项目基于以下开源项目衍生开发：

- [OpenClaw](https://github.com/openclaw) — AI Agent 基础设施
- [OpenClawChineseTranslation](https://github.com/1186258278/OpenClawChineseTranslation) — 中文本地化
- [edict](https://github.com/cft0808/edict))


严格遵守上游项目的开源许可证协议。

## 📄 许可证

[MIT License](LICENSE) © 2026 AgentClaw Contributors
