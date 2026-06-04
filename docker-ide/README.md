# 共享云端 IDE (code-server) 部署指南

本目录包含了一套基于 Docker 部署的共享云端开发环境。它不仅提供网页版 VS Code 界面，而且针对本项目预装了 **Python 3**、**Node.js (v20)** 以及 **Playwright 系统依赖**。

这使得你和你的伙伴可以同时接入服务器，在完全一致的运行环境中进行开发、调试和运行量化交易机器人。

---

## 1. 部署前准备

在部署这套环境前，请确保服务器上已安装：
1. **Docker**: [官方安装指南](https://docs.docker.com/get-docker/)
2. **Docker Compose**: 通常已随 Docker Desktop/Engine 自动安装。

---

## 2. 部署步骤

1. **拉取代码**：
   在服务器上克隆本项目代码库，并进入 `docker-ide` 目录：
   ```bash
   git clone <你的项目Git仓库地址>
   cd <项目目录>/docker-ide
   ```

2. **修改配置**（可选）：
   使用编辑器打开 [docker-compose.yml](file:///c:/Users/Philo/Documents/bn%E8%87%AA%E5%8A%A8%E5%8C%96%E4%BA%A4%E6%98%93/docker-ide/docker-compose.yml)：
   * 修改 `PASSWORD`：网页 IDE 的登录密码（建议改成复杂的密码）。
   * 修改 `SUDO_PASSWORD`：容器内执行 sudo 命令所需的密码。
   * 如果服务器在国内，下载 GitHub/pip/npm 慢，可以取消注释并修改 `HTTP_PROXY` 与 `HTTPS_PROXY` 的代理配置。

3. **启动容器**：
   执行以下命令构建并启动容器：
   ```bash
   docker compose up -d --build
   ```
   * 构建时会自动拉取 Debian 基础镜像、安装 Python 3, Node.js 以及 Playwright 所需的各种 Linux 动态链接库。

4. **访问网页版 IDE**：
   * 浏览器打开：`http://<服务器IP>:8080`
   * 输入你在 `docker-compose.yml` 中设置的 `PASSWORD` 即可进入云端 VS Code 界面。

---

## 3. 在 IDE 中进行项目初始化

登录网页端 VS Code 后，你会自动进入 `/home/coder/project`（即你的项目根目录）。请在 IDE 底部打开终端 (Terminal)，运行以下命令完成环境初始化：

### Python 环境初始化
由于我们设置了 `PIP_BREAK_SYSTEM_PACKAGES=1`，你可以直接在全局安装依赖，无需每次都激活虚拟环境：
```bash
# 国内源加速安装（可选）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Playwright 的浏览器二进制文件（重点：镜像中已提前装好系统依赖，这里只需安装浏览器）
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

## 4. 运行与调试

### 1. 启动量化交易 Bot 脚本
```bash
python binance_square_momentum_bot.py
```

### 2. 启动前端 Vite 调试服务器
```bash
npm run dev
```
启动后可在浏览器直接访问 `http://<服务器IP>:5173` 进行前端页面预览。

### 3. 启动后台 Dashboard 服务
```bash
python web_dashboard.py
```
启动后可在浏览器直接访问 `http://<服务器IP>:8000` 访问后端 API 或网页后台。

---

## 5. 常见问题 (FAQ)

### Q: 两个人在同一个工作区写代码会冲突吗？
* **文件同步**：code-server 本质上允许多个浏览器标签页同时连接同一个工作区。当你修改代码时，你的伙伴会实时看到屏幕上的字在变化，体验类似于 Google Docs 的协同（但需要注意不要同时修改同一行以防覆盖输入）。
* **Git 提交**：在容器内配置好各自的 git config（或使用 IDE 左侧的 Git 提交栏），直接推送到 Github 即可。

### Q: 怎么保存我们安装的 VS Code 插件？
* `docker-compose.yml` 已经把整个 `/home/coder` 目录挂载到了 Docker 卷 `code_server_home`。因此，你安装的任何插件、VS Code 配置主题、bash 历史记录都会在容器重启后完好保留。
