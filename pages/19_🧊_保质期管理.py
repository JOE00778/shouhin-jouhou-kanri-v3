"""模块 #19 保质期管理 · Lark 同步 + 状态分级.

数据源: item_expiry（来自 Lark 多维表手动同步）+ warehouse_stock（库存联动）
状态: 期限切れ / 60日以内 / 余裕あり / 未登録
功能: Lark 手动同步 + 关键词搜索 + 多维过滤 + CSV 下载 + 行高亮
"""
from __future__ import annotations

import datetime
import re

import pandas as pd
import requests
import streamlit as st

from shared.db import get_connection
from shared.i18n import get_lang, lang_selector, t

st.set_page_config(page_title=t("保质期管理"), page_icon="🧊", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("🧊 保质期管理"))
st.caption(t("Lark 多维表手动同步 · 4 状态分级 · 库存联动"))

# ============================================================
# ⏸ 模块后置标识 (Boss 2026-05 决定)
# 原因: 1.0 Streamlit Cloud 后台无访问权限,LARK_APP_ID/SECRET 暂不可得
# 解锁条件: 拿到飞书 internal app 凭据后填入 v3 secrets.toml
# ============================================================
st.warning(t(
    "⏸ **本模块暂时后置** · Lark 同步需要飞书 internal app 凭据 "
    "(LARK_APP_ID / LARK_APP_SECRET),目前无法访问 1.0 Streamlit Cloud secrets。"
    "拿到凭据后填入 v3 secrets.toml 即可解锁。"
))
with st.expander(t("📤 备用方案: 直接上传 CSV (绕过 Lark 同步)"), expanded=False):
    st.caption(t(
        "Lark 表导出 CSV (列: jan / name / expiry_1~5) 上传到 item_expiry 表"
    ))
    csv_file = st.file_uploader(
        t("上传 item_expiry CSV"), type=["csv"], key="expiry_csv_upload"
    )
    if csv_file is not None:
        try:
            import pandas as _pd
            df_up = _pd.read_csv(csv_file, encoding="utf-8-sig")
            cur = conn.cursor()
            cur.execute("DELETE FROM item_expiry")
            now_iso = datetime.datetime.utcnow().isoformat()
            n_in = 0
            for _, row in df_up.iterrows():
                jan = _normalize_jan_cell(row.get("jan")) if "_normalize_jan_cell" in dir() else str(row.get("jan", "")).strip()
                if not jan:
                    continue
                cur.execute(
                    "INSERT INTO item_expiry(jan,name,expiry_1,expiry_2,expiry_3,expiry_4,expiry_5,expiry_min,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        jan,
                        str(row.get("name", "")) if row.get("name") is not None else None,
                        row.get("expiry_1") or None,
                        row.get("expiry_2") or None,
                        row.get("expiry_3") or None,
                        row.get("expiry_4") or None,
                        row.get("expiry_5") or None,
                        row.get("expiry_min") or row.get("expiry_1") or None,
                        now_iso,
                    ),
                )
                n_in += 1
            conn.commit()
            st.success(t(f"✅ CSV 上传成功: {n_in} 条"))
        except Exception as e:
            st.error(t(f"❌ CSV 解析失败: {e}"))

st.divider()

# 状态文本（沿用日文版关键字以兼容数据库）
ST_EXPIRED = t("期限切れ")
ST_WITHIN = t("60日以内")
ST_OK = t("余裕あり")
ST_NONE = t("未登録")
COL_DAYS = t("剩余天数")
COL_STATUS = t("状态")

# Lark 设置（缺失时只警告不 stop）
LARK_OK = True
try:
    LARK_APP_ID = st.secrets["LARK_APP_ID"]
    LARK_APP_SECRET = st.secrets["LARK_APP_SECRET"]
    LARK_SPREADSHEET_TOKEN = st.secrets.get("LARK_SPREADSHEET_TOKEN", "O6VQsoFDOhOPV7t3qSslkoSEg3b")
    LARK_SHEET_ID = st.secrets.get("LARK_SHEET_ID", "91fd41")
except Exception:
    LARK_OK = False


def _normalize_jan_cell(x):
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = re.sub(r"\D", "", s)
    return s if s else None


