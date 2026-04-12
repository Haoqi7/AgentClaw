# AgentClaw V8 — 三省六部协作系统

> 一个基于「看板即消息总线」的多 Agent 协作框架。
> 12 个 AI Agent 模拟古代三省六部，自动协作完成你的任务。

## 这是什么？

你可以把它想象成一个**AI 朝廷**：

```
你（皇上）下旨 → 太子接旨 → 中书省起草方案 → 门下省审议
    → 尚书省派发 → 六部执行 → 中书省回奏 → 太子回奏给你
```

你只需要说一句话（下旨），12 个 AI 就会自动走完整个流程，最终把结果交给你。

**它能做什么？**
- 写文章、做数据分析、写代码、做方案……任何你想让 AI 做的事
- 多个 Agent 分工协作，每个 Agent 负责自己的专业领域
- 全程可追踪，随时在看板上查看进度

---

## 快速开始

### 方式一：Docker 一键部署（推荐小白使用）

**前提**：你的服务器上已经装好了 Docker 和 Docker Compose。

```bash
# 1. 下载代码
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw

# 2. 创建数据目录（Docker 会把数据存在这里，不会丢失）
mkdir -p data logs openclaw-home

# 3. 配置 API Key（重要！没有 API Key Agent 无法工作）
# 先启动容器完成初始化，然后进入容器配置：
docker compose -f docker/docker-compose.yml up -d

# 4. 进入容器配置 API Key（只需配置一次）
docker exec -it agentclaw-all-in-one bash
# 在容器内执行：
openclaw onboard          # 按提示完成初始化（输入你的 API Key）
exit

# 5. 重启容器使配置生效
docker compose -f docker/docker-compose.yml restart

# 6. 打开浏览器访问看板
# http://你的服务器IP:7891
```

**就这么简单，看到看板页面就说明部署成功了。**

> **如何通过 GitHub Actions 自动构建 Docker 镜像？**
> 仓库里已经配好了两条自动化流水线：
> - `master.yml`：构建镜像并推送到 Docker Hub（需要在 GitHub 仓库 Settings → Secrets 中配置 `DOCKER_USERNAME` 和 `DOCKER_TOKEN`）
> - `main.yml`：构建 amd64 + arm64 两个平台的镜像包，上传到 GitHub Release 供下载
>
> 使用方法：GitHub 仓库页面 → Actions → 选择 workflow → Run workflow → 输入版本号（如 `v1.0.0`）→ 点击运行

### 方式二：手动部署（适合有经验的用户）

```bash
# 1. 下载代码
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw

# 2. 安装依赖
# 需要提前安装好：Python 3.8+、Node.js 18+、openclaw CLI
# openclaw 安装参考：https://openclaw.ai

# 3. 一键安装（创建 Workspace、注册 Agent、初始化数据）
bash install.sh

# 4. 配置 API Key（按提示操作）
openclaw agents add taizi
# 重新运行安装脚本同步 Key 到所有 Agent
bash install.sh

# 5. 一键启动（启动所有后台进程）
bash start.sh
# 按 Ctrl+C 停止所有进程

# 6. 打开浏览器访问看板
# http://127.0.0.1:7891
```

---

## 使用方法

部署完成后，打开看板页面（`http://你的IP:7891`），你可以：

### 1. 下旨（创建任务）

在看板上点击「下旨」按钮，输入你的需求，例如：

- "帮我写一篇关于人工智能的文章"
- "分析一下最近一个月的销售数据"
- "开发一个用户登录功能"

系统会自动走完整个流程：太子分拣 → 中书省规划 → 门下省审议 → 尚书省派发 → 六部执行 → 回奏结果

### 2. 查看进度

看板上会实时显示每个任务的当前状态：

