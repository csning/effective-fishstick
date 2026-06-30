"""策略画像引擎 — 加载 YAML 画像，注入 Agent 参数，支持切换和自动匹配。

画像模板存放在 profiles/ 目录，YAML 格式：
    name: "Default Balanced"
    bias:
      stock_selection:
        pe_max: 60
        roe_min: 0.08
        prefer_sectors: []
        holding_period: "medium"
      timing:
        signal_sensitivity: "medium"
        require_volume_confirmation: true
      position:
        concentration: "medium"
        max_positions: 15
        rebalance_frequency: "weekly"
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger


@dataclass
class ProfileBias:
    """从 YAML 解析出的策略偏差参数。"""
    name: str
    description: str = ""

    # 选股偏差
    pe_max: float = 60
    roe_min: float = 0.08
    prefer_sectors: list = field(default_factory=list)
    prefer_momentum: bool = False
    holding_period: str = "medium"

    # 择时偏差
    signal_sensitivity: str = "medium"
    require_volume_confirmation: bool = True
    trailing_stop: bool = False
    stop_loss_pct_override: Optional[float] = None

    # 仓位偏差
    concentration: str = "medium"
    max_positions: int = 15
    rebalance_frequency: str = "weekly"

    # 风控偏差
    risk_weights_override: Optional[dict] = None


class ProfileEngine:
    """策略画像引擎。

    用法:
        engine = ProfileEngine()
        engine.load_all()
        engine.apply("momentum")

        bias = engine.current
        # 将 bias.pe_max 注入 StockSelector
        # 将 bias.signal_sensitivity 注入 TimingAgent
    """

    def __init__(self, profiles_dir: str = "profiles"):
        self._dir = Path(profiles_dir)
        self._profiles: dict[str, ProfileBias] = {}
        self._active: Optional[str] = None

    # ── 加载 ──

    def load_all(self) -> list[str]:
        """加载 profiles/ 目录下所有 .yaml 文件。"""
        if not self._dir.exists():
            logger.warning(f"画像目录不存在: {self._dir}")
            return []

        loaded = []
        for p in sorted(self._dir.glob("*.yaml")):
            try:
                bias = self._parse_file(p)
                key = p.stem
                self._profiles[key] = bias
                loaded.append(key)
                logger.info(f"加载画像: {key} ({bias.name})")
            except Exception as e:
                logger.error(f"画像解析失败 {p}: {e}")

        if not loaded:
            logger.warning("未加载到任何画像，使用内置默认")
            self._profiles["default"] = ProfileBias(name="Default")
            loaded = ["default"]

        if self._active is None and loaded:
            self._active = loaded[0]

        return loaded

    def apply(self, name: str) -> ProfileBias:
        """切换当前生效画像。"""
        if name not in self._profiles:
            available = list(self._profiles.keys())
            raise ValueError(f"画像 '{name}' 不存在。可用: {available}")
        self._active = name
        logger.info(f"切换策略画像: {name} ({self._profiles[name].name})")
        return self._profiles[name]

    @property
    def current(self) -> Optional[ProfileBias]:
        """当前生效的画像参数。"""
        if self._active is None:
            return None
        return self._profiles.get(self._active)

    @property
    def active_name(self) -> Optional[str]:
        return self._active

    def list_profiles(self) -> dict[str, str]:
        """列出所有可用画像: {key: display_name}。"""
        return {k: v.name for k, v in self._profiles.items()}

    def auto_match(self, holdings_sectors: list[str]) -> Optional[str]:
        """根据持仓行业分布自动匹配画像。

        规则：
        - 消费/金融/能源 > 50% → value
        - 科技/创业板 > 50% → momentum
        - 其他 → default
        """
        if not holdings_sectors:
            return None

        total = len(holdings_sectors)
        value_sectors = {"主板", "深主板"}
        momentum_sectors = {"创业板", "科创板", "中小板"}

        value_cnt = sum(1 for s in holdings_sectors if s in value_sectors)
        mom_cnt = sum(1 for s in holdings_sectors if s in momentum_sectors)

        if value_cnt / total > 0.5 and "buffett_value" in self._profiles:
            self.apply("buffett_value")
            return "buffett_value"
        elif mom_cnt / total > 0.5 and "momentum" in self._profiles:
            self.apply("momentum")
            return "momentum"
        elif "default" in self._profiles:
            self.apply("default")
            return "default"
        return None

    # ── 注入 Agent ──

    def inject_stock_selector(self, selector) -> None:
        """将画像偏差注入 StockSelector。"""
        bias = self.current
        if bias is None:
            return

        # StockSelector 的因子权重可以在运行时覆写（如果 Agent 支持）
        if hasattr(selector, "pe_max"):
            selector.pe_max = bias.pe_max
        if hasattr(selector, "roe_min"):
            selector.roe_min = bias.roe_min
        if hasattr(selector, "prefer_sectors"):
            selector.prefer_sectors = bias.prefer_sectors

        logger.debug(f"StockSelector 已注入画像: {self._active}")

    def inject_timing(self, timing_agent) -> None:
        """将画像偏差注入 TimingAgent。"""
        bias = self.current
        if bias is None:
            return

        sensitivity_map = {"low": 0.45, "medium": 0.0, "high": -0.15}

        if hasattr(timing_agent, "signal_offset"):
            timing_agent.signal_offset = sensitivity_map.get(
                bias.signal_sensitivity, 0.0
            )

        logger.debug(f"TimingAgent 已注入画像: {self._active}")

    def inject_risk_engine(self, risk_engine) -> None:
        """将画像偏差注入 RiskEngine（覆写权重）。"""
        bias = self.current
        if bias is None:
            return

        if bias.risk_weights_override:
            risk_engine.weights = bias.risk_weights_override

        logger.debug(f"RiskEngine 已注入画像: {self._active}")

    # ── 内部 ──

    def _parse_file(self, path: Path) -> ProfileBias:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        bias_raw = raw.get("bias", {})
        sel = bias_raw.get("stock_selection", {})
        tim = bias_raw.get("timing", {})
        pos = bias_raw.get("position", {})
        risk = bias_raw.get("risk", {})

        return ProfileBias(
            name=raw.get("name", path.stem),
            description=raw.get("description", ""),
            pe_max=float(sel.get("pe_max", 60)),
            roe_min=float(sel.get("roe_min", 0.08)),
            prefer_sectors=sel.get("prefer_sectors", []),
            prefer_momentum=bool(sel.get("prefer_momentum", False)),
            holding_period=str(sel.get("holding_period", "medium")),
            signal_sensitivity=str(tim.get("signal_sensitivity", "medium")),
            require_volume_confirmation=bool(tim.get("require_volume_confirmation", True)),
            trailing_stop=bool(tim.get("trailing_stop", False)),
            stop_loss_pct_override=(
                float(tim["stop_loss_pct_override"])
                if tim.get("stop_loss_pct_override") is not None
                else None
            ),
            concentration=str(pos.get("concentration", "medium")),
            max_positions=int(pos.get("max_positions", 15)),
            rebalance_frequency=str(pos.get("rebalance_frequency", "weekly")),
            risk_weights_override=risk.get("weights") if risk else None,
        )
