"""飞书电子表格追加：支持两种后端
- host 模式: 调用 lark-cli（默认；用户态凭据）
- container 模式: 直接调用 Lark OpenAPI（需环境变量 LARK_APP_ID / LARK_APP_SECRET）
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

from config import LARK_SHEET_TOKEN, LARK_SHEET_ID, LARK_SHEET_URL

LARK_APP_ID = os.environ.get("LARK_APP_ID", "")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET", "")


# ---------- 后端 1: 直接 HTTP OpenAPI（container 模式）----------

_token_cache = {"token": "", "expire_at": 0}


def _tenant_token() -> str:
    """缓存 tenant_access_token（有效期 2h）"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 300:
        return _token_cache["token"]
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token 获取失败: {data}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expire_at"] = now + data.get("expire", 7200)
    return _token_cache["token"]


def _append_via_api(rows: list[list]) -> bool:
    token = _tenant_token()
    url = (
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/"
        f"{LARK_SHEET_TOKEN}/values_append"
    )
    body = {
        "valueRange": {
            "range": f"{LARK_SHEET_ID}!A:I",
            "values": rows,
        }
    }
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[sheet-api] HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
        return False
    if data.get("code") != 0:
        print(f"[sheet-api] API err: {data}")
        return False
    print(f"[sheet-api] 追加 {len(rows)} 行")
    return True


# ---------- 后端 2: lark-cli（host 模式）----------

def _append_via_cli(rows: list[list]) -> bool:
    cmd = [
        "lark-cli", "sheets", "+append",
        "--spreadsheet-token", LARK_SHEET_TOKEN,
        "--sheet-id", LARK_SHEET_ID,
        "--values", json.dumps(rows, ensure_ascii=False),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            print(f"[sheet-cli] 追加失败: {res.stderr[:500]}")
            return False
        print(f"[sheet-cli] 追加 {len(rows)} 行")
        return True
    except FileNotFoundError:
        print("[sheet-cli] lark-cli 未安装")
        return False
    except Exception as e:
        print(f"[sheet-cli] 异常: {e}")
        return False


# ---------- 对外接口 ----------

def _append_rows(rows: list[list]) -> bool:
    if not rows:
        return True
    if LARK_APP_ID and LARK_APP_SECRET:
        return _append_via_api(rows)
    return _append_via_cli(rows)


def _detail_url(product_id) -> str:
    if not product_id:
        return ""
    return f"https://netdeoroshi.com/product.php?id={product_id}"


def send_summary(diff: dict, report_path: str):
    today = datetime.now().strftime("%Y-%m-%d")
    rows: list[list] = []

    for e in diff.get("newly_discontinued", []):
        rows.append([today, e["jan"], e["maker"], e["name"], "停産", "新規停産",
                     e.get("matched_name") or "", e.get("matched_id") or "",
                     _detail_url(e.get("matched_id"))])
    for e in diff.get("disappeared", []):
        rows.append([today, e["jan"], e["maker"], e["name"], "消失", "サイトから消失",
                     "", "", ""])
    for e in diff.get("newly_found", []):
        rows.append([today, e["jan"], e["maker"], e["name"], "在庫", "新規上架",
                     e.get("matched_name") or "", e.get("matched_id") or "",
                     _detail_url(e.get("matched_id"))])

    if not rows:
        print("[sheet] 无变化，不追加")
        return

    ok = _append_rows(rows)
    if ok:
        print(f"[sheet] 完了: {LARK_SHEET_URL}")
