FROM codercom/code-server:4.91.1-debian

USER root

# 使用 Debian 镜像源（针对国内服务器，可选配置，这里默认使用官方源，若慢可换清华源/阿里源）
# 安装 Python3, pip, venv, git, curl, build-essential 编译工具及 Node.js
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    git \
    build-essential \
    sudo \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 安装 Playwright 的系统依赖库（用于无头浏览器运行，例如自动化交易脚本里的网页抓取）
# 这里我们全局安装 playwright 并运行 install-deps，完成后卸载全局包，以减小体积
RUN npm install -g playwright && npx playwright install-deps && npm uninstall -g playwright

# 允许 coder 用户免密执行 sudo 权限，方便后续在终端里安装其他系统级依赖
RUN echo "coder ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/nopasswd

# 设置 Python 环境变量，允许在系统全局（容器内）使用 pip 安装包而不会报 EXTERNALLY-MANAGED 错误
ENV PIP_BREAK_SYSTEM_PACKAGES=1

USER coder

# 预装 VS Code 常用扩展，省去手动安装的时间
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension dbaeumer.vscode-eslint \
    && code-server --install-extension esbenp.prettier-vscode

WORKDIR /home/coder/project