| 状态 | 含义 | 谁在处理 |
|------|------|----------|
| Taizi | 皇上刚下旨，太子正在接旨 | 太子 |
| Zhongshu | 中书省正在起草方案 | 中书省 |
| Menxia | 门下省正在审议方案 | 门下省 |
| Assigned | 门下省已准奏，尚书省正在派发 | 尚书省 |
| Doing | 六部正在执行任务 | 各部 |
| Review | 执行完成，尚书省正在审查 | 尚书省 |
| Zhongshu_Final | 中书省正在撰写回奏 | 中书省 |
| Done | 任务完成 | - |
| Blocked | 任务被阻塞 | - |

### 3. 管理任务

- **叫停**：随时暂停正在执行的任务
- **恢复**：恢复被暂停的任务
- **取消**：彻底取消任务
- **归档**：把已完成的任务归档，保持看板整洁

---

## 十二官署说明

| 机构 | 对应 Agent | 职责 |
|------|-----------|------|
| 太子 | taizi | 接收皇上旨意，分拣归类后转交中书省 |
| 中书省 | zhongshu | 起草执行方案，撰写回奏报告 |
| 门下省 | menxia | 审议方案，可准奏或封驳（最多 2 次封驳） |
| 尚书省 | shangshu | 执行调度，将任务派发给合适的六部 |
| 户部 | hubu | 数据分析、统计、报表 |
| 礼部 | libu | 文档撰写、内容创作 |
| 兵部 | bingbu | 功能开发、技术实现 |
| 刑部 | xingbu | 审查测试、质量把控 |
| 工部 | gongbu | 部署运维、基础架构 |
| 吏部 | libu_hr | 人事管理、资源配置 |
| 钦天监 | zaochao | 早朝简报、信息汇总 |
| 御史台 | jiancha | 流程监察、异常纠正 |

---

## 项目文件结构

```
AgentClaw/
├── install.sh                      # 一键安装脚本（首次部署运行一次）
├── start.sh                        # 一键启动脚本（每次启动运行）
├── uninstall.sh                    # 一键卸载脚本
│
├── scripts/                        # 核心程序代码
│   ├── config.py                   # 全局配置（常量、路径、超时等）
│   ├── file_lock.py                # 文件锁（防止并发冲突）
│   ├── kanban_commands.py          # 看板命令协议（消息增删改查）
│   ├── kanban_update.py            # 看板命令 CLI（Agent 调用入口）
│   ├── agent_notifier.py           # Agent 唤醒模块
│   ├── pipeline_orchestrator.py    # 编排引擎（系统大脑，5 秒轮询）
│   ├── pipeline_watchdog.py        # 监察脚本（检测停滞、异常）
│   └── run_loop.sh                 # 数据刷新循环（后台常驻）
│
├── agents/                         # Agent 灵魂提示词
│   ├── taizi/SOUL.md               # 太子的人设和职责定义
│   ├── zhongshu/SOUL.md            # 中书省的人设和职责定义
│   ├── menxia/SOUL.md              # 门下省的人设和职责定义
│   └── ...                         # 其他部门同理
│
├── dashboard/                      # 看板前端 + 后端
│   ├── server.py                   # 看板 HTTP 服务器（端口 7891）
│   └── dist/                       # 前端页面（React 构建）
│
├── docker/                         # Docker 部署相关
│   ├── Dockerfile                  # 镜像构建文件
│   ├── docker-compose.yml          # Docker Compose 配置
│   └── entrypoint.sh               # 容器启动脚本
│
├── edict/                          # 前端源码（Vue/React）
│   ├── frontend/                   # 前端项目
│   └── backend/                    # 后端 API
│
├── data/                           # 运行数据（自动生成）
│   ├── tasks_source.json           # 看板主数据
│   └── ...                         # 其他数据文件
│
└── logs/                           # 运行日志（自动生成）
```

---

## 三个脚本的分工

| 脚本 | 做什么 | 什么时候用 |
|------|--------|-----------|
| `install.sh` | 安装初始化：创建 Workspace、注册 Agent、复制提示词、初始化数据、构建前端 | **首次部署时运行一次**；以后添加新 Agent 或更新提示词时再跑 |
| `start.sh` | 启动运行：启动 Gateway、编排引擎、数据循环、看板服务器 | **每次启动系统时运行**（服务器重启后也要跑） |
| `uninstall.sh` | 完全卸载：清理所有 Agent 注册、Workspace、数据 | 不想用了的时候 |

