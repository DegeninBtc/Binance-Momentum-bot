# 共享云端 IDE (code-server) & 远程开发部署指南

本目录包含了一套基于 Docker 部署的共享云端开发环境。它提供：
1. **Web 浏览器访问**：直接通过网页浏览器打开 VS Code 界面。
2. **SSH 远程通道**：允许你和你的伙伴通过**本地 VS Code (使用 Remote-SSH 插件)** 及各类 **AI 编程助手 (Codex / Cursor / Copilot)** 直接接入容器内部进行协同开发。

容器已预装 **Python 3**、**Node.js (v20)** 及 **Playwright 运行依赖**。

---

## 1. 部署前准备

在部署这套环境前，请确保服务器上已安装：
1. **Docker**: [官方安装指南](https://docs.docker.com/get-docker/)
2. **Docker Compose**: 通常已随 Docker Desktop/Engine 自动安装。

---

## 2. 一键部署步骤

1. **拉取代码**：
   在服务器上克隆本项目代码库，并进入 `docker-ide` 目录：
   ```bash
   git clone <你的项目Git仓库地址>
   cd <项目目录>/docker-ide
   ```

2. **修改配置**：
   使用编辑器打开 [docker-compose.yml](file:///c:/Users/Philo/Documents/bn%E8%87%AA%E5%8A%A8%E5%8C%96%E4%BA%A4%E6%98%93/docker-ide/docker-compose.yml)：
   * 修改 `PASSWORD`：网页 IDE 与 SSH 连接的共同登录密码（强烈建议改成复杂的安全密码）。
   * 修改 `SUDO_PASSWORD`：容器内执行 sudo 命令所需的密码。
   * 如果服务器在国内，下载 GitHub/pip/npm 慢，可以取消注释并修改 `HTTP_PROXY` 与 `HTTPS_PROXY` 的代理配置。

3. **启动容器**：
   执行以下命令构建并启动容器：
   ```bash
   docker compose up -d --build
   ```

---

## 3. 如何让 AI 助手 (Codex) & 本地 VS Code 连接协作？

为了实现最丝滑的团队协作，建议你和朋友**都使用本地 VS Code 连接到远程 Docker 容器中**。

### 连接方法：

1. **本地 VS Code 安装插件**：
   在本地 VS Code 中安装微软官方扩展：`Remote - SSH`。

2. **添加 SSH 连接**：
   按快捷键 `Ctrl+Shift+P` (Mac 上为 `Cmd+Shift+P`)，输入 `SSH: Connect to Host...` -> `Add New SSH Host...`，输入连接命令：
   ```ssh
   ssh coder@<服务器IP地址> -p 2222
   ```
   *(注：端口 `2222` 是我们在 docker-compose 中映射的 SSH 端口)*。

3. **保存并连接**：
   选择将配置保存到默认的 SSH 配置文件中。再次点击连接，选择该主机，根据提示选择 `Linux` 系统，并输入你在 `docker-compose.yml` 中配置的 `PASSWORD`（默认值：`your_secure_password`）。

4. **打开项目目录**：
   连接成功后，在 VS Code 中点击 **Open Folder（打开文件夹）**，选择路径：
   ```text
   /home/coder/project
   ```

5. **配置 AI 助手 (Codex / Cursor / Copilot 等)**：
   * **VS Code 插件模式**：在连接了 SSH 的 VS Code 窗口中，打开插件市场，找到你的 AI 编程助手（如 Copilot, Cline, Roo Code 等），点击 **"Install in SSH: <主机名>"**。
   * **Cursor 等第三方 IDE 模式**：如果你使用的是 Cursor，直接使用其自带的 Remote-SSH 功能连接即可，AI 会自动加载并运行在容器环境下。
   * **Antigravity 等独立 Agent 模式**：由于容器内已经完整安装了 Node.js、Python 和 Git，你可以直接在远程 VS Code 的终端 (Terminal) 中运行 Agent 程序，它将拥有读写代码和执行命令的完整权限。

---

## 4. 在 IDE 中进行项目初始化

连接进入 `/home/coder/project` 后，打开 VS Code 终端，运行以下命令：

### Python 环境初始化
```bash
# 国内源加速安装（可选）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Playwright 的浏览器二进制文件
playwright install chromium
```

### Node.js 环境初始化
```bash
# 国内源加速安装（可选）
npm config set registry https://registry.npmmirror.com

# 安装依赖
npm install
```

---

## 5. 运行与调试

* **运行交易 Bot**：`python binance_square_momentum_bot.py`
* **启动 Vite 前端服务**：`npm run dev`（启动后可在本地浏览器通过 `http://<服务器IP>:5173` 访问调试）
* **启动后台 Dashboard 服务**：`python web_dashboard.py`（访问端口 `8000`）
