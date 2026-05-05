"""Supabase REST API 客户端封装。

所有页面通过这个模块访问 Supabase，避免在 page 文件里到处散布 requests 调用。
若 secrets 未配，提供 None 兜底，便于离线开发模块 #1（成本同步）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import requests
import streamlit as st


class SupabaseClient:
    """轻量 REST 封装，无 ORM。"""

    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def fetch_table(self, table: str, *, batch: int = 1000) -> pd.DataFrame:
        """全量分页获取一张表，返回 DataFrame。"""
        dfs = []
        offset = 0
        headers = {**self.headers, "Prefer": "count=exact"}
        while True:
            url = f"{self.url}/rest/v1/{table}?select=*&offset={offset}&limit={batch}"
            res = requests.get(url, headers=headers, timeout=30)
            if res.status_code == 416:
                break
            if res.status_code not in (200, 206):
                raise RuntimeError(
                    f"fetch_table({table}) failed: {res.status_code} / {res.text}"
                )
            rows = res.json()
            if not rows:
                break
            dfs.append(pd.DataFrame(rows))
            if len(rows) < batch:
                break
            offset += batch
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        """轻量 SELECT，返回原始 list[dict]。"""
        params = params or {}
        if "select" not in params:
            params["select"] = "*"
        res = requests.get(
            f"{self.url}/rest/v1/{table}",
            headers=self.headers,
            params=params,
            timeout=30,
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"select({table}) failed: {res.status_code} / {res.text}"
            )
        return res.json()

    def upsert(self, table: str, rows: list[dict]) -> int:
        """批量 upsert（resolution=merge-duplicates）。返回写入条数。"""
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), 500):
            batch = rows[i : i + 500]
            headers = {
                **self.headers,
                "Prefer": "resolution=merge-duplicates,return=representation",
            }
            res = requests.post(
                f"{self.url}/rest/v1/{table}",
                headers=headers,
                json=batch,
                timeout=30,
            )
            if res.status_code not in (200, 201):
                raise RuntimeError(
                    f"upsert({table}) failed: {res.status_code} / {res.text}"
                )
            data = res.json()
            total += len(data) if isinstance(data, list) else len(batch)
        return total


@st.cache_resource
def get_client() -> SupabaseClient | None:
    """从 st.secrets 读 SUPABASE_URL/KEY 并构建 client。

    未配 secrets 时返回 None；调用方应判断 client is None 走离线路径。
    """
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except (KeyError, FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return None
    if not url or not key or url.startswith("https://YOUR_PROJECT"):
        return None
    return SupabaseClient(url, key)


def is_configured() -> bool:
    return get_client() is not None
