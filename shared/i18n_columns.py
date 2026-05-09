"""i18n column 标签 · ja / zh / en 三语 dataframe 列名翻译。

设计：
- DB 内部 column 保持英文（性能 + SQL 简洁）
- UI 显示 / 导出文件用对应语言列名
- page st.dataframe 渲染前调 localize_df(df) 自动 rename

使用：
    from shared.i18n_columns import localize_df
    df = pd.DataFrame(...)
    st.dataframe(localize_df(df), use_container_width=True)

切换语言：shared/i18n.lang_selector() 已经有 ja/zh/en，本模块自动跟随。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from shared.i18n import get_lang


# ============================================================
# 翻译表 · 英文 column → {ja, zh, en}
# ============================================================
COLUMN_LABELS: dict[str, dict[str, str]] = {
    # ── ID / 商品基础（item_v2）──
    "jan":              {"ja": "JAN",          "zh": "JAN",       "en": "JAN"},
    "item_code":        {"ja": "アイテム",      "zh": "商品编号",   "en": "Item Code"},
    "internal_id":      {"ja": "内部ID",        "zh": "内部 ID",   "en": "Internal ID"},
    "upc":              {"ja": "UPCコード",     "zh": "UPC",       "en": "UPC"},
    "display_name":     {"ja": "表示名",        "zh": "商品名",     "en": "Display Name"},
    "maker":            {"ja": "メーカー",      "zh": "品牌",       "en": "Maker"},
    "rank":             {"ja": "ランク",        "zh": "等级",       "en": "Rank"},
    "handling_status":  {"ja": "取扱区分",      "zh": "经营状态",   "en": "Handling Status"},
    "department":       {"ja": "部門",          "zh": "部门",       "en": "Department"},

    # ── 进货 ──
    "std_cost":         {"ja": "定義原価",      "zh": "定义原价",   "en": "Std Cost"},
    "avg_cost":         {"ja": "平均原価",      "zh": "平均原价",   "en": "Avg Cost"},
    "actual_cost":      {"ja": "実績原価",      "zh": "实绩原价",   "en": "Actual Cost"},
    "min_cost":         {"ja": "最安原価",      "zh": "最低原价",   "en": "Min Cost"},
    "case_qty":         {"ja": "ケース入数",    "zh": "箱入数",     "en": "Case Qty"},
    "order_lot":        {"ja": "発注ロット",    "zh": "订货起订量", "en": "Order Lot"},
    "weight":           {"ja": "重量",          "zh": "重量",       "en": "Weight"},
    "supplier_default": {"ja": "仕入先",        "zh": "默认供应商", "en": "Supplier"},
    "supplier_name":    {"ja": "仕入先",        "zh": "供应商",     "en": "Supplier"},
    "supply_cycle_days":{"ja": "仕入サイクル",  "zh": "进货周期",   "en": "Supply Cycle"},
    "bucket":           {"ja": "サイクル区分",  "zh": "周期分类",   "en": "Bucket"},
    "cost_class":       {"ja": "原価ランク",    "zh": "原价等级",   "en": "Cost Class"},
    "unit_cost":        {"ja": "単価",          "zh": "单价",       "en": "Unit Cost"},
    "currency":         {"ja": "通貨",          "zh": "币种",       "en": "Currency"},

    # ── 库存 ──
    "on_hand_total":    {"ja": "手持合計",      "zh": "总在库",     "en": "On Hand"},
    "on_order_total":   {"ja": "注文済合計",    "zh": "在途总量",   "en": "On Order"},
    "qty_on_hand":      {"ja": "手持",          "zh": "在库",       "en": "Qty On Hand"},
    "qty_committed":    {"ja": "確保済",        "zh": "已占用",     "en": "Committed"},
    "qty_committed_total": {"ja": "確保済合計", "zh": "已占用合计", "en": "Committed Total"},
    "qty_backorder":    {"ja": "バック・オーダー", "zh": "缺货中",   "en": "Backorder"},
    "total_amount":     {"ja": "在庫金額",      "zh": "库存金额",   "en": "Stock Value"},
    "location":         {"ja": "場所",          "zh": "仓库",       "en": "Location"},
    "bin_number":       {"ja": "保管棚",        "zh": "货架号",     "en": "Bin"},
    "snapshot_at":      {"ja": "棚卸日",        "zh": "快照时间",   "en": "Snapshot At"},
    "status":           {"ja": "ステータス",    "zh": "状态",       "en": "Status"},
    "owner":            {"ja": "担当者",        "zh": "负责人",     "en": "Owner"},

    # ── 销售（shop_sales / item_sales_history）──
    "shop_id":          {"ja": "店舗ID",        "zh": "店铺 ID",   "en": "Shop ID"},
    "store":            {"ja": "店舗",          "zh": "店铺",       "en": "Store"},
    "fb_store":         {"ja": "FB店舗",        "zh": "FB 店铺",    "en": "FB Store"},
    "market_id":        {"ja": "市場",          "zh": "市场",       "en": "Market"},
    "platform":         {"ja": "プラットフォーム", "zh": "平台",     "en": "Platform"},
    "channel":          {"ja": "チャネル",      "zh": "渠道",       "en": "Channel"},
    "granularity":      {"ja": "粒度",          "zh": "粒度",       "en": "Granularity"},
    "period_start":     {"ja": "期間開始",      "zh": "开始日期",   "en": "Period Start"},
    "period_end":       {"ja": "期間終了",      "zh": "结束日期",   "en": "Period End"},
    "year_month":       {"ja": "年月",          "zh": "年月",       "en": "Year-Month"},
    "qty_sold":         {"ja": "販売数量",      "zh": "销量",       "en": "Qty Sold"},
    "unit_price":       {"ja": "単価",          "zh": "单价",       "en": "Unit Price"},
    "revenue":          {"ja": "売上",          "zh": "收入",       "en": "Revenue"},
    "revenue_jpy":      {"ja": "売上(JPY)",     "zh": "收入(JPY)",  "en": "Revenue JPY"},
    "cost":             {"ja": "原価",          "zh": "成本",       "en": "Cost"},
    "gross_profit":     {"ja": "粗利",          "zh": "毛利",       "en": "Gross Profit"},
    "gross_margin":     {"ja": "粗利率",        "zh": "毛利率",     "en": "Gross Margin"},
    "rank_at_sale":     {"ja": "販売時ランク",  "zh": "销售时等级", "en": "Rank At Sale"},

    # ── shop_monthly KPI ──
    "gmv":              {"ja": "GMV",          "zh": "GMV",        "en": "GMV"},
    "profit":           {"ja": "利益",          "zh": "利润",       "en": "Profit"},
    "margin_rate":      {"ja": "利益率",        "zh": "利润率",     "en": "Margin Rate"},
    "profit_contrib":   {"ja": "利益貢献率",    "zh": "利润贡献率", "en": "Profit Contrib"},
    "deduction_total":  {"ja": "控除合計",      "zh": "扣减合计",   "en": "Deductions"},
    "order_count":      {"ja": "注文数",        "zh": "订单数",     "en": "Orders"},
    "store_rating":     {"ja": "店舗評価",      "zh": "店铺评分",   "en": "Rating"},
    "online_products":  {"ja": "出品数",        "zh": "在线商品",   "en": "Online SKUs"},

    # ── 通用元数据 ──
    "source":           {"ja": "ソース",        "zh": "来源",       "en": "Source"},
    "source_file":      {"ja": "元ファイル",    "zh": "源文件",     "en": "Source File"},
    "source_priority":  {"ja": "優先度",        "zh": "优先级",     "en": "Priority"},
    "imported_at":      {"ja": "登録日時",      "zh": "导入时间",   "en": "Imported At"},
    "updated_at":       {"ja": "更新日時",      "zh": "更新时间",   "en": "Updated At"},
    "created_at":       {"ja": "作成日時",      "zh": "创建时间",   "en": "Created At"},
    "completed_at":     {"ja": "完了日時",      "zh": "完成时间",   "en": "Completed At"},
    "triggered_at":     {"ja": "実行日時",      "zh": "触发时间",   "en": "Triggered At"},
    "triggered_by":     {"ja": "実行者",        "zh": "触发者",     "en": "Triggered By"},
    "ran_at":           {"ja": "実行日時",      "zh": "运行时间",   "en": "Ran At"},
    "active":           {"ja": "有効",          "zh": "启用",       "en": "Active"},
    "display_name_2":   {"ja": "表示名",        "zh": "显示名",     "en": "Display Name"},

    # ── ETL / automation_runs / _v2_migration_runs ──
    "step":             {"ja": "ステップ",      "zh": "步骤",       "en": "Step"},
    "source_table":     {"ja": "ソーステーブル", "zh": "源表",      "en": "Source Table"},
    "rows_read":        {"ja": "読込件数",      "zh": "读入行数",   "en": "Rows Read"},
    "rows_written":     {"ja": "書込件数",      "zh": "写入行数",   "en": "Rows Written"},
    "errors":           {"ja": "エラー数",      "zh": "错误数",     "en": "Errors"},
    "notes":            {"ja": "備考",          "zh": "备注",       "en": "Notes"},
    "run_id":           {"ja": "実行ID",        "zh": "运行 ID",   "en": "Run ID"},
    "module":           {"ja": "モジュール",    "zh": "模块",       "en": "Module"},
    "payload":          {"ja": "ペイロード",    "zh": "参数",       "en": "Payload"},
    "summary":          {"ja": "結果",          "zh": "结果",       "en": "Summary"},

    # ── 改廃监控 ──
    "signal_type":      {"ja": "シグナル種別",  "zh": "信号类型",   "en": "Signal Type"},
    "detected_at":      {"ja": "検出日時",      "zh": "检测时间",   "en": "Detected At"},
    "acknowledged_by":  {"ja": "確認者",        "zh": "确认人",     "en": "Acked By"},
    "acknowledged_at":  {"ja": "確認日時",      "zh": "确认时间",   "en": "Acked At"},
    "action":           {"ja": "対応",          "zh": "处置",       "en": "Action"},
}


def label(col: str, lang: str | None = None) -> str:
    """单列翻译。未登记的 col 直接返回原名。"""
    if lang is None:
        try:
            lang = get_lang()
        except Exception:
            lang = "ja"
    if lang not in ("ja", "zh", "en"):
        lang = "ja"
    return COLUMN_LABELS.get(col, {}).get(lang, col)


def localize_df(df: Any, lang: str | None = None):
    """DataFrame column rename 到当前语言（保持原 df 不变）。

    用法：st.dataframe(localize_df(df))

    None 安全：传 None / 空 df 直接返回。
    """
    if df is None:
        return df
    try:
        if not hasattr(df, "rename"):
            return df
        if lang is None:
            try:
                lang = get_lang()
            except Exception:
                lang = "ja"
        if lang not in ("ja", "zh", "en"):
            lang = "ja"
        rename_map = {
            c: COLUMN_LABELS[c][lang]
            for c in df.columns
            if c in COLUMN_LABELS and lang in COLUMN_LABELS[c]
        }
        return df.rename(columns=rename_map) if rename_map else df
    except Exception:
        return df


def localize_records(rows: list[dict], lang: str | None = None) -> list[dict]:
    """list[dict] 列名翻译（pandas 不可用时）"""
    if not rows:
        return rows
    if lang is None:
        try:
            lang = get_lang()
        except Exception:
            lang = "ja"
    if lang not in ("ja", "zh", "en"):
        lang = "ja"
    out = []
    for r in rows:
        out.append({
            COLUMN_LABELS.get(k, {}).get(lang, k): v
            for k, v in r.items()
        })
    return out