简单来说：
```
第一次：bash install.sh  →  bash start.sh
以后每次：bash start.sh
```

Docker 部署不需要手动运行这些脚本，`entrypoint.sh` 会自动处理。

---

## 系统运行时有哪些进程？

系统启动后，后台会运行 4 个进程：

| 进程 | 作用 | 说明 |
|------|------|------|
| OpenClaw Gateway | Agent 通信网关 | 端口 18789，所有 Agent 通信走这里 |
| pipeline_orchestrator | 编排引擎 | 系统大脑，每 5 秒扫描看板决定下一步 |
| run_loop.sh | 数据刷新循环 | 每 15 秒刷新数据，每 60 秒跑监察，每 2 分钟检查 Gateway 健康状况 |
| dashboard/server.py | 看板 HTTP 服务 | 端口 7891，你看到的网页界面 |

它们之间会互相监控：如果 Gateway 挂了，run_loop 会自动重启；如果看板服务挂了，run_loop 也会自动重启。**你不需要手动维护。**

---

## 核心机制说明

### 封驳机制

门下省审议中书省的方案时，可以选择：
- **准奏**（approve）：方案通过，进入执行阶段
- **封驳**（reject）：打回修改，中书省需要重新起草

每个任务最多封驳 **2 次**，第 3 次系统会自动强制准奏（防止无限循环）。

### 停滞检测

如果一个任务在某个状态停留太久：
- **3 分钟没动静**：系统自动催办当前负责的 Agent
- **6 分钟没动静**：系统自动上报给御史台（监察部门）处理

### 监察系统

御史台负责流程完整性检查，发现问题会自动纠正，比如：任务卡在错误的状态、流程断链等。

### 文件锁

所有数据读写都通过文件锁保证安全，即使多个进程同时操作同一个文件也不会出错。

---

## 常见问题

### Q: 部署后看板打不开？

1. 检查端口 7891 是否被占用：`netstat -tlnp | grep 7891`
2. 检查防火墙是否放行端口
3. Docker 部署检查容器是否在运行：`docker ps`

### Q: Agent 不响应 / 报错？

1. 检查 API Key 是否配置正确：`openclaw agents list`
2. 检查 Gateway 是否在运行（端口 18789）
3. 查看日志：`logs/` 目录下的日志文件

### Q: 如何更新代码？

```bash
cd AgentClaw
git pull
# 如果有新的 Agent 或提示词更新：
bash install.sh
# 然后重启：
# Docker: docker compose -f docker/docker-compose.yml restart
# 手动: 先 Ctrl+C 停止 start.sh，再重新 bash start.sh
```

### Q: 如何备份数据？

所有重要数据都在 `data/` 目录下，只需备份这个目录即可：
```bash
cp -r data/ data_backup_$(date +%Y%m%d)/
```

Docker 部署的数据在项目根目录的 `data/` 和 `openclaw-home/` 中，这两个目录已通过 volumes 映射到宿主机。

### Q: 如何修改封驳次数上限？

编辑 `scripts/config.py`，找到这一行：
```python
MAX_REJECT_COUNT = 2    # 门下省最大封驳次数
```
修改数字后重启系统生效。

---

## 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 7891 | 看板 Dashboard | 浏览器访问的网页界面 |
| 18789 | OpenClaw Gateway | Agent 通信网关（一般不需要直接访问） |

---

## 技术要求

| 项目 | 最低要求 |
|------|---------|
| Python | 3.8+ |
| Node.js | 18+（仅构建前端需要） |
| openclaw CLI | 最新版（Agent 通信必需） |
| Docker（可选） | 20.10+ |
| 内存 | 建议 2GB 以上 |
| 磁盘 | 建议 10GB 以上 |

---

## License

MIT
