"""公司对日元固定汇率表（人工维护，非市场实时）.

用途:
- Shopee 财务等模块把外币结算金额（PHP/TWD/MYR 等）换算成 JPY 视角
- 跨市场对账时统一基准货币

汇率口径: 1 单位外币 = X 日元

注意:
- 这是公司内部固定汇率，不是市场汇率
- 修改请联系财务确认
- PHP = 2.4 (Boss 2026-05 确认)
"""
from __future__ import annotations

# 1 单位外币 = X 日元
FX_TO_JPY: dict[str, float] = {
    "JPY": 1.0,
    "PHP": 2.4,    # 菲律宾比索（Boss 2026-05 确认）
    "TWD": 4.7,    # 台币（占位待确认）
    "MYR": 33.0,   # 马来西亚林吉特（占位待确认）
    "SGD": 110.0,  # 新加坡元（占位待确认）
    "IDR": 0.0095, # 印尼盾（占位待确认）
    "THB": 4.2,    # 泰铢（占位待确认）
    "VND": 0.006,  # 越南盾（占位待确认）
    "USD": 150.0,  # 美元（占位待确认）
}

# 货币显示符号
FX_SYMBOLS: dict[str, str] = {
    "JPY": "¥", "PHP": "₱", "TWD": "NT$", "MYR": "RM", "SGD": "S$",
    "IDR": "Rp", "THB": "฿", "VND": "₫", "USD": "$",
}


def to_jpy(amount: float, currency: str) -> float:
    """外币金额 → JPY."""
    rate = FX_TO_JPY.get(currency.upper(), 0.0)
    return amount * rate


def fmt(amount: float, currency: str) -> str:
    """格式化: ₱ 1,234 / ¥ 56,789"""
    sym = FX_SYMBOLS.get(currency.upper(), currency.upper() + " ")
    return f"{sym}{amount:,.0f}"
