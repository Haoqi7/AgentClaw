# AgentClaw V8 — 三省六部协作架构

## 架构概述

AgentClaw V8 是一个基于「看板即消息总线」理念的多 Agent 协作框架。所有 Agent 之间的通信通过 JSON 看板文件完成，编排引擎负责轮询看板、决策派发、唤醒对应 Agent。

**核心设计原则：**
- Agent 只做「思考 + 写看板」，程序负责「读看板 → 决策 → 唤醒」
- 看板是唯一的信息通道，所有状态变更持久化到文件
- 确定性派发：由程序决定通知谁、何时通知，不依赖 LLM
- 异步主循环：asyncio + ThreadPoolExecutor，不阻塞扫描

---

## 一键部署（Docker）

```bash
# 1. 克隆项目
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw

# 2. Docker Compose 启动（全自动，首次启动约 2-3 分钟）
cd docker
docker compose up -d

# 3. 查看日志
docker compose logs -f

# 4. 打开看板
# 浏览器访问 http://你的服务器IP:7891
```

**Docker 会自动完成：**
- 安装 OpenClaw 并初始化 Gateway
- 运行 install.sh（创建 Workspace、注册 12 个 Agent、同步 API Key）
- 构建前端页面
- 启动全部 4 个后台进程（Gateway + 编排引擎 + 数据刷新 + 看板服务器）

**数据持久化：** `data/`、`logs/`、`openclaw-home/` 通过 Docker Volume 挂载，重启不丢失。

---

## 一键部署（本地 / 裸机）

```bash
# 1. 克隆项目
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw

# 2. 确保已安装 openclaw CLI 并完成初始化
openclaw --version

# 3. 运行安装脚本（首次运行，创建 Agent Workspace 和配置）
bash install.sh

# 4. 启动全部服务（一条命令）
bash start.sh
```

`start.sh` 会自动后台启动以下进程：
1. **OpenClaw Gateway**（端口 18789）
2. **编排引擎** `pipeline_orchestrator.py`（看板轮询 + Agent 派发）
3. **数据刷新循环** `run_loop.sh`（15 秒周期刷新 + 60 秒监察 + 健康检查）
4. **看板服务器** `server.py`（端口 7891，前台运行）

打开浏览器访问 `http://127.0.0.1:7891` 即可。

---

## 项目结构

```
AgentClaw/
├── scripts/                        # 核心程序代码
│   ├── config.py                   # 全局配置中心（常量、路径、超时、状态机）
│   ├── file_lock.py                # 跨平台文件锁（原子 JSON 读写）
│   ├── utils.py                    # 公共工具函数（时间、校验）
│   ├── kanban_commands.py          # 看板命令协议层（消息增删改查）
│   ├── kanban_update.py            # 看板命令 CLI 入口（供 Agent 调用）
│   ├── agent_notifier.py           # Agent 唤醒模块（openclaw 封装）
│   ├── pipeline_orchestrator.py    # 编排引擎主循环（5秒轮询看板）
│   ├── pipeline_watchdog.py        # 监察脚本（停滞检测、封驳循环、纠正）
│   ├── run_loop.sh                 # 数据刷新循环（含健康检查和自动重启）
│   └── test_v8_integration.py      # 集成测试
│
├── dashboard/
│   ├── server.py                   # HTTP 看板服务器（API + 静态文件）
│   └── dist/                       # 前端构建产物（React）
│
├── agents/                         # Agent 灵魂文件（SOUL.md + AGENTS.md）
│   ├── taizi/                      # 太子 — 皇上接口
│   ├── zhongshu/                   # 中书省 — 规划决策
│   ├── menxia/                     # 门下省 — 审议把关
│   ├── shangshu/                   # 尚书省 — 执行调度
│   ├── libu/                       # 礼部 — 文档撰写
│   ├── hubu/                       # 户部 — 数据分析
│   ├── bingbu/                     # 兵部 — 功能开发
│   ├── xingbu/                     # 刑部 — 审查测试
│   ├── gongbu/                     # 工部 — 部署运维
│   ├── libu_hr/                    # 吏部 — 人事管理
│   ├── jiancha/                    # 御史台 — 流程监察
│   └── zaochao/                    # 钦天监 — 早朝简报
│
├── docker/
│   ├── Dockerfile                  # Docker 镜像定义
│   ├── docker-compose.yml          # Docker Compose 编排
│   └── entrypoint.sh               # 容器启动脚本
│
├── data/                           # 看板数据文件
├── edict/                          # 多渠道通知后端（可选）
├── install.sh                      # 一键安装脚本
├── start.sh                        # 一键启动脚本
└── README.md                       # 本文件
```

