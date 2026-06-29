# VPS 部署指南：飞书 Bot 从零到上线

## 前置条件

- VPS 一台（2C4G 足够），Ubuntu 22.04+ / Debian 12+
- 能 SSH 登录（root 或 sudo 用户）
- 项目已推送到 GitHub
- 飞书开放平台已创建企业自建应用

---

## 第一步：传到 VPS

```bash
ssh root@你的VPS公网IP
cd /opt
git clone https://github.com/csning/effective-fishstick.git
cd effective-fishstick
```

## 第二步：运行一键部署

```bash
bash scripts/setup_vps.sh
```

脚本自动完成：安装 Python 3.12、创建虚拟环境、装依赖、配 systemd 服务、开防火墙。

脚本结束时先选 `n` 不启动，因为还没配凭证。脚本会自动运行 `check_feishu.py` 诊断，此时 app_id/app_secret 会显示 `MISSING`，正常。

## 第三步：填写凭证

```bash
vim config/settings.local.yaml
```

最小配置：

```yaml
data:
  tushare_token: "4ddede23..."
llm:
  api_key: "sk-68a858..."
  chat_model: deepseek-v4-flash
  reasoner_model: deepseek-v4-pro
notify:
  feishu_app_id: "cli_aab5..."
  feishu_app_secret: "JbqVUM7..."
  feishu_webhook: ""
```

## 第四步：防火墙

**这是最容易漏的一步。VPS 本机防火墙开了不够，云服务商外面还有一层安全组。**

### 4a. VPS 本机

部署脚本已自动处理（ufw / firewalld）。确认：

```bash
# Ubuntu/Debian
sudo ufw status | grep 8000

# CentOS
sudo firewall-cmd --list-ports
```

### 4b. 云服务商安全组（阿里云 / 腾讯云 / 华为云）

登录云控制台 → ECS/云服务器 → 安全组 → 入方向 → 添加规则：

| 协议 | 端口 | 授权对象 |
|------|------|----------|
| TCP  | 8000 | 0.0.0.0/0 |

验证外网可达（从你自己电脑跑）：

```bash
curl http://VPS公网IP:8000/health
# 服务启动后应返回 {"status":"ok",...}
```

## 第五步：启动服务

```bash
systemctl start effective-fishstick
systemctl status effective-fishstick
```

确认启动日志里有：

```
Config: app_id=OK app_secret=OK webhook=unset
Feishu API: connected OK
```

如果显示 `MISSING`，说明第三步的凭证没填对。如果 `Feishu API: connect failed`，说明 VPS 无法访问 `open.feishu.cn`，检查 DNS。

## 第六步：配置飞书开放平台

