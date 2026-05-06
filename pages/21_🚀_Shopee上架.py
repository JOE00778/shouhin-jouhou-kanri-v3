"""模块 #21 Shopee 上架（T-309）· 把 shopee-listing pipeline 整合到 Streamlit。

业务定位：Boss/运营在 UI 里上传 SPU+SKU CSV → 选方案 A/B → 一键产出 xlsx 下载。

数据流：
  ┌── Step 1：上传 SPU+SKU CSV（A=SPU, B=SKU 极简两列）
  ├── Step 2：选方案（A · Shopee 直传 · 5 类目分文件 / B · 店小秘批量 · 单 xlsx 34 列）
  └── Step 3：跑 pipeline（解析 → JAN → AI → 类目 → 主图 → 导出）→ 下载 xlsx

依赖 pipeline modules（来自 ~/CC/shopee-listing/scripts/）：
  - parse_simple_spu.parse_simple_spu_csv  (T-310)
  - jan_collector.fetch_batch              (T-302)
  - listing_generator.generate_listing     (T-303, mock 时跳过)
  - category_mapper.pick_category, fill_attributes  (T-304)
  - image_finder.find_images_batch         (T-305, 可选跳过)
  - dianxiaomi_exporter.export_to_dianxiaomi (T-310, 方案 B)
  - xlsx_exporter.export_to_xlsx           (T-306, 方案 A)
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import streamlit as st

from shared.auth import is_admin, require_admin
from shared.i18n import get_lang, lang_selector, t

# --------------------------------------------------------------------------- #
# sys.path bootstrap — let us import shopee-listing pipeline modules
# 商品信息管理 / shopee-listing 是平行目录（~/CC/{商品信息管理,shopee-listing}/）
# --------------------------------------------------------------------------- #
SHOPEE_LISTING_ROOT = Path(__file__).resolve().parents[2] / "shopee-listing"
for _p in (SHOPEE_LISTING_ROOT, SHOPEE_LISTING_ROOT / "scripts"):
    p_str = str(_p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)


# --------------------------------------------------------------------------- #
# 三语 i18n 兜底（标题/按钮/警告 ja/zh/en）
# 既有 shared.i18n 仅 ja/zh，本 page 自带 en 翻译表。
# --------------------------------------------------------------------------- #
_PAGE_STRINGS_EN: Dict[str, str] = {
    "🚀 Shopee 上架": "🚀 Shopee Listing",
    "Shopee 上架": "Shopee Listing",
    "把 SPU+SKU 两列 CSV 一键转成 Shopee 直传 / 店小秘批量 xlsx":
        "Turn a 2-column SPU+SKU CSV into Shopee direct-upload or Dianxiaomi xlsx",
    "1️⃣ 上传 SPU+SKU CSV": "1️⃣ Upload SPU+SKU CSV",
    "2️⃣ 选方案 + 配置": "2️⃣ Pick channel + config",
    "3️⃣ 生成 + 下载": "3️⃣ Generate + Download",
    "📋 步骤 1 / 3：上传 SPU+SKU CSV": "📋 Step 1 / 3: Upload SPU+SKU CSV",
    "📋 步骤 2 / 3：选方案 + 配置": "📋 Step 2 / 3: Pick channel + config",
    "📋 步骤 3 / 3：生成 + 下载": "📋 Step 3 / 3: Generate + download",
    "上传 CSV 文件": "Upload CSV file",
    "📥 下载示例 CSV": "📥 Download sample CSV",
    "前 20 行预览": "Preview (first 20 rows)",
    "SPU 数": "SPU count",
    "SKU 数": "SKU count",
    "❌ CSV 至少需要 A 列 (SPU) + B 列 (SKU)": "❌ CSV needs at least column A (SPU) + column B (SKU)",
    "示例 CSV：第 1 行表头 SPU,SKU；第 2 行起 A 列填 SPU 编号、B 列填 13 位 JAN（即 SKU）":
        "Sample CSV: row 1 header SPU,SKU; from row 2 fill A with SPU id and B with 13-digit JAN (= SKU)",
    "上传方案": "Channel",
    "方案 A · Shopee 直传（5 类目 5 个 xlsx）": "Plan A · Shopee direct upload (5 xlsx by category)",
    "方案 B · 店小秘批量（单 xlsx · 34 列）": "Plan B · Dianxiaomi batch (single xlsx · 34 cols)",
    "Mock 模式（不调 LLM/Rakuten，用占位数据生成 xlsx 验证流程）":
        "Mock mode (skip LLM/Rakuten, fill placeholders to verify pipeline)",
    "跳过主图查找（dry-run 时加速）": "Skip main-image lookup (faster dry-run)",
    "✅ ANTHROPIC_API_KEY 已配置": "✅ ANTHROPIC_API_KEY configured",
    "⚠️ ANTHROPIC_API_KEY 未配置 — 仅可跑 Mock 模式":
        "⚠️ ANTHROPIC_API_KEY not set — only Mock mode works",
    "✅ RAKUTEN_APP_ID 已配置": "✅ RAKUTEN_APP_ID configured",
    "⚠️ RAKUTEN_APP_ID 未配置 — JAN 查询会跳过（Mock 模式不受影响）":
        "⚠️ RAKUTEN_APP_ID not set — JAN lookup will be skipped (Mock mode unaffected)",
    "🚀 开始生成": "🚀 Start",
    "↩️ 返回上一步": "↩️ Back",
    "📥 下载 xlsx": "📥 Download xlsx",
    "📜 生成日志": "📜 Generation log",
    "解析 SPU/SKU CSV": "Parsing SPU/SKU CSV",
    "采集 JAN 信息（Rakuten）": "Collecting JAN info (Rakuten)",
    "AI 生成 listing": "AI listing generation",
    "类目匹配 + 属性填充": "Category mapping + attribute fill",
    "查找主图": "Looking up main image",
    "导出 xlsx": "Exporting xlsx",
    "✅ 完成": "✅ Done",
    "⚠️ SKU 重复，已自动去重（保留首次出现）": "⚠️ Duplicate SKU detected — kept first occurrence",
    "⚠️ AI 调用失败，已 fallback 到 mock 占位": "⚠️ AI call failed — falling back to mock placeholder",
    "⚠️ 主图查找失败，主图列将留空": "⚠️ Main-image lookup failed — main-image column left blank",
    "❌ 生成失败": "❌ Generation failed",
    "🚫 仅管理员可访问": "🚫 Admin only",
}


def tt(text: str) -> str:
    """三语翻译：lang=en 优先查 _PAGE_STRINGS_EN；否则委托给 shared.i18n.t（ja/zh）."""
    lang = get_lang()
    if lang == "en":
        return _PAGE_STRINGS_EN.get(text, t(text))
    return t(text)


# --------------------------------------------------------------------------- #
# Page setup
# --------------------------------------------------------------------------- #
st.set_page_config(page_title=tt("Shopee 上架"), page_icon="🚀", layout="wide")
require_admin()
lang_selector()

# Page 自带 en 切换（独立于 shared.i18n 的 ja/zh，仅作用于本 page）
with st.sidebar:
    st.divider()
    en_on = st.checkbox("🌐 English (this page)", value=False, key="t309_en")
    if en_on:
        st.session_state["lang"] = "en"

st.title(tt("🚀 Shopee 上架"))
st.caption(tt("把 SPU+SKU 两列 CSV 一键转成 Shopee 直传 / 店小秘批量 xlsx"))


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
def _reset_state() -> None:
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith("t309_"):
            if k == "t309_en":
                continue
            st.session_state.pop(k, None)


if "t309_step" not in st.session_state:
    st.session_state.t309_step = 1
if "t309_csv_path" not in st.session_state:
    st.session_state.t309_csv_path = None
if "t309_spus" not in st.session_state:
    st.session_state.t309_spus = None
if "t309_dup_warnings" not in st.session_state:
    st.session_state.t309_dup_warnings = []
if "t309_output_bytes" not in st.session_state:
    st.session_state.t309_output_bytes = None
if "t309_output_name" not in st.session_state:
    st.session_state.t309_output_name = None
if "t309_log_lines" not in st.session_state:
    st.session_state.t309_log_lines = []


# --------------------------------------------------------------------------- #
# Progress header
# --------------------------------------------------------------------------- #
step = st.session_state.t309_step
prog_cols = st.columns(3)
for i, label in enumerate(
    [
        tt("1️⃣ 上传 SPU+SKU CSV"),
        tt("2️⃣ 选方案 + 配置"),
        tt("3️⃣ 生成 + 下载"),
    ],
    1,
):
    with prog_cols[i - 1]:
        if i == step:
            st.info(f"**{label}**")
        elif i < step:
            st.success(f"{label} ✓")
        else:
            st.caption(label)
st.divider()


# --------------------------------------------------------------------------- #
# Helpers — CSV parsing + dedup
# --------------------------------------------------------------------------- #
def _read_uploaded_csv(uploaded) -> pd.DataFrame:
    """读取上传 CSV → DataFrame。"""
    raw = uploaded.read()
    try:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="cp932", keep_default_na=False)


def _validate_and_dedup(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """校验列 + 去重。返回 (clean_df, warnings)."""
    warnings: list[str] = []
    if df.shape[1] < 2:
        raise ValueError(tt("❌ CSV 至少需要 A 列 (SPU) + B 列 (SKU)"))

    # 重命名前两列 SPU / SKU（不管原表头叫什么，按位置取）
    df = df.copy()
    cols = list(df.columns)
    df = df.rename(columns={cols[0]: "SPU", cols[1]: "SKU"})

    # 去前后空白
    df["SPU"] = df["SPU"].astype(str).str.strip()
    df["SKU"] = df["SKU"].astype(str).str.strip()

    # 丢空行
    df = df[(df["SPU"] != "") & (df["SKU"] != "")].reset_index(drop=True)

    # SKU 重复 → 保留首次
    dup_mask = df["SKU"].duplicated(keep="first")
    if dup_mask.any():
        dup_skus = df.loc[dup_mask, "SKU"].tolist()
        warnings.append(
            f"{tt('⚠️ SKU 重复，已自动去重（保留首次出现）')}：{', '.join(dup_skus[:10])}"
            + ("..." if len(dup_skus) > 10 else "")
        )
        df = df[~dup_mask].reset_index(drop=True)

    return df, warnings


def _df_to_clean_csv_path(df: pd.DataFrame) -> Path:
    """把去重后的 DataFrame 写到临时 CSV 给 parse_simple_spu_csv 吃."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    )
    df[["SPU", "SKU"]].to_csv(tmp.name, index=False)
    tmp.close()
    return Path(tmp.name)


