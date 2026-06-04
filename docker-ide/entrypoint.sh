#!/bin/bash

# 动态将 docker-compose 中的 PASSWORD 环境变量设置为 Linux 系统中 coder 用户的密码
# 这样不仅网页版可以使用该密码，SSH 登录也可以使用相同的密码
if [ -n "$PASSWORD" ]; then
    echo "coder:$PASSWORD" | sudo chpasswd
fi

# 创建 SSH 运行所需目录并启动 SSH 服务
sudo mkdir -p /var/run/sshd
sudo service ssh start

# 执行 code-server 官方默认的入口程序，保证网页 IDE 正常运行
exec /usr/bin/entrypoint.sh "$@"
