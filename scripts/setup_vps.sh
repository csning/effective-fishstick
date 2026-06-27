#!/bin/bash
# Effective Fishstick — VPS 一键部署脚本
# 支持 Ubuntu 22.04+, Debian 12+, CentOS 8+
# 用法：在 VPS 上以 root 执行：bash setup_vps.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${RED}[-]${NC} $1"; }

# ── 0. 系统检测 ──────────────────────────────────────────

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VER=$VERSION_ID
else
    warn "无法检测系统版本"
    exit 1
fi

log "检测到: $OS $VER"

# ── 1. 安装系统依赖 ──────────────────────────────────────

install_python() {
    log "安装 Python 3.12+ ..."
    case $OS in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq software-properties-common
            add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
            apt-get update -qq
            apt-get install -y -qq python3.12 python3.12-venv python3.12-dev git curl
            ;;
        centos|rhel|rocky|almalinux)
            dnf install -y epel-release 2>/dev/null || yum install -y epel-release
            dnf install -y python3.12 python3.12-devel git curl 2>/dev/null || {
                warn "CentOS 7 需要手动安装 Python 3.12，请参考 https://github.com/deadsnakes"
                exit 1
            }
            ;;
        *)
            warn "不支持的系统: $OS"
            exit 1
            ;;
    esac
}

if ! command -v python3.12 &>/dev/null; then
    install_python
else
    log "Python 3.12 已安装: $(python3.12 --version)"
fi

# ── 2. 克隆项目 ──────────────────────────────────────────

PROJECT_DIR="/opt/effective-fishstick"
REPO_URL="${1:-}"

if [ -d "$PROJECT_DIR/.git" ]; then
    log "项目已存在，执行 git pull ..."
    cd "$PROJECT_DIR"
    git pull
elif [ -n "$REPO_URL" ]; then
    log "克隆项目: $REPO_URL"
    git clone "$REPO_URL" "$PROJECT_DIR"
else
    warn "未提供 git 仓库地址，请手动将代码放到 $PROJECT_DIR"
    warn "然后重新运行此脚本"
    warn "用法: bash setup_vps.sh git@github.com:user/repo.git"
fi

# ── 3. 创建虚拟环境 ──────────────────────────────────────

if [ -d "$PROJECT_DIR" ]; then
    cd "$PROJECT_DIR"
    log "创建 Python 虚拟环境..."
    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip -q
    pip install -e ".[dev,feishu]" -q
    log "依赖安装完成"
fi

# ── 4. 配置文件 ──────────────────────────────────────────

if [ -f "$PROJECT_DIR/config/settings.local.yaml" ]; then
    log "settings.local.yaml 已存在，跳过创建"
else
    log "创建 settings.local.yaml 模板..."
    cat > "$PROJECT_DIR/config/settings.local.yaml" << 'YEOF'
data:
  tushare_token: "your-tushare-token"
llm:
  api_key: "your-deepseek-api-key"
  chat_model: deepseek-v4-flash
  reasoner_model: deepseek-v4-pro
notify:
  feishu_app_id: "your-feishu-app-id"
  feishu_app_secret: "your-feishu-app-secret"
  feishu_webhook: ""
YEOF
    warn "请编辑 $PROJECT_DIR/config/settings.local.yaml 填入真实凭证"
fi

# ── 5. systemd 服务 ──────────────────────────────────────

SERVICE_FILE="/etc/systemd/system/effective-fishstick.service"
cp "$PROJECT_DIR/scripts/effective-fishstick.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable effective-fishstick
log "systemd 服务已注册"

# ── 6. 防火墙 ────────────────────────────────────────────

log "配置防火墙（开放 8000 端口）..."
case $OS in
    ubuntu|debian)
        ufw allow 8000/tcp 2>/dev/null && log "ufw: 8000/tcp 已开放" || true
        ;;
    centos|rhel|rocky|almalinux)
        firewall-cmd --permanent --add-port=8000/tcp 2>/dev/null && firewall-cmd --reload 2>/dev/null && log "firewalld: 8000/tcp 已开放" || true
        ;;
esac
log "⚠️  阿里云安全组还需在控制台放行 8000 端口（见下方说明）"

# ── 7. 启动服务 ──────────────────────────────────────────

read -p "是否现在启动服务？[Y/n] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [ -z "$REPLY" ]; then
    systemctl start effective-fishstick
    sleep 2
    systemctl status effective-fishstick --no-pager
fi

echo ""
echo "============================================"
log "部署完成！"
echo ""
echo "常用命令："
echo "  启动:    systemctl start effective-fishstick"
echo "  停止:    systemctl stop effective-fishstick"
echo "  状态:    systemctl status effective-fishstick"
echo "  日志:    journalctl -u effective-fishstick -f"
echo "  重启:    systemctl restart effective-fishstick"
echo ""
echo "飞书配置："
echo "  事件订阅 URL:  http://<VPS公网IP>:8000/feishu/webhook"
echo ""
echo "⚠️  别忘了在阿里云安全组中放行 TCP 8000 端口！"
echo "============================================"