# --------------------------------------------------------------------------- #
# Pipeline runner — wraps stages with progress + log callback
# --------------------------------------------------------------------------- #
def _make_mock_listing(spu, ListingDraftCls):
    """生成占位 ListingDraft（mock 模式 / AI 失败 fallback）."""
    return ListingDraftCls(
        title=f"<MOCK> {spu.spu_key} — Direct from Japan",
        description=(
            f"<MOCK description for {spu.spu_key}>\n"
            "[NOTE] Placeholder generated by Streamlit page (Mock mode). "
            "Replace by running listing_generator with ANTHROPIC_API_KEY set."
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
    plan: str,  # "A" or "B"
    mock_mode: bool,
    skip_images: bool,
    progress_cb: Callable[[float, str], None],
    log_cb: Callable[[str], None],
    out_dir: Path,
) -> Dict[str, Any]:
    """端到端跑 pipeline，返回 {output_path, output_name, missing_image_jans, n_rows}.

    plan: "A" → xlsx_exporter（5 类目分文件）/ "B" → dianxiaomi_exporter（单文件）
    Streamlit page 仅打包「下载第一个生成的 xlsx」（方案 A 多文件时打 zip）.
    """
    # 延迟 import：保持 page module 在没有 shopee-listing 路径时仍可导入
    from parse_simple_spu import parse_simple_spu_csv  # noqa: WPS433
    from listing_generator import ListingDraft  # noqa: WPS433
    from category_mapper import fill_attributes, pick_category  # noqa: WPS433
    from dianxiaomi_exporter import export_to_dianxiaomi  # noqa: WPS433
    from xlsx_exporter import export_to_xlsx  # noqa: WPS433

    # ── Stage 1：解析 CSV ─────────────────────────────────────
    progress_cb(0.05, tt("解析 SPU/SKU CSV"))
    spus = parse_simple_spu_csv(csv_path)
    log_cb(f"[parse] {len(spus)} SPU / {sum(len(s.variants) for s in spus)} variants")

    # ── Stage 2：JAN 采集 ──────────────────────────────────────
    progress_cb(0.15, tt("采集 JAN 信息（Rakuten）"))
    jan_infos_by_spu: Dict[str, list] = {}
    if mock_mode or not os.environ.get("RAKUTEN_APP_ID", "").strip():
        log_cb("[jan] mock / RAKUTEN_APP_ID 未配置 — 跳过 JAN 采集")
        for spu in spus:
            jan_infos_by_spu[spu.spu_key] = []
    else:
        from jan_collector import fetch_batch  # noqa: WPS433
        for spu in spus:
            jans = [v.jan for v in spu.variants]
            try:
                infos = fetch_batch(jans)
                jan_infos_by_spu[spu.spu_key] = infos
                log_cb(f"[jan] SPU {spu.spu_key}: {sum(1 for i in infos if i.found)}/{len(infos)} hit")
            except Exception as e:  # noqa: BLE001
                log_cb(f"[jan] SPU {spu.spu_key} 采集失败 ({e!r}) — 跳过")
                jan_infos_by_spu[spu.spu_key] = []

    # ── Stage 3：AI 生成 listing ─────────────────────────────
    progress_cb(0.35, tt("AI 生成 listing"))
    listings: Dict[str, Any] = {}
    if mock_mode or not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log_cb("[ai] mock / ANTHROPIC_API_KEY 未配置 — 全部用 mock listing")
        for spu in spus:
            listings[spu.spu_key] = _make_mock_listing(spu, ListingDraft)
    else:
        from listing_generator import generate_listing  # noqa: WPS433
        for spu in spus:
            try:
                draft = generate_listing(
                    spu, jan_infos_by_spu.get(spu.spu_key, [])
                )
                listings[spu.spu_key] = draft
                log_cb(f"[ai] SPU {spu.spu_key}: title={draft.title[:30]}...")
            except Exception as e:  # noqa: BLE001
                log_cb(f"{tt('⚠️ AI 调用失败，已 fallback 到 mock 占位')}: {spu.spu_key} ({e!r})")
                listings[spu.spu_key] = _make_mock_listing(spu, ListingDraft)

    # ── Stage 4：类目 + 属性 ──────────────────────────────────
    progress_cb(0.55, tt("类目匹配 + 属性填充"))
    cat_assignment: Dict[str, str] = {}
    attributes: Dict[str, Dict[str, Any]] = {}
    for spu in spus:
        listing = listings.get(spu.spu_key)
        try:
            cat_id = pick_category(spu, listing)
        except Exception as e:  # noqa: BLE001
            log_cb(f"[cat] SPU {spu.spu_key} pick_category 失败 ({e!r}) — 用 100630")
            cat_id = "100630"
        cat_assignment[spu.spu_key] = cat_id
        try:
            attributes[spu.spu_key] = fill_attributes(cat_id, spu, listing)
        except Exception as e:  # noqa: BLE001
            log_cb(f"[cat] SPU {spu.spu_key} fill_attributes 失败 ({e!r}) — 空属性")
            attributes[spu.spu_key] = {}
        log_cb(f"[cat] SPU {spu.spu_key} → {cat_id}")

    # ── Stage 5：主图 ─────────────────────────────────────────
    progress_cb(0.75, tt("查找主图"))
    image_refs: Dict[str, Any] = {}
    if skip_images or mock_mode:
        log_cb("[img] mock / skip_images — 全部留空")
        for spu in spus:
            for v in spu.variants:
                image_refs[v.jan] = None
    else:
        from image_finder import find_images_batch  # noqa: WPS433
        all_jans = [v.jan for spu in spus for v in spu.variants]
        try:
            refs = find_images_batch(all_jans)
            for jan, ref in zip(all_jans, refs):
                image_refs[jan] = ref
            n_hit = sum(1 for r in refs if r is not None)
            log_cb(f"[img] {n_hit}/{len(all_jans)} hit")
        except Exception as e:  # noqa: BLE001
            log_cb(f"{tt('⚠️ 主图查找失败，主图列将留空')}: {e!r}")
            for jan in all_jans:
                image_refs[jan] = None

    # ── Stage 6：导出 xlsx ─────────────────────────────────────
    progress_cb(0.9, tt("导出 xlsx"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    missing: List[str] = []
    if plan == "B":
        out_path = out_dir / f"shopee_dianxiaomi_{ts}.xlsx"
        result = export_to_dianxiaomi(
            spus=spus,
            listings=listings,
            attributes=attributes,
            image_refs=image_refs,
            out_path=out_path,
            category_assignment=cat_assignment,
            warn_logger=log_cb,
        )
        log_cb(f"[export-B] {result.row_count} rows → {out_path.name}")
        missing = list(result.missing_image_jans)
        return {
            "output_path": out_path,
            "output_name": out_path.name,
            "missing_image_jans": missing,
            "n_rows": result.row_count,
            "plan": "B",
        }
    else:
        results = export_to_xlsx(
            spus=spus,
            listings=listings,
            attributes=attributes,
            image_refs=image_refs,
            out_dir=out_dir,
            category_assignment=cat_assignment,
            timestamp=ts,
            warn_logger=log_cb,
        )
        for r in results:
            log_cb(f"[export-A] cat {r.category_id}: {r.row_count} rows → {r.path.name}")
            missing.extend(r.missing_image_jans)
        # 多文件：打 zip
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
# Step 1：上传 + 校验
# --------------------------------------------------------------------------- #
SAMPLE_CSV_PATH = Path(__file__).resolve().parents[1] / "assets" / "shopee_simple_spu_sample.csv"

if step == 1:
    st.subheader(tt("📋 步骤 1 / 3：上传 SPU+SKU CSV"))

    # 示例 CSV 下载
    if SAMPLE_CSV_PATH.exists():
        with SAMPLE_CSV_PATH.open("rb") as fh:
            st.download_button(
                tt("📥 下载示例 CSV"),
                data=fh.read(),
                file_name="shopee_simple_spu_sample.csv",
                mime="text/csv",
                key="t309_dl_sample",
            )
    st.caption(tt("示例 CSV：第 1 行表头 SPU,SKU；第 2 行起 A 列填 SPU 编号、B 列填 13 位 JAN（即 SKU）"))

    uploaded = st.file_uploader(tt("上传 CSV 文件"), type=["csv"], key="t309_upload")
    if uploaded is not None:
        try:
            df = _read_uploaded_csv(uploaded)
            df, warnings = _validate_and_dedup(df)
        except Exception as e:  # noqa: BLE001
            st.error(f"{tt('❌ CSV 至少需要 A 列 (SPU) + B 列 (SKU)')}\n\n{e}")
            st.stop()

        for w in warnings:
            st.warning(w)

        st.session_state.t309_dup_warnings = warnings

        # 预览前 20 行
        st.subheader(tt("前 20 行预览"))
        st.dataframe(df.head(20), use_container_width=True)

        c1, c2 = st.columns(2)
        c1.metric(tt("SPU 数"), f"{df['SPU'].nunique():,}")
        c2.metric(tt("SKU 数"), f"{len(df):,}")

        if st.button(tt("🚀 开始生成").replace("🚀 ", "▶️ "), type="primary", key="t309_to_step2"):
            csv_path = _df_to_clean_csv_path(df)
            st.session_state.t309_csv_path = str(csv_path)
            st.session_state.t309_step = 2
            st.rerun()


# --------------------------------------------------------------------------- #
# Step 2：选方案 + 配置
# --------------------------------------------------------------------------- #
elif step == 2:
    st.subheader(tt("📋 步骤 2 / 3：选方案 + 配置"))

    plan_label = st.radio(
        tt("上传方案"),
        options=[
            tt("方案 B · 店小秘批量（单 xlsx · 34 列）"),
            tt("方案 A · Shopee 直传（5 类目 5 个 xlsx）"),
        ],
        index=0,
        key="t309_plan",
    )
    plan = "B" if "B" in plan_label[:8] or "B " in plan_label[:8] or plan_label.startswith("方案 B") or plan_label.startswith("Plan B") else "A"

    st.divider()

    # 环境变量状态
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    has_rakuten = bool(os.environ.get("RAKUTEN_APP_ID", "").strip())
    if has_anthropic:
        st.success(tt("✅ ANTHROPIC_API_KEY 已配置"))
    else:
        st.warning(tt("⚠️ ANTHROPIC_API_KEY 未配置 — 仅可跑 Mock 模式"))
    if has_rakuten:
        st.success(tt("✅ RAKUTEN_APP_ID 已配置"))
    else:
        st.warning(tt("⚠️ RAKUTEN_APP_ID 未配置 — JAN 查询会跳过（Mock 模式不受影响）"))

    mock_mode = st.checkbox(
        tt("Mock 模式（不调 LLM/Rakuten，用占位数据生成 xlsx 验证流程）"),
        value=True,
        key="t309_mock",
    )
    skip_images = st.checkbox(
        tt("跳过主图查找（dry-run 时加速）"),
        value=False,
        key="t309_skip_img",
    )

    c1, c2 = st.columns(2)
    if c1.button(tt("↩️ 返回上一步"), key="t309_back_to_1"):
        st.session_state.t309_step = 1
        st.rerun()
    if c2.button(tt("🚀 开始生成"), type="primary", key="t309_run"):
        st.session_state["t309_plan_choice"] = plan
        st.session_state["t309_mock_mode"] = mock_mode
        st.session_state["t309_skip_images"] = skip_images
        st.session_state.t309_step = 3
        st.rerun()


# --------------------------------------------------------------------------- #
# Step 3：跑 pipeline + 下载
# --------------------------------------------------------------------------- #
elif step == 3:
    st.subheader(tt("📋 步骤 3 / 3：生成 + 下载"))

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
                    plan=plan,
                    mock_mode=mock_mode,
                    skip_images=skip_images,
                    progress_cb=_progress_cb,
                    log_cb=_log_cb,
                    out_dir=Path(out_dir),
                )
                # 把生成文件读进 session_state（出 with 块前）
                with result["output_path"].open("rb") as fh:
                    st.session_state.t309_output_bytes = fh.read()
                st.session_state.t309_output_name = result["output_name"]
                st.session_state["t309_n_rows"] = result["n_rows"]
                st.session_state["t309_missing_imgs"] = result["missing_image_jans"]
                st.session_state.t309_log_lines = log_lines
            progress_bar.progress(1.0)
            status_box.success(tt("✅ 完成"))
        except Exception as e:  # noqa: BLE001
            st.error(f"{tt('❌ 生成失败')}: {e}")
            with st.expander("traceback"):
                st.code(traceback.format_exc())
            if st.button(tt("↩️ 返回上一步"), key="t309_back_after_fail"):
                st.session_state.t309_step = 2
                st.rerun()
            st.stop()

    # 下载 + 日志
    if st.session_state.t309_output_bytes is not None:
        n_rows = st.session_state.get("t309_n_rows", 0)
        missing = st.session_state.get("t309_missing_imgs", [])
        st.success(f"{tt('✅ 完成')} · {n_rows} rows")
        if missing:
            st.warning(f"{tt('⚠️ 主图查找失败，主图列将留空')}：{len(missing)} JAN")

        out_name = st.session_state.t309_output_name or "shopee_listing.xlsx"
        mime = (
            "application/zip"
            if out_name.endswith(".zip")
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        st.download_button(
            tt("📥 下载 xlsx"),
            data=st.session_state.t309_output_bytes,
            file_name=out_name,
            mime=mime,
            key="t309_dl_output",
        )

        with st.expander(tt("📜 生成日志"), expanded=False):
            st.code("\n".join(st.session_state.t309_log_lines), language="text")

        if st.button(tt("↩️ 返回上一步"), key="t309_back_after_done"):
            _reset_state()
            st.session_state.t309_step = 1
            st.rerun()
