"""CMS → N8N 触发器（通用 webhook 调用 + automation_runs 落库）。

用法：
    from shared.n8n_client import trigger_workflow
    run_id = trigger_workflow(
        module="shopee_mass_upload",
        webhook_path="shopee-mass-upload",
        payload={"market": "TW", "xlsx_urls": [...]},
        triggered_by=user_email,
    )
    # 后续 page 用 run_id 去 automation_runs 表查状态
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st


def _secret(name: str, default: str = "") -> str:
    """优先 streamlit secrets，fallback env var。与 shared.auth._secret 一致。"""
    try:
        v = st.secrets.get(name, None)
        if v:
            return str(v)
    except (FileNotFoundError, KeyError):
        pass
    return os.environ.get(name, "") or default


def _n8n_base_url() -> str:
    """N8N 公网地址（含 BasicAuth 用户密码也由此模块拿）。

    部署时设：
        N8N_BASE_URL=https://n8n.smikie-cms.cc
        N8N_BASIC_AUTH_USER=admin
        N8N_BASIC_AUTH_PASSWORD=...
    """
    return _secret("N8N_BASE_URL", "https://n8n.smikie-cms.cc").rstrip("/")


def _n8n_auth() -> tuple[str, str] | None:
    user = _secret("N8N_BASIC_AUTH_USER")
    pwd = _secret("N8N_BASIC_AUTH_PASSWORD")
    if user and pwd:
        return (user, pwd)
    return None


def trigger_workflow(
    *,
    module: str,
    webhook_path: str,
    payload: dict[str, Any],
    conn: Any,
    triggered_by: str = "system",
    timeout: int = 30,
) -> str:
    """触发 N8N workflow，返回新建的 run_id。

    流程：
        1. 生成 run_id（uuid4）
        2. INSERT automation_runs (run_id, module, payload, status='pending', ...)
        3. POST https://N8N_HOST/webhook/{webhook_path}（含 run_id）
        4. 把 N8N 立即返回的 accepted/rejected 写回 automation_runs.status

    Args:
        module: 业务模块名（'shopee_mass_upload' 等）
        webhook_path: N8N webhook 路径（不含前缀斜杠，如 'shopee-mass-upload'）
        payload: 业务负载（除 run_id 外的全部字段，会被 JSON 序列化）
        conn: CMS DB 连接（用于写 automation_runs）
        triggered_by: 触发用户标识（用户邮箱/账号）
        timeout: HTTP 请求超时秒数

    Returns:
        run_id（用于后续状态轮询）
    """
    run_id = str(uuid.uuid4())
    full_payload = {**payload, "run_id": run_id}

    # 1. 落 automation_runs.pending
    conn.execute(
        """
        INSERT INTO automation_runs
            (run_id, module, payload, status, triggered_by, triggered_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            module,
            json.dumps(payload, ensure_ascii=False, default=str),
            "pending",
            triggered_by,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()

    # 2. 调 N8N webhook
    url = f"{_n8n_base_url()}/webhook/{webhook_path.lstrip('/')}"
    auth = _n8n_auth()
    try:
        resp = requests.post(
            url,
            json=full_payload,
            auth=auth,
            timeout=timeout,
        )
        resp.raise_for_status()
        accepted = True
        msg = resp.text[:500]
    except requests.RequestException as e:
        accepted = False
        msg = f"N8N webhook 调用失败: {e}"

    # 3. 写回状态
    new_status = "processing" if accepted else "failed"
    conn.execute(
        """
        UPDATE automation_runs
        SET status = ?, summary = ?
        WHERE run_id = ?
        """,
        (new_status, json.dumps({"trigger_response": msg}, ensure_ascii=False), run_id),
    )
    conn.commit()

    if not accepted:
        raise RuntimeError(msg)

    return run_id


def get_run_status(conn: Any, run_id: str) -> dict[str, Any] | None:
    """查 automation_runs 一条记录。返回 None 表示不存在。"""
    row = conn.execute(
        "SELECT * FROM automation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    if out.get("payload"):
        try:
            out["payload"] = json.loads(out["payload"])
        except (TypeError, ValueError):
            pass
    if out.get("summary"):
        try:
            out["summary"] = json.loads(out["summary"])
        except (TypeError, ValueError):
            pass
    return out


def update_run(
    conn: Any,
    run_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
) -> None:
    """N8N 回调时（或本地手动）更新一条 automation_runs。"""
    completed_at = (
        datetime.now(timezone.utc).isoformat()
        if status in ("completed", "failed")
        else None
    )
    conn.execute(
        """
        UPDATE automation_runs
        SET status = ?, summary = ?, completed_at = COALESCE(?, completed_at)
        WHERE run_id = ?
        """,
        (
            status,
            json.dumps(summary or {}, ensure_ascii=False, default=str),
            completed_at,
            run_id,
        ),
    )
    conn.commit()


def list_recent_runs(conn: Any, *, module: str | None = None, limit: int = 50) -> list[dict]:
    """最近的 automation_runs，可按模块过滤。"""
    if module:
        rows = conn.execute(
            "SELECT * FROM automation_runs WHERE module = ? "
            "ORDER BY triggered_at DESC LIMIT ?",
            (module, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM automation_runs ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def poll_until_done(
    conn: Any,
    run_id: str,
    *,
    interval: float = 2.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """阻塞轮询直到 run 状态进入 completed/failed 或超时。

    在 Streamlit 里慎用（会卡住整个 page）。一般用 st.empty() + st.rerun() 异步轮询。
    """
    start = time.time()
    while time.time() - start < timeout:
        row = get_run_status(conn, run_id)
        if row and row.get("status") in ("completed", "failed"):
            return row
        time.sleep(interval)
    raise TimeoutError(f"automation_run {run_id} 超时（>{timeout}s）")
