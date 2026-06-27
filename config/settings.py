import os
from pathlib import Path
from typing import Optional, Literal

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-v4-flash"
    reasoner_model: str = "deepseek-v4-pro"
    max_tokens: int = 4096
    temperature: float = 0.3


class DataConfig(BaseModel):
    tushare_token: str = ""
    cache_dir: str = "data_cache"
    history_days: int = 1095
    us_indices: list = [".DJI", ".IXIC", ".SPX"]
    request_interval: float = 0.5


class RiskConfig(BaseModel):
    default_level: int = 3
    auto_adjust: bool = True
    position_caps: dict = {1: 0.30, 2: 0.50, 3: 0.70, 4: 0.90, 5: 1.00}
    stop_loss_pcts: dict = {1: 0.03, 2: 0.05, 3: 0.08, 4: 0.12, 5: 0.15}


class NotifyConfig(BaseModel):
    feishu_webhook: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    channel: Literal["feishu", "wechat", "both"] = "feishu"
    feishu_base_url: str = "https://open.feishu.cn"


class VPSConfig(BaseModel):
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    mac_mini_addr: str = ""


class TradeImportConfig(BaseModel):
    mode: Literal["fragment", "csv"] = "fragment"
    watch_dir: str = "trade_imports"



def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 的值覆盖 base。"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class Settings(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    vps: VPSConfig = Field(default_factory=VPSConfig)
    trade_import: TradeImportConfig = Field(default_factory=TradeImportConfig)

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "Settings":
        if path is None:
            path = Path(__file__).resolve().parent / "settings.yaml"
        data: dict = {}
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            _deep_merge(data, raw)
        local_path = path.parent / "settings.local.yaml"
        if local_path.exists():
            with open(local_path) as f:
                _deep_merge(data, yaml.safe_load(f) or {})
        env_map = {
            "DEEPSEEK_API_KEY": ("llm", "api_key"),
            "FEISHU_WEBHOOK": ("notify", "feishu_webhook"),
            "FEISHU_APP_ID": ("notify", "feishu_app_id"),
            "FEISHU_APP_SECRET": ("notify", "feishu_app_secret"),
            "REDIS_HOST": ("vps", "redis_host"),
        }
        for env_var, (section, key) in env_map.items():
            if val := os.environ.get(env_var):
                data.setdefault(section, {})[key] = val
        return cls.model_validate(data)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml()
    return _settings
