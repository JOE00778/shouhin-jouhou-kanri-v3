"""模块 #21 Shopee 上架（整合版 v2）

四个 Tab 把 Shopee 上架全套功能集中在一个 page：

    🤖 全自动管线   →  输 SPU CSV → 一键调 N8N → 自动出图 + 自动上架 + 飞书通知
    📤 手工模式     →  仅出 mass-upload XLSX（兼容 T-309 旧用法，本机下载）
    🎨 参考图库     →  按品类一次性上传参考详情图（喂给 Nano Banana 2 复刻风格）
    📜 历史运行     →  automation_runs 实时进度 + 历史

原 page 22「Shopee 自动上架」已合并进本页 Tab 1 + Tab 4。
原计划 page 23「参考图库」已合并进本页 Tab 3。

依赖：
- shared/n8n_client.py · 触发 + 状态查询
- shared/lark_notify.py · 兜底飞书
- shopee-listing/scripts/* · 手工 pipeline
- automation_runs 表
- data/files/reference-images/<category>/*.png  · 参考图存储
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import streamlit as st

from shared.auth import is_admin, require_admin
from shared.db import get_connection, INPUTS_DIR, DATA_DIR
from shared.i18n import get_lang, lang_selector, t
from shared.n8n_client import (
    get_run_status,
    list_recent_runs,
    trigger_workflow,
)


# --------------------------------------------------------------------------- #
# sys.path bootstrap — shopee-listing pipeline
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EMBEDDED = _REPO_ROOT / "shopee_listing"
_SIBLING = Path(__file__).resolve().parents[2] / "shopee-listing"

SHOPEE_LISTING_ROOT = _EMBEDDED if _EMBEDDED.is_dir() else _SIBLING

for _p in (SHOPEE_LISTING_ROOT, SHOPEE_LISTING_ROOT / "scripts"):
    p_str = str(_p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)


# --------------------------------------------------------------------------- #
# 参考图库 · 文件系统位置
# --------------------------------------------------------------------------- #
REFERENCE_DIR = DATA_DIR / "files" / "reference-images"
REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Page setup
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Shopee 上架", page_icon="🚀", layout="wide")
require_admin()
lang_selector()

with st.sidebar:
    st.divider()
    en_on = st.checkbox("🌐 English (this page)", value=False, key="t309_en")
    if en_on:
        st.session_state["lang"] = "en"

st.title("🚀 Shopee 上架")
st.caption("全自动管线 / 手工模式 / 参考图库 / 历史运行 一站式")

conn = get_connection()


# --------------------------------------------------------------------------- #
# 三语 i18n 兜底
# --------------------------------------------------------------------------- #
_PAGE_STRINGS_EN: Dict[str, str] = {
    "Shopee 上架": "Shopee Listing",
    "全自动管线 / 手工模式 / 参考图库 / 历史运行 一站式":
        "Auto pipeline / Manual / Reference library / History — all in one",
}


def tt(text: str) -> str:
    lang = get_lang()
    if lang == "en":
        return _PAGE_STRINGS_EN.get(text, t(text))
    return t(text)


# --------------------------------------------------------------------------- #
# 公共 helpers
# --------------------------------------------------------------------------- #
def _read_uploaded_csv(uploaded) -> pd.DataFrame:
    raw = uploaded.read()
    try:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="cp932", keep_default_na=False)


def _validate_and_dedup(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if df.shape[1] < 2:
        raise ValueError("❌ CSV 至少需要 A 列 (SPU) + B 列 (SKU)")
    df = df.copy()
    cols = list(df.columns)
    df = df.rename(columns={cols[0]: "SPU", cols[1]: "SKU"})
    df["SPU"] = df["SPU"].astype(str).str.strip()
    df["SKU"] = df["SKU"].astype(str).str.strip()
    df = df[(df["SPU"] != "") & (df["SKU"] != "")].reset_index(drop=True)
    dup_mask = df["SKU"].duplicated(keep="first")
    if dup_mask.any():
        dup_skus = df.loc[dup_mask, "SKU"].tolist()
        warnings.append(
            f"⚠️ SKU 重复，已自动去重（保留首次出现）：{', '.join(dup_skus[:10])}"
            + ("..." if len(dup_skus) > 10 else "")
        )
        df = df[~dup_mask].reset_index(drop=True)
    return df, warnings


def _df_to_clean_csv_path(df: pd.DataFrame) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    )
    df[["SPU", "SKU"]].to_csv(tmp.name, index=False)
    tmp.close()
    return Path(tmp.name)


# --------------------------------------------------------------------------- #
# 手工 pipeline runner（从原 T-309 page 21 完整搬过来）
# --------------------------------------------------------------------------- #
def _make_mock_listing(spu, ListingDraftCls):
    return ListingDraftCls(
        title=f"<MOCK> {spu.spu_key} — Direct from Japan",
        description=(
            f"<MOCK description for {spu.spu_key}>\n"
            "[NOTE] Placeholder generated by Streamlit page (Mock mode)."
        ),
        key_features=["<mock feature 1>", "<mock feature 2>"],
        how_to_use=["<mock step 1>"],
        ingredients=None,
        spec_json={},
        brand_normalized="",
        hook="Direct from Japan",
        model="mock",
        spu_key=spu.spu_key,
    )


def run_pipeline(
    csv_path: Path,
    *,
    plan: str,
    mock_mode: bool,
    skip_images: bool,
    progress_cb: Callable[[float, str], None],
    log_cb: Callable[[str], None],
    out_dir: Path,
) -> Dict[str, Any]:
    from parse_simple_spu import parse_simple_spu_csv
    from listing_generator import ListingDraft
    from category_mapper import fill_attributes, pick_category
    from dianxiaomi_exporter import export_to_dianxiaomi
    from xlsx_exporter import export_to_xlsx

    progress_cb(0.05, "解析 SPU/SKU CSV")
    spus = parse_simple_spu_csv(csv_path)
    log_cb(f"[parse] {len(spus)} SPU / {sum(len(s.variants) for s in spus)} variants")

    progress_cb(0.15, "采集 JAN 信息（Rakuten）")
    jan_infos_by_spu: Dict[str, list] = {}
    if mock_mode or not os.environ.get("RAKUTEN_APP_ID", "").strip():
        log_cb("[jan] mock / RAKUTEN_APP_ID 未配置 — 跳过 JAN 采集")
        for spu in spus:
            jan_infos_by_spu[spu.spu_key] = []
    else:
        from jan_collector import fetch_batch
        for spu in spus:
            jans = [v.jan for v in spu.variants]
            try:
                infos = fetch_batch(jans)
                jan_infos_by_spu[spu.spu_key] = infos
                log_cb(f"[jan] SPU {spu.spu_key}: {sum(1 for i in infos if i.found)}/{len(infos)} hit")
            except Exception as e:
                log_cb(f"[jan] SPU {spu.spu_key} 采集失败 ({e!r}) — 跳过")
                jan_infos_by_spu[spu.spu_key] = []

    progress_cb(0.35, "AI 生成 listing")
    listings: Dict[str, Any] = {}
    has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if mock_mode or not (has_gemini or has_anthropic):
        log_cb("[ai] mock / 未配置 LLM key — 全部用 mock listing")
        for spu in spus:
            listings[spu.spu_key] = _make_mock_listing(spu, ListingDraft)
    else:
        from listing_generator import generate_listing
        for spu in spus:
            try:
                draft = generate_listing(spu, jan_infos_by_spu.get(spu.spu_key, []))
                listings[spu.spu_key] = draft
                log_cb(f"[ai] SPU {spu.spu_key}: title={draft.title[:30]}...")
            except Exception as e:
                log_cb(f"⚠️ AI 调用失败，fallback mock: {spu.spu_key} ({e!r})")
                listings[spu.spu_key] = _make_mock_listing(spu, ListingDraft)

    progress_cb(0.55, "类目匹配 + 属性填充")
    cat_assignment: Dict[str, str] = {}
    attributes: Dict[str, Dict[str, Any]] = {}
    for spu in spus:
        listing = listings.get(spu.spu_key)
        try:
            cat_id = pick_category(spu, listing)
        except Exception as e:
            log_cb(f"[cat] SPU {spu.spu_key} pick_category 失败 ({e!r}) — 用 100630")
            cat_id = "100630"
        cat_assignment[spu.spu_key] = cat_id
        try:
            attributes[spu.spu_key] = fill_attributes(cat_id, spu, listing)
        except Exception as e:
            log_cb(f"[cat] SPU {spu.spu_key} fill_attributes 失败 ({e!r}) — 空属性")
            attributes[spu.spu_key] = {}
        log_cb(f"[cat] SPU {spu.spu_key} → {cat_id}")

    progress_cb(0.75, "查找主图")
    image_refs: Dict[str, Any] = {}
    if skip_images or mock_mode:
        log_cb("[img] mock / skip_images — 全部留空")
        for spu in spus:
            for v in spu.variants:
                image_refs[v.jan] = None
    else:
        from image_finder import find_images_batch
        all_jans = [v.jan for spu in spus for v in spu.variants]
        try:
            refs = find_images_batch(all_jans)
            for jan, ref in zip(all_jans, refs):
                image_refs[jan] = ref
            n_hit = sum(1 for r in refs if r is not None)
            log_cb(f"[img] {n_hit}/{len(all_jans)} hit")
        except Exception as e:
            log_cb(f"⚠️ 主图查找失败：{e!r}")
            for jan in all_jans:
                image_refs[jan] = None

    progress_cb(0.9, "导出 xlsx")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    missing: List[str] = []
    if plan == "B":
        out_path = out_dir / f"shopee_dianxiaomi_{ts}.xlsx"
        result = export_to_dianxiaomi(
            spus=spus, listings=listings, attributes=attributes,
            image_refs=image_refs, out_path=out_path,
            category_assignment=cat_assignment, warn_logger=log_cb,
        )
        log_cb(f"[export-B] {result.row_count} rows → {out_path.name}")
        return {
            "output_path": out_path,
            "output_name": out_path.name,
            "missing_image_jans": list(result.missing_image_jans),
            "n_rows": result.row_count,
            "plan": "B",
        }
    else:
        results = export_to_xlsx(
            spus=spus, listings=listings, attributes=attributes,
            image_refs=image_refs, out_dir=out_dir,
            category_assignment=cat_assignment, timestamp=ts, warn_logger=log_cb,
        )
        for r in results:
            log_cb(f"[export-A] cat {r.category_id}: {r.row_count} rows → {r.path.name}")
            missing.extend(r.missing_image_jans)
        import zipfile
        zip_path = out_dir / f"shopee_mass_upload_{ts}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for r in results:
                z.write(r.path, arcname=r.path.name)
        return {
            "output_path": zip_path,
            "output_name": zip_path.name,
            "missing_image_jans": missing,
            "n_rows": sum(r.row_count for r in results),
            "plan": "A",
            "category_results": results,
        }


# --------------------------------------------------------------------------- #
# 4 Tab
# --------------------------------------------------------------------------- #
tab_auto, tab_manual, tab_refs, tab_history = st.tabs(
    ["🤖 全自动管线", "📤 手工模式", "🎨 参考图库", "📜 历史运行"]
)


# ============================================================
# Tab 1 · 🤖 全自动管线
# ============================================================
with tab_auto:
    st.subheader("一键完成：出图 + 上架 + 飞书通知")
    st.caption(
        "上传 SPU CSV → 自动按品类调 Nano Banana 2 出详情图（用 Tab 3 的参考图复刻风格）"
        " → 自动跑 Shopee mass-upload pipeline → N8N 调 Shopee API 上架 → 飞书群通知"
    )

    # --- Step 1: 选市场 + 上传 CSV ---
    col_left, col_right = st.columns([2, 1])
    with col_left:
        market = st.selectbox(
            "🌏 Shopee 站点",
            ["TW", "SG", "MY", "PH", "TH", "VN", "ID"],
            help="N8N 用对应市场的 Shopee Partner API 鉴权",
            key="auto_market",
        )
    with col_right:
        num_outputs = st.number_input(
            "🖼️ 每商品出图数",
            min_value=1, max_value=10, value=5,
            help="Nano Banana 2 一次出 N 张同商品不同角度",
            key="auto_num_imgs",
        )

    csv_file = st.file_uploader(
        "📄 上传 SPU+SKU CSV（A=SPU, B=SKU/JAN）",
        type=["csv"],
        key="auto_csv",
    )

    enable_image_gen = st.checkbox(
        "🎨 自动出图（用 Nano Banana 2 + Tab 3 参考图）",
        value=True,
        key="auto_enable_img_gen",
        help="关掉则跳过 AI 出图，仅做 listing 生成 + Shopee 上架（用 SKU 自带主图）",
    )

    # 环境检查
    has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if enable_image_gen and not has_gemini:
        st.warning("⚠️ 未配置 GEMINI_API_KEY — 出图功能将跳过（仍会跑 listing + 上架）")

    # --- Step 2: 触发 ---
    can_trigger = csv_file is not None
    if not can_trigger:
        st.info("先上传 SPU CSV 才能触发")

    if st.button(
        "🚀 触发全自动管线",
        type="primary",
        disabled=not can_trigger,
        use_container_width=True,
        key="auto_trigger",
    ):
        try:
            df = _read_uploaded_csv(csv_file)
            df, warns = _validate_and_dedup(df)
            for w in warns:
                st.warning(w)

            # 把 CSV 编码进 payload
            csv_bytes = df[["SPU", "SKU"]].to_csv(index=False).encode("utf-8")
            payload = {
                "market": market,
                "csv_b64": base64.b64encode(csv_bytes).decode("ascii"),
                "spu_count": int(df["SPU"].nunique()),
                "sku_count": int(len(df)),
                "enable_image_gen": enable_image_gen,
                "num_outputs_per_product": int(num_outputs),
                "triggered_at": datetime.utcnow().isoformat() + "Z",
            }

            user_email = st.session_state.get("user_email", "admin")
            run_id = trigger_workflow(
                module="shopee_mass_upload",
                webhook_path="shopee-mass-upload",
                payload=payload,
                conn=conn,
                triggered_by=user_email,
            )
            st.session_state["last_auto_run_id"] = run_id
            st.success(f"✅ 已触发，run_id = `{run_id}`")
        except Exception as e:
            st.error(f"❌ 触发失败：{e}")
            st.exception(e)

    # --- Step 3: 实时进度 ---
    last_id = st.session_state.get("last_auto_run_id")
    if last_id:
        st.divider()
        st.subheader(f"实时进度 · `{last_id[:8]}…`")
        placeholder = st.empty()
        for _ in range(20):
            row = get_run_status(conn, last_id)
            if not row:
                placeholder.warning("查不到该 run（DB 还没刷新）")
                break
            with placeholder.container():
                cols = st.columns(4)
                cols[0].metric("状态", row.get("status", "-"))
                cols[1].metric("模块", row.get("module", "-"))
                cols[2].metric("触发者", row.get("triggered_by", "-"))
                cols[3].metric("触发时间", str(row.get("triggered_at", "-"))[:19])
                if row.get("summary"):
                    st.json(row["summary"])
            if row.get("status") in ("completed", "failed"):
                break
            time.sleep(1.5)
        else:
            placeholder.info("仍在处理中 — 切到「📜 历史运行」Tab 或刷新页面继续观察")


# ============================================================
# Tab 2 · 📤 手工模式（兼容旧 T-309 流程）
# ============================================================
with tab_manual:
    st.subheader("仅出 mass-upload XLSX（不触发 N8N，本机下载）")
    st.caption("用于 dry-run 验证 / Boss 想自己手工传 Shopee 后台的场景")

    if "t309_step" not in st.session_state:
        st.session_state.t309_step = 1
    if "t309_csv_path" not in st.session_state:
        st.session_state.t309_csv_path = None
    if "t309_output_bytes" not in st.session_state:
        st.session_state.t309_output_bytes = None
    if "t309_output_name" not in st.session_state:
        st.session_state.t309_output_name = None
    if "t309_log_lines" not in st.session_state:
        st.session_state.t309_log_lines = []

    step = st.session_state.t309_step
    SAMPLE_CSV_PATH = _REPO_ROOT / "assets" / "shopee_simple_spu_sample.csv"

    prog_cols = st.columns(3)
    for i, label in enumerate(["1️⃣ 上传 CSV", "2️⃣ 选方案 + 配置", "3️⃣ 生成 + 下载"], 1):
        with prog_cols[i - 1]:
            if i == step:
                st.info(f"**{label}**")
            elif i < step:
                st.success(f"{label} ✓")
            else:
                st.caption(label)

    # --- Step 1 ---
    if step == 1:
        if SAMPLE_CSV_PATH.exists():
            with SAMPLE_CSV_PATH.open("rb") as fh:
                st.download_button(
                    "📥 下载示例 CSV",
                    data=fh.read(),
                    file_name="shopee_simple_spu_sample.csv",
                    mime="text/csv",
                    key="t309_dl_sample",
                )
        st.caption("第 1 行表头 SPU,SKU；第 2 行起 A 列填 SPU 编号、B 列填 13 位 JAN")

        uploaded = st.file_uploader("上传 CSV", type=["csv"], key="t309_upload")
        if uploaded is not None:
            try:
                df = _read_uploaded_csv(uploaded)
                df, warnings = _validate_and_dedup(df)
            except Exception as e:
                st.error(f"❌ {e}")
                st.stop()
            for w in warnings:
                st.warning(w)
            st.dataframe(df.head(20), use_container_width=True)
            c1, c2 = st.columns(2)
            c1.metric("SPU 数", f"{df['SPU'].nunique():,}")
            c2.metric("SKU 数", f"{len(df):,}")
            if st.button("▶️ 下一步", type="primary", key="t309_to_step2"):
                st.session_state.t309_csv_path = str(_df_to_clean_csv_path(df))
                st.session_state.t309_step = 2
                st.rerun()

    # --- Step 2 ---
    elif step == 2:
        plan_label = st.radio(
            "上传方案",
            options=[
                "方案 B · 店小秘批量（单 xlsx · 34 列）",
                "方案 A · Shopee 直传（5 类目 5 个 xlsx）",
            ],
            index=0,
            key="t309_plan",
        )
        plan = "B" if plan_label.startswith("方案 B") else "A"

        st.divider()
        has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        has_rakuten = bool(os.environ.get("RAKUTEN_APP_ID", "").strip())
        if has_gemini:
            st.success("✅ GEMINI_API_KEY 已配置（免费）")
        elif has_anthropic:
            st.success("✅ ANTHROPIC_API_KEY 已配置（付费）")
        else:
            st.warning("⚠️ 未配置 LLM key — 仅可跑 Mock 模式")
        if has_rakuten:
            st.success("✅ RAKUTEN_APP_ID 已配置")
        else:
            st.warning("⚠️ RAKUTEN_APP_ID 未配置 — JAN 查询会跳过")

        mock_mode = st.checkbox("Mock 模式（占位数据，验证流程）", value=True, key="t309_mock")
        skip_images = st.checkbox("跳过主图查找（dry-run 加速）", value=False, key="t309_skip_img")

        c1, c2 = st.columns(2)
        if c1.button("↩️ 返回上一步", key="t309_back_to_1"):
            st.session_state.t309_step = 1
            st.rerun()
        if c2.button("🚀 开始生成", type="primary", key="t309_run"):
            st.session_state["t309_plan_choice"] = plan
            st.session_state["t309_mock_mode"] = mock_mode
            st.session_state["t309_skip_images"] = skip_images
            st.session_state.t309_step = 3
            st.rerun()

    # --- Step 3 ---
    elif step == 3:
        csv_path = Path(st.session_state.t309_csv_path)
        plan = st.session_state.get("t309_plan_choice", "B")
        mock_mode = st.session_state.get("t309_mock_mode", True)
        skip_images = st.session_state.get("t309_skip_images", False)

        if st.session_state.t309_output_bytes is None:
            progress_bar = st.progress(0.0)
            status_box = st.empty()
            log_box = st.empty()
            log_lines: List[str] = []

            def _progress_cb(pct: float, msg: str) -> None:
                progress_bar.progress(min(max(pct, 0.0), 1.0))
                status_box.info(msg)

            def _log_cb(msg: str) -> None:
                log_lines.append(str(msg))
                log_box.code("\n".join(log_lines[-30:]), language="text")

            try:
                with tempfile.TemporaryDirectory() as out_dir:
                    result = run_pipeline(
                        csv_path,
                        plan=plan, mock_mode=mock_mode, skip_images=skip_images,
                        progress_cb=_progress_cb, log_cb=_log_cb, out_dir=Path(out_dir),
                    )
                    with result["output_path"].open("rb") as fh:
                        st.session_state.t309_output_bytes = fh.read()
                    st.session_state.t309_output_name = result["output_name"]
                    st.session_state["t309_n_rows"] = result["n_rows"]
                    st.session_state["t309_missing_imgs"] = result["missing_image_jans"]
                    st.session_state.t309_log_lines = log_lines
                progress_bar.progress(1.0)
                status_box.success("✅ 完成")
            except Exception as e:
                st.error(f"❌ 生成失败：{e}")
                with st.expander("traceback"):
                    st.code(traceback.format_exc())
                if st.button("↩️ 返回上一步", key="t309_back_after_fail"):
                    st.session_state.t309_step = 2
                    st.rerun()
                st.stop()

        if st.session_state.t309_output_bytes is not None:
            n_rows = st.session_state.get("t309_n_rows", 0)
            missing = st.session_state.get("t309_missing_imgs", [])
            st.success(f"✅ 完成 · {n_rows} rows")
            if missing:
                st.warning(f"⚠️ 主图查找失败：{len(missing)} JAN")

            out_name = st.session_state.t309_output_name or "shopee_listing.xlsx"
            mime = "application/zip" if out_name.endswith(".zip") \
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            st.download_button(
                "📥 下载 xlsx",
                data=st.session_state.t309_output_bytes,
                file_name=out_name, mime=mime, key="t309_dl_output",
            )
            with st.expander("📜 生成日志", expanded=False):
                st.code("\n".join(st.session_state.t309_log_lines), language="text")
            if st.button("↩️ 重新开始", key="t309_back_after_done"):
                for k in list(st.session_state.keys()):
                    if isinstance(k, str) and k.startswith("t309_") and k != "t309_en":
                        st.session_state.pop(k, None)
                st.session_state.t309_step = 1
                st.rerun()


# ============================================================
# Tab 3 · 🎨 参考图库
# ============================================================
with tab_refs:
    st.subheader("按品类一次性上传参考详情图（喂给 Nano Banana 2 复刻风格）")
    st.caption(
        f"存储位置：`{REFERENCE_DIR}` · 每个品类放 3-14 张 · "
        "Tab 1 触发自动管线时按 SPU 类目自动加载对应文件夹"
    )

    # 列出现有品类
    existing_categories = sorted([
        p.name for p in REFERENCE_DIR.iterdir() if p.is_dir()
    ]) if REFERENCE_DIR.exists() else []

    col_a, col_b = st.columns([2, 3])

    with col_a:
        st.write("**已有品类**")
        if existing_categories:
            for cat in existing_categories:
                cat_path = REFERENCE_DIR / cat
                imgs = list(cat_path.glob("*.png")) + list(cat_path.glob("*.jpg")) + list(cat_path.glob("*.jpeg"))
                st.write(f"- `{cat}` · {len(imgs)} 张")
        else:
            st.info("还没有任何品类")

        st.divider()
        st.write("**新建 / 选择品类**")
        category_input = st.text_input(
            "品类名（中日英都可，建议跟 Shopee 类目一致）",
            placeholder="如：ボトル / ステッカー / スマホスタンド",
            key="ref_cat_new",
        )
        if existing_categories:
            category_select = st.selectbox(
                "或从已有品类选",
                options=["（不选）"] + existing_categories,
                key="ref_cat_sel",
            )
            if category_select != "（不选）":
                category_input = category_select

    with col_b:
        st.write("**上传参考图到该品类**")
        if not category_input:
            st.info("先填左边的品类名才能上传图")
        else:
            uploaded_imgs = st.file_uploader(
                f"拖拽图片到「{category_input}」品类（多选）",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=True,
                key=f"ref_upload_{category_input}",
            )
            if uploaded_imgs:
                cat_dir = REFERENCE_DIR / category_input
                cat_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                saved = 0
                for i, f in enumerate(uploaded_imgs, 1):
                    safe_name = f"{ts}_{i:02d}_{f.name}"
                    (cat_dir / safe_name).write_bytes(f.getvalue())
                    saved += 1
                st.success(f"✅ 上传成功 · {saved} 张图存到 `{cat_dir}`")
                st.rerun()

            # 列出该品类已有图
            cat_dir = REFERENCE_DIR / category_input
            if cat_dir.exists():
                imgs = sorted(
                    list(cat_dir.glob("*.png")) + list(cat_dir.glob("*.jpg")) + list(cat_dir.glob("*.jpeg"))
                )
                if imgs:
                    st.divider()
                    st.write(f"**`{category_input}` 已有 {len(imgs)} 张参考图**")
                    cols = st.columns(min(4, len(imgs)))
                    for i, img_path in enumerate(imgs[:12]):
                        with cols[i % 4]:
                            st.image(str(img_path), caption=img_path.name[:20], width=180)
                            if st.button("🗑 删", key=f"del_{img_path.name}"):
                                img_path.unlink()
                                st.rerun()


# ============================================================
# Tab 4 · 📜 历史运行
# ============================================================
with tab_history:
    st.subheader("最近 50 次自动化运行（含 Shopee 上架 + 出图 + 改廃监控等）")

    module_filter = st.selectbox(
        "按模块过滤",
        ["全部", "shopee_mass_upload", "image_gen", "discontinue_confirm", "nst_order"],
        key="history_module",
    )

    runs = list_recent_runs(
        conn,
        module=None if module_filter == "全部" else module_filter,
        limit=50,
    )
    if not runs:
        st.info("还没有任何运行记录")
    else:
        rows = []
        for r in runs:
            rows.append({
                "run_id": r["run_id"][:8] + "...",
                "module": r["module"],
                "status": r["status"],
                "triggered_by": r["triggered_by"],
                "triggered_at": (r.get("triggered_at") or "")[:19],
                "completed_at": (r.get("completed_at") or "")[:19],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        ids = [r["run_id"] for r in runs]
        sel = st.selectbox(
            "查看 payload + summary",
            ids,
            format_func=lambda x: f"{x[:8]}... · "
            + next(r['module'] for r in runs if r['run_id'] == x)
            + " · "
            + next(r['status'] for r in runs if r['run_id'] == x),
            key="history_sel",
        )
        if sel:
            row = next(r for r in runs if r["run_id"] == sel)
            with st.expander("payload", expanded=False):
                p = row.get("payload")
                try:
                    st.json(json.loads(p) if isinstance(p, str) else p)
                except Exception:
                    st.code(p or "(empty)")
            with st.expander("summary", expanded=True):
                s = row.get("summary")
                try:
                    st.json(json.loads(s) if isinstance(s, str) else s)
                except Exception:
                    st.code(s or "(empty)")