def _parse_date_cell(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        base = datetime.date(1899, 12, 30)
        return (base + datetime.timedelta(days=int(x))).isoformat()
    s = str(x).strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _min_date_iso(*isos):
    ds = [d for d in isos if d]
    return min(ds) if ds else None


def _lark_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal/"
    r = requests.post(url, json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"Lark token error: {j}")
    return j["tenant_access_token"]


def _lark_read(token, rng="A1:G5000"):
    url = (
        "https://open.larksuite.com/open-apis/"
        f"sheets/v2/spreadsheets/{LARK_SPREADSHEET_TOKEN}/values_batch_get"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                     params={"ranges": f"{LARK_SHEET_ID}!{rng}"}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(j)
    return j["data"]["valueRanges"][0]["values"]


def _sync_lark_to_db():
    token = _lark_token()
    values = _lark_read(token)
    if not values or len(values) < 2:
        return {"upserted": 0, "errors": []}

    upserts, errors = [], []
    for idx, row in enumerate(values[1:], start=2):
        try:
            a = row[0] if len(row) > 0 else None
            b = row[1] if len(row) > 1 else None
            jan = _normalize_jan_cell(a)
            if not jan:
                continue
            e1 = _parse_date_cell(row[2] if len(row) > 2 else None)
            e2 = _parse_date_cell(row[3] if len(row) > 3 else None)
            e3 = _parse_date_cell(row[4] if len(row) > 4 else None)
            e4 = _parse_date_cell(row[5] if len(row) > 5 else None)
            e5 = _parse_date_cell(row[6] if len(row) > 6 else None)
            upserts.append({
                "jan": jan,
                "name": str(b).strip() if b is not None else None,
                "expiry_1": e1, "expiry_2": e2, "expiry_3": e3, "expiry_4": e4, "expiry_5": e5,
                "expiry_min": _min_date_iso(e1, e2, e3, e4, e5),
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as ex:
            errors.append({"row": idx, "raw": row, "error": str(ex)})

    # 写入 SQLite (truncate + insert)
    cur = conn.cursor()
    cur.execute("DELETE FROM item_expiry")
    cur.executemany(
        "INSERT INTO item_expiry(jan,name,expiry_1,expiry_2,expiry_3,expiry_4,expiry_5,expiry_min,updated_at) "
        "VALUES(:jan,:name,:expiry_1,:expiry_2,:expiry_3,:expiry_4,:expiry_5,:expiry_min,:updated_at)",
        upserts,
    )
    conn.commit()
    return {"upserted": len(upserts), "errors": errors}


# UI: Lark 同步
st.subheader(t("🔄 从 Lark 同步（手动）"))
st.caption(t("同步约需 20 秒。完成后请刷新浏览器。"))
if LARK_OK:
    if st.button(t("🔄 立即同步"), key="expiry_sync"):
        with st.spinner(t("同步中...")):
            try:
                result = _sync_lark_to_db()
                st.success(t(f"✅ 同步完成: {result['upserted']} 条"))
                if result["errors"]:
                    st.warning(t(f"⚠️ 错误行: {len(result['errors'])} 条"))
                    df_err = pd.DataFrame(result["errors"]).copy()
                    if "raw" in df_err.columns:
                        df_err["raw"] = df_err["raw"].apply(
                            lambda x: " | ".join(map(str, x)) if isinstance(x, (list, tuple)) else str(x)
                        )
                    st.dataframe(df_err, use_container_width=True)
            except Exception as e:
                st.error(t(f"❌ 同步失败: {e}"))
else:
    st.error(t("❌ st.secrets 缺少 LARK_APP_ID / LARK_APP_SECRET"))

st.markdown("---")

# 数据展示
df = pd.DataFrame([dict(r) for r in conn.execute("SELECT * FROM item_expiry").fetchall()])
if df.empty:
    st.info(t("item_expiry 暂无数据。请先同步。"))
    st.stop()

df["jan"] = df["jan"].astype(str).str.strip()
jans = df["jan"].dropna().unique().tolist()

# 库存 join
df_stock = pd.DataFrame([
    dict(r) for r in conn.execute("SELECT jan, stock_available FROM warehouse_stock").fetchall()
])
if not df_stock.empty:
    df_stock["jan"] = df_stock["jan"].astype(str).str.strip()
    df_stock["stock_available"] = pd.to_numeric(df_stock["stock_available"], errors="coerce").fillna(0).astype(int)
    df_stock = df_stock.groupby("jan", as_index=False)["stock_available"].sum()
else:
    df_stock = pd.DataFrame(columns=["jan", "stock_available"])

df = df.merge(df_stock, on="jan", how="left")
df["stock_available"] = pd.to_numeric(df.get("stock_available"), errors="coerce").fillna(0).astype(int)
df["name"] = df.get("name", "").astype(str).fillna("").str.strip()

expiry_cols = ["expiry_1", "expiry_2", "expiry_3", "expiry_4", "expiry_5"]
for c in expiry_cols:
    if c in df.columns:
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.date

if set(expiry_cols).issubset(df.columns):
    df["expiry_min"] = pd.to_datetime(df[expiry_cols].stack(), errors="coerce").groupby(level=0).min().dt.date

df = df.where(pd.notnull(df), None)
df["expiry_min_dt"] = pd.to_datetime(df.get("expiry_min"), errors="coerce")
today = pd.Timestamp.today().normalize()
df[COL_DAYS] = ((df["expiry_min_dt"] - today).dt.days).astype("Int64")


def _status(days):
    if pd.isna(days):
        return ST_NONE
    if days < 0:
        return ST_EXPIRED
    if days <= 60:
        return ST_WITHIN
    return ST_OK


df[COL_STATUS] = df[COL_DAYS].apply(_status)

# 过滤
st.subheader(t("🔎 过滤"))
c1, c2, c3, c4 = st.columns([1.2, 1.0, 1.0, 0.8])
with c1:
    kw = st.text_input(t("搜索: JAN / 商品名"), value="", key="ex_kw")
with c2:
    statuses = [ST_EXPIRED, ST_WITHIN, ST_OK, ST_NONE]
    sel = st.multiselect(t("状态"), statuses, default=[ST_EXPIRED, ST_WITHIN], key="ex_st")
with c3:
    only_with = st.checkbox(t("仅显示已登记"), value=False, key="ex_with")
    only_no = st.checkbox(t("仅显示未登记"), value=False, key="ex_no")
    only_in = st.checkbox(t("仅有库存（隐藏库存 0）"), value=True, key="ex_in")
    only_zero = st.checkbox(t("仅库存 0"), value=False, key="ex_zero")
with c4:
    limit = st.number_input(t("显示条数上限"), min_value=50, max_value=5000, value=500, step=50, key="ex_lim")

view = df.copy()
if kw:
    cond = view["jan"].astype(str).str.contains(kw.strip(), na=False)
    if "name" in view.columns:
        cond = cond | view["name"].astype(str).str.contains(kw.strip(), na=False)
    view = view[cond]
if sel:
    view = view[view[COL_STATUS].isin(sel)]
if only_with and not only_no:
    view = view[view["expiry_min_dt"].notna()]
if only_no and not only_with:
    view = view[view["expiry_min_dt"].isna()]
if only_zero:
    view = view[view["stock_available"] <= 0]
elif only_in:
    view = view[view["stock_available"] > 0]

view = view.sort_values(by=["expiry_min_dt", "jan"], ascending=[True, True])

cols = ["jan", "name", "stock_available", "expiry_min", COL_DAYS, COL_STATUS] + expiry_cols
cols = [c for c in cols if c in view.columns]

st.subheader(f"{t('保质期 / 剩余天数')} · {len(view):,} {t('件')}")


def _highlight(row):
    if row[COL_STATUS] == ST_EXPIRED:
        return ["background-color: #ffcccc"] * len(row)
    if row[COL_STATUS] == ST_WITHIN:
        return ["background-color: #ffe599"] * len(row)
    return [""] * len(row)


st.dataframe(
    view.head(int(limit))[cols].style.apply(_highlight, axis=1),
    use_container_width=True,
)

csv = view[cols].to_csv(index=False).encode("utf-8-sig")
st.download_button(t("📥 CSV 下载"), data=csv, file_name="item_expiry_filtered.csv", mime="text/csv")
