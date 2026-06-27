# VPS 部署指南：飞书 Bot 上线

## 前置条件

- 阿里云 VPS 一台（2C4G 足够），已安装 Ubuntu 22.04 / Debian 12 / CentOS 8+
- 能 SSH 登录（root 或 sudo 用户）
- 项目已推送到 GitHub（或能用 scp 传上去）

## 步骤

### 1. 传到 VPS

```bash
# 方式 A：从 GitHub 拉（推荐）
ssh root@<你的VPS公网IP>
cd /opt
git clone https://github.com/你的用户名/effective-fishstick.git

# 方式 B：从本机 scp
cd /Volumes/WG512/codex/
scp -r effective-fishstick root@<你的VPS公网IP>:/opt/
```

### 2. 运行部署脚本

脚本自动完成：安装 Python 3.12、创建虚拟环境、装依赖、配 systemd、开防火墙。

```bash
ssh root@<你的VPS公网IP>
cd /opt/effective-fishstick
bash scripts/setup_vps.sh
```

脚本执行过程中会让你确认启动，先选 n（还没配凭证）。

### 3. 填写凭证

```bash
vim /opt/effective-fishstick/config/settings.local.yaml
```

填入你的实际值：

```yaml
data:
  tushare_token: "4ddede23582a5d..."
llm:
  api_key: "sk-68a8587df1..."
  chat_model: deepseek-v4-flash
  reasoner_model: deepseek-v4-pro
notify:
  feishu_app_id: "你的-feishu-app-id"
  feishu_app_secret: "你的-feishu-app-secret"
```

### 4. 阿里云安全组放行端口

这是最容易忘的一步。VPS 上的 iptables/ufw 开了不够，阿里云外层还有安全组。

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)
2. 找到你的 ECS 实例 → **安全组** → **配置规则**
3. **入方向** → **手动添加**：
   - 协议：TCP
   - 端口：`8000`
   - 授权对象：`0.0.0.0/0`
4. 保存

### 5. 启动服务

```bash
systemctl start effective-fishstick
systemctl status effective-fishstick    # 确认 active (running)
journalctl -u effective-fishstick -f    # 看实时日志
```

验证服务可达：

```bash
curl http://localhost:8000/health
# 应返回 {"status":"ok","service":"effective-fishstick"}
```

### 6. 配置飞书事件订阅

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 进入你的应用 → **事件与回调** → **事件订阅**
3. **请求网址** 填入：`http://<VPS公网IP>:8000/feishu/webhook`
4. 点保存，飞书会发送 challenge 验证
5. 验证通过后，添加事件订阅，勾选：
   - **接收消息** → `im.message.receive_v1`
6. 保存并发布版本

### 7. 测试

在飞书里找到你的机器人，发一条「帮助」，应该收到指令菜单卡片。

## 日常运维

```bash
systemctl start effective-fishstick     # 启动
systemctl stop effective-fishstick      # 停止
systemctl restart effective-fishstick   # 重启
systemctl status effective-fishstick    # 状态
journalctl -u effective-fishstick -f    # 实时日志
journalctl -u effective-fishstick -n 50 # 最近 50 行日志
```

更新代码后重启：

```bash
cd /opt/effective-fishstick
git pull
source .venv/bin/activate
pip install -e ".[dev,feishu]"
systemctl restart effective-fishstick
```
