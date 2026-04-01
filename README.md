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

### 第二步：克隆项目

```bash
git clone https://github.com/Haoqi7/AgentClaw.git
cd AgentClaw
```

> 说明：`install.sh` 不作为必需步骤（不建议依赖安装脚本）。

---

### 第三步：配置消息渠道（OpenClaw）

在 OpenClaw 中配置消息渠道（Feishu / Telegram / Signal），将 `taizi`（太子）Agent 设为旨意入口。

```bash
# 查看当前渠道
openclaw channels list

# 添加飞书渠道（入口设为太子）
openclaw channels add --type feishu --agent taizi
```

---

### 第四步：启动服务

```bash
# 终端 1：数据刷新循环（每 15 秒同步）
bash scripts/run_loop.sh

# 终端 2：看板���务器
python3 dashboard/server.py

# 打开浏览器
open http://127.0.0.1:7891
```

> 💡 `run_loop.sh` 每 15 秒自动同步数据，可用 `&` 后台运行。

---

### 第五步：发送第一道旨意

通过消息渠道发送任务（太子会自动识别并转发到中书省）：

```
请帮我用 Python 写一个文本分类器：
1. 使用 scikit-learn
2. 支持多分类
3. 输出混淆矩阵
4. 写完整的文档
```

---

## 🎯 进阶用法

- **使用圣旨模板**：看板 → 📜 旨库 → 选择模板 → 填写参数 → 下旨
- **切换 Agent 模型**：看板 → ⚙️ 模型配置 → 选择新模型 → 应用更改
- **管理技能**：看板 → 🛠️ 技能配置 → 查看已安装技能 → 添加新技能
- **叫停 / 取消任务**：任务详情中点击 ⏸ / 🚫

---

## ❓ 故障排查

### 看板显示「服务器未启动」
```bash
python3 dashboard/server.py
```

### Agent 不响应
```bash
openclaw gateway status
openclaw gateway restart
```

### 数据不更新
```bash
ps aux | grep run_loop
python3 scripts/refresh_live_data.py
```

---

## 📚 更多资源

- [快速上手指南](docs/getting-started.md)
- [贡献指南](CONTRIBUTING.md)
