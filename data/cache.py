"""基于 Parquet 的数据缓存层，支持 TTL 过期与自动清理。"""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


class DataCache:
    """将 DataFrame 缓存为 Parquet 文件，支持 TTL 自动过期。"""

    def __init__(self, cache_dir: str = "data_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._meta: dict = self._load_meta()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get(self, key: str, ttl_seconds: int = 3600) -> Optional[pd.DataFrame]:
        """如果缓存未过期则返回 DataFrame，否则返回 None。"""
        meta = self._meta.get(key)
        if meta is None:
            return None
        age = time.time() - meta["ts"]
        if age > ttl_seconds:
            logger.debug("缓存过期: {} (age={:.0f}s, ttl={}s)", key, age, ttl_seconds)
            return None
        parquet_path = self.cache_dir / (key + ".parquet")
        if not parquet_path.exists():
            return None
        df = pd.read_parquet(parquet_path)
        logger.debug("缓存命中: {} (rows={}, age={:.0f}s)", key, len(df), age)
        return df

    def put(self, key: str, df: pd.DataFrame) -> None:
        """存储 DataFrame 并更新元数据。"""
        parquet_path = self.cache_dir / (key + ".parquet")
        df.to_parquet(parquet_path, index=False)
        self._meta[key] = {"ts": time.time(), "rows": len(df)}
        self._save_meta()
        logger.debug("缓存写入: {} (rows={})", key, len(df))

    def invalidate(self, key: str) -> None:
        """删除指定缓存条目。"""
        self._meta.pop(key, None)
        (self.cache_dir / (key + ".parquet")).unlink(missing_ok=True)
        self._save_meta()

    @staticmethod
    def make_key(prefix: str, **params) -> str:
        """根据前缀和参数生成确定性的缓存键。"""
        raw = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.md5(raw.encode()).hexdigest()[:12]
        return prefix + "_" + digest

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _load_meta(self) -> dict:
        meta_path = self.cache_dir / "_meta.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text())
        return {}

    def _save_meta(self) -> None:
        (self.cache_dir / "_meta.json").write_text(json.dumps(self._meta))
