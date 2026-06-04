FROM codercom/code-server:4.91.1-debian

USER root

# 安装 Python3, Node.js, 编译工具以及 SSH 服务
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    git \
    build-essential \
    sudo \
    openssh-server \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 配置 SSH 服务，允许密码登录
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo "PermitUserEnvironment yes" >> /etc/ssh/sshd_config

# 安装 Playwright 的系统依赖库（用于无头浏览器运行）
RUN npm install -g playwright && npx playwright install-deps && npm uninstall -g playwright

# 允许 coder 用户免密执行 sudo 权限
RUN echo "coder ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/nopasswd

# 设置 Python 环境变量，允许在系统全局（容器内）使用 pip 安装包
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# 复制自定义入口脚本并赋予执行权限
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER coder

# 预装 VS Code 常用扩展
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension dbaeumer.vscode-eslint \
    && code-server --install-extension esbenp.prettier-vscode

WORKDIR /home/coder/project

ENTRYPOINT ["/entrypoint.sh"]