---

## 系统架构（4 个进程）

```
┌──────────────────────────────────────────────────────────┐
│                    Docker 容器 / 服务器                    │
│                                                           │
│  ┌─────────────────┐    ┌────────────────────────────┐   │
│  │  OpenClaw        │    │  pipeline_orchestrator.py  │   │
│  │  Gateway         │    │  （编排引擎 - V8 核心）      │   │
│  │  :18789          │◄──│  每5秒轮询看板              │   │
│  │                  │    │  决策派发 + 唤醒 Agent      │   │
│  └─────────────────┘    └──────────┬─────────────────┘   │
│                                     │                     │
│  ┌─────────────────┐    ┌──────────▼─────────────────┐   │
│  │  run_loop.sh     │    │  dashboard/server.py        │   │
│  │  数据刷新循环     │    │  HTTP 看板服务器             │   │
│  │  + 监察 (60s)    │    │  :7891                     │   │
│  │  + 健康检查       │    │  API + 静态前端              │   │
│  └─────────────────┘    └────────────────────────────┘   │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  data/tasks_source.json（看板 — 唯一信息通道）        │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 九状态机

任务在整个系统中经历以下状态流转：

```
皇上旨意 → Taizi（太子分拣）
         → Zhongshu（中书省起草方案）
         → Menxia（门下省审议）
             ├── reject → Zhongshu（封驳，最多 2 轮）
             └── approve → Assigned（准奏）
                         → Doing（尚书省派发六部执行）
                         │   ├── → Review（六部全部完成）
                         │   └── → Blocked（阻塞）
                         │                ├── → Doing（恢复）
                         │                └── → Cancelled（取消）
                         → Zhongshu_Final（中书省撰写回奏）
                         → Done（太子回奏皇上）
```

| 当前状态 | 可转换到 |
|----------|----------|
| (新创建) | Taizi |
| Taizi | Zhongshu |
| Zhongshu | Menxia, Assigned |
| Menxia | Zhongshu（封驳）, Assigned（准奏） |
| Assigned | Doing |
| Doing | Review, Blocked |
| Review | Zhongshu_Final |
| Zhongshu_Final | Done, Zhongshu（需修改） |
| Blocked | Doing, Cancelled |
| Done / Cancelled | （终态） |

---

## 9 种看板命令

所有 Agent 通过以下 CLI 命令与看板通信：

| 命令 | 用法 | 说明 |
|------|------|------|
| `approve` | `kanban_update.py approve <id> "准奏意见"` | 门下省准奏 |
| `reject` | `kanban_update.py reject <id> "封驳意见"` | 门下省封驳 |
| `assign` | `kanban_update.py assign <id> <dept> "任务说明"` | 尚书省派发六部 |
| `done-v2` | `kanban_update.py done-v2 <id> "产出路径" "说明"` | 六部完成上报 |
| `report` | `kanban_update.py report <id> "产出" "说明"` | 汇总报告 |
| `ask` | `kanban_update.py ask <id> <目标部门> "问题"` | 请示/发消息 |
| `answer` | `kanban_update.py answer <id> <目标部门> "回答"` | 回复请示 |
| `escalate` | `kanban_update.py escalate <id> "原因"` | 异常上报 |
| `redirect` | `kanban_update.py redirect <id> <目标部门> "原因"` | 监察纠正（御史台专用） |

**管理命令：** `create`、`progress`、`todo`（详见 `kanban_update.py --help`）

---

## 编排引擎工作流程

`pipeline_orchestrator.py` 是系统的大脑，每 5 秒轮询一次看板：

```
主循环（每 5 秒一轮）
  │
  ├── 1. 扫描所有非终态任务
  │     ├── 检测状态变化 → dispatch_map 路由派发
  │     ├── 检查未读消息 → 9 种消息类型处理器
  │     ├── 检查待回答问题 → 重新通知
  │     └── 检查停滞（3分钟催办 / 6分钟上报监察）
  │
  ├── 2. 消息路由优先级
  │     ① redirect（监察纠正，最高优先级）
  │     ② escalate（异常上报）
  │     ③ ask / answer（对话消息）
  │     ④ approve / reject（状态转换）
  │     ⑤ assign / done / report（任务流转）
  │
  └── 3. 关键保障机制
        ├── 双重派发防护（快照同步）
        ├── 封驳上限（2次封驳后第3次强制准奏）
        ├── 原子文件操作（file_lock + tmpfile rename）
        └── 启动恢复（重启后自动补发未完成任务）