打开 [飞书开放平台](https://open.feishu.cn/app) → 你的应用。

### 6a. 事件订阅

**左侧菜单 → 事件与回调 → 事件订阅**

1. 请求网址填入：`http://你的VPS公网IP:8000/feishu/webhook`
2. 点击保存，飞书会发送 challenge 验证
3. 验证通过后，点击 **添加事件**，搜索并勾选：
   - **`im.message.receive_v1`** — 接收消息

**⚠️ 常见踩坑：只验证了 URL 但没有添加事件订阅。URL 验证通过不代表消息能到达！**

### 6b. 权限管理

**左侧菜单 → 权限管理**，确保已开启：

| 权限 | 说明 |
|------|------|
| `im:message` | 获取与发送消息 |
| `im:message:read_as_bot` | 以机器人身份读取消息 |
| `im:message:send_as_bot` | 以机器人身份发送消息 |

### 6c. 发布应用

**左侧菜单 → 应用发布 → 创建版本 → 发布**

如果无法正式发布，可以在 **安全设置** 中将自己的飞书账号添加为测试人员，创建测试版本。

### 6d. 将 Bot 加入会话

在飞书客户端里搜索你的应用名称，进入与 Bot 的单聊，或者将 Bot 添加到目标群聊。

**⚠️ Bot 不在会话里的话，发消息是到不了 VPS 的。**

## 第七步：测试

在飞书给 Bot 发送「帮助」，应该收到指令菜单卡片。

如果没反应，在 VPS 上查看实时诊断：

```bash
# 查看最近事件
curl -s http://127.0.0.1:8000/feishu/health | python3 -m json.tool

# 查看服务日志
journalctl -u effective-fishstick -f
```

---

## 快速迁移到新 VPS

如果要换 VPS 服务商，只需：

```bash
# 新 VPS 上
cd /opt
git clone https://github.com/csning/effective-fishstick.git
cd effective-fishstick

# 复制旧 VPS 的 settings.local.yaml 过来
vim config/settings.local.yaml   # 粘贴凭证

# 部署
bash scripts/setup_vps.sh
systemctl start effective-fishstick

# 更新飞书开放平台的事件订阅 URL 为新 VPS 的 IP
# 更新云服务商安全组放行新 VPS 的 8000 端口
```

一次命令更新代码：

```bash
bash scripts/update.sh
```

---

## 常见故障排查

| 现象 | 根因 | 解决 |
|------|------|------|
| URL 验证通过，发消息无反应 | 事件订阅只验证了 URL，**没有添加 `im.message.receive_v1` 事件** | 飞书开放平台 → 事件订阅 → 添加事件 |
| URL 验证通过，事件已订阅，仍无反应 | 飞书用的是 v2 事件格式（`schema: 2.0`），旧版本代码只处理 v1 | 更新代码到最新版（已修复） |
| Bot 回复失败了 | `sender_id` 在 v2 中是对象 `{"open_id":"..."}`，旧代码当字符串处理 | 更新代码到最新版（已修复） |
| `curl` 本地通，外网不通 | 云服务商安全组没放行 8000 端口 | 控制台 → 安全组 → 入方向添加 TCP 8000 |
| 启动日志 `Feishu API: connect failed` | VPS 无法访问外网 / DNS 问题 | `curl https://open.feishu.cn` 测试连通性 |
| `recent_events: []` 始终为空 | 事件根本没到达 VPS | 检查事件订阅 URL 和网络安全组 |
| 诊断脚本 `app_id=MISSING` | 凭证没填 | `vim config/settings.local.yaml` |

---

## 日常运维

```bash
systemctl status effective-fishstick    # 查看状态
journalctl -u effective-fishstick -f    # 实时日志
journalctl -u effective-fishstick -n 50 # 最近 50 行
systemctl restart effective-fishstick   # 重启

bash scripts/update.sh                  # 更新代码 + 重启 + 诊断
curl -s http://127.0.0.1:8000/feishu/health | python3 -m json.tool  # 诊断
```

---

## 踩坑记录（技术细节）

### 坑 1：飞书 v2 事件格式不兼容

飞书新版应用默认使用 v2 事件格式。v2 事件体结构为：

```json
{
  "schema": "2.0",
  "header": {"event_type": "im.message.receive_v1", ...},
  "event": {...}
}
```

而不是旧版的 `{"type": "event_callback", "event": {"type": "im.message.receive_v1", ...}}`。

旧代码只检查 `data.type == "event_callback"`，v2 事件被直接跳过，且没有任何日志警告。
修复：在 `web/app.py` 中同时支持 `schema: "2.0"` 和 `type: "event_callback"` 两种格式。

### 坑 2：sender_id 是对象不是字符串

飞书 v2 事件中 `sender.sender_id` 是一个嵌套对象：

```json
"sender": {"sender_id": {"open_id": "ou_xxx", "union_id": "on_xxx", "user_id": "xxx"}}
```

旧代码 `sender.get("sender_id", "")` 拿到的不是字符串，导致 `open_id` 和 `receive_id` 错误，所有回复 API 调用失败。
修复：新增 `_extract_open_id()` 函数，兼容对象和字符串两种格式。

### 坑 3：URL 验证 ≠ 事件订阅

飞书开放平台的事件订阅页面，填好请求网址后点保存，飞书会发 challenge 验证。验证通过只说明 URL 可达，**不代表消息事件会自动推送**。

必须在同一个页面里点击 **「添加事件」**，搜索并勾选 `im.message.receive_v1`，否则飞书不会推送任何消息事件。这是最容易被忽略的一步。

### 坑 4：双防火墙

云服务器有两层防火墙：VPS 本机的 iptables/ufw 和云服务商的安全组。两层都要放行 8000 端口。部署脚本只处理了本机那层。

### 坑 5：Bot 必须加入会话

飞书 Bot 不会自动接收到发给「所有人」的消息。必须将 Bot 添加为目标群聊的成员，或者用户主动打开与 Bot 的单聊会话。在这之前发的消息不会产生任何事件。
