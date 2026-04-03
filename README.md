<h1 align="center">⚔️ 三省六部 · AgentClaw</h1>

> 运行依赖 **OpenClaw**。请先完成 OpenClaw 安装与初始化，再继续安装/启动本项目。

---

## 🚀 快速上手

### 第一步：安装 OpenClaw（必须）

三省六部基于OpenClaw 运行，请先安装：

## 4 步上手

> **前提条件**：需要 **Node.js >= 22**（[下载 Node.js](https://nodejs.org/)）
>
> 检查版本：`node -v`

### 第 1 步：安装

```bash
npm install -g @qingchencloud/openclaw-zh@latest
```

### 第 2 步：初始化（推荐守护进程模式）

```bash
openclaw onboard --install-daemon
```

初始化向导会引导你完成：选择 AI 模型 → 配置 API 密钥 → 设置聊天通道

### 第 3 步：启动网关

```bash
openclaw gateway
```

### 第 4 步：打开控制台

```bash
openclaw dashboard
```

浏览器会自动打开全中文的 Dashboard 控制台。完成！



安装完成后初始化：

```bash
openclaw init
```

---



## 第二步：克隆并安装三省六部

```bash
git clone https://github.com/Haoqi7/AgentClaw.git
cd edict
chmod +x install.sh && ./install.sh
```

安装脚本会自动完成：
- ✅ 创建 12 个 Agent Workspace（`~/.openclaw/workspace-*`）
- ✅ 写入各省部 SOUL.md 人格文件
- ✅ 注册 Agent 及权限矩阵到 `openclaw.json`
- ✅ 配置旨意数据清洗规则
- ✅ 构建 React 前端到 `dashboard/dist/`（需 Node.js 18+）
- ✅ 初始化数据目录
- ✅ 执行首次数据同步
- ✅ 重启 Gateway 使配置生效

## 第三步：配置消息渠道

在 OpenClaw 中配置消息渠道（Feishu / Telegram / Signal），将 `taizi`（太子）Agent 设为旨意入口。太子会自动分拣闲聊与指令，指令类消息提炼标题后转发中书省。

```bash
# 查看当前渠道
openclaw channels list

# 添加飞书渠道（入口设为太子）
openclaw channels add --type feishu --agent taizi
```

参考 OpenClaw 文档：https://docs.openclaw.ai/channels

## 第四步：启动服务

```bash
# 终端 1：数据刷新循环（每 15 秒同步）
bash scripts/run_loop.sh

# 终端 2：看板服务器
python3 dashboard/server.py

# 打开浏览器
open http://127.0.0.1:7891
```

> 💡 **提示**：`run_loop.sh` 每 15 秒自动同步数据。可用 `&` 后台运行。

> 💡 **看板即开即用**：`server.py` 内嵌 `dashboard/dashboard.html`，无需额外构建。Docker 镜像包含预构建的 React 前端。

## 第五步：发送第一道旨意

通过消息渠道发送任务（太子会自动识别并转发到中书省）：

```
请帮我用 Python 写一个文本分类器：
1. 使用 scikit-learn
2. 支持多分类
3. 输出混淆矩阵
4. 写完整的文档
```

## 第六步：观察执行过程

打开看板 http://127.0.0.1:7891

1. **📋 旨意看板** — 观察任务在各状态之间流转
2. **🔭 省部调度** — 查看各部门工作分布
3. **📜 奏折阁** — 任务完成后自动归档为奏折

任务流转路径：
```
收件 → 太子分拣 → 中书规划 → 门下审议 → 已派发 → 执行中 → 已完成
```

---

## docker部署
从release下载对应版本，上传到docker。
开放port：'7891'，'17889'
挂载 '/root/,openclaw'
## 🎯 进阶用法

### 使用圣旨模板

> 看板 → 📜 旨库 → 选择模板 → 填写参数 → 下旨

9 个预设模板：周报生成 · 代码审查 · API 设计 · 竞品分析 · 数据报告 · 博客文章 · 部署方案 · 邮件文案 · 站会摘要

### 切换 Agent 模型

> 看板 → ⚙️ 模型配置 → 选择新模型 → 应用更改

约 5 秒后 Gateway 自动重启生效。

### 管理技能

> 看板 → 🛠️ 技能配置 → 查看已安装技能 → 点击添加新技能

### 叫停 / 取消任务

> 在旨意看板或任务详情中，点击 **⏸ 叫停** 或 **🚫 取消** 按钮

### 订阅天下要闻

> 看板 → 📰 天下要闻 → ⚙️ 订阅管理 → 选择分类 / 添加源 / 配飞书推送

---

## ❓ 故障排查

### 看板显示「服务器未启动」
```bash
# 确认服务器正在运行
python3 dashboard/server.py
```

### Agent 报错 "No API key found for provider"

这是最常见的问题。三省六部有 11 个 Agent，每个都需要 API Key。

```bash
# 方法一：为任意 Agent 配置后重新运行 install.sh（推荐）
openclaw agents add taizi          # 按提示输入 Anthropic API Key
cd edict && ./install.sh            # 自动同步到所有 Agent

# 方法二：手动复制 auth 文件
MAIN_AUTH=$(find ~/.openclaw/agents -name auth-profiles.json | head -1)
for agent in taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu; do
  mkdir -p ~/.openclaw/agents/$agent/agent
  cp "$MAIN_AUTH" ~/.openclaw/agents/$agent/agent/auth-profiles.json
done

# 方法三：逐个配置
openclaw agents add taizi
openclaw agents add zhongshu
# ... 其他 Agent
```

### Agent 不响应
```bash
# 检查 Gateway 状态
openclaw gateway status

# 必要时重启
openclaw gateway restart
```

### 数据不更新
```bash
# 检查刷新循环是否运行
ps aux | grep run_loop

# 手动执行一次同步
python3 scripts/refresh_live_data.py
```

### 心跳显示红色 / 告警
```bash
# 检查对应 Agent 的进程
openclaw agent status <agent-id>

# 重启指定 Agent
openclaw agent restart <agent-id>
```

### 模型切换后不生效
等待约 5 秒让 Gateway 重启完成。仍不生效则：
```bash
python3 scripts/apply_model_changes.py
openclaw gateway restart
```

---



### 📄 许可证
本项目 **AgentClaw** 基于 [MIT 许可证](LICENSE) 开源，您可以自由使用、修改和分发本项目代码，需遵循许可证相关声明。

#### 衍生项目声明
本项目基于以下两个开源项目衍生开发：
1. [OpenClawChineseTranslation](https://github.com/1186258278/OpenClawChineseTranslation)
2. [edict](https://github.com/cft0808/edict)

本项目严格遵守上述上游项目的开源许可证协议，保留所有原始版权与授权声明。