```

---

## 封驳机制

门下省对中书省方案进行审议：

- 每次封驳，系统递增 `reviewRound` 计数
- **最多 2 轮封驳**，第 3 次系统自动强制准奏
- 强制准奏时记录审计标记 `override` 到 `auditFlags`
- 强制准奏后自动通知尚书省进入派发阶段

---

## 停滞检测

两级停滞检测机制（编排引擎 + 监察脚本并行运作）：

| 级别 | 阈值 | 动作 |
|------|------|------|
| 催办 | 3 分钟无活动 | 通知当前负责 Agent |
| 上报 | 6 分钟无活动 | 向御史台发送 escalate 消息 |

基于 `task.last_activity` 时间戳判断，Agent 执行 `progress` 或任何看板命令都会更新此时间戳。

---

## 监察系统

`pipeline_watchdog.py` 由 `run_loop.sh` 每 60 秒自动调用一次（无需手动配置 cron）：

- **看板停滞检测**：基于 `last_activity` 时间戳
- **封驳循环检测**：`reviewRound >= MAX_REJECT_COUNT` 时记录违规
- **agentLog 异常扫描**：检测 ESCALATE、ERROR、HELP 等关键词
- **流转纠正**：通过 `redirect` 命令将错误流转重定向
- **自动归档**：Done/Cancelled 超过 5 分钟自动归档

---

## 数据文件

| 文件 | 说明 |
|------|------|
| `data/tasks_source.json` | 看板主数据（任务列表 + 全局计数器） |
| `data/pipeline_audit.json` | 监察审计日志 |
| `data/audit_exclude.json` | 手动排除的任务列表 |
| `data/watchdog_config.json` | 监察运行时配置 |

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `EDICT_HOME` | 项目根目录 | 自动推断 |
| `EDICT_DASHBOARD_PORT` | 看板服务器端口 | 7891 |
| `EDICT_GATEWAY_URL` | Gateway 内部地址 | http://127.0.0.1:18789 |
| `EDICT_EXTERNAL_URL` | 看板外部访问地址 | 从请求头推断 |
| `AGENTCLAW_LOG_LEVEL` | 日志级别 | INFO |
| `TZ` | 时区 | Asia/Shanghai |

---

## 常见问题

**Q: Docker 启动后看板打不开？**
A: 等待约 40 秒让容器完成初始化（Gateway 启动 + install.sh + 前端构建）。使用 `docker compose logs -f` 查看进度。

**Q: 如何查看编排引擎日志？**
A: Docker: `docker compose logs -f | grep orchestrator`。本地: 编排引擎日志输出到 stdout。

**Q: 如何修改封驳上限？**
A: 编辑 `scripts/config.py` 中的 `MAX_REJECT_COUNT`，然后重启编排引擎。

**Q: Agent 被唤醒但没有执行？**
A: 检查 OpenClaw Gateway 是否在线（端口 18789），以及 Agent 的 API Key 是否已配置。
