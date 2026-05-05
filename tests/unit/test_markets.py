"""测试市场分类规则。"""
from __future__ import annotations

import pandas as pd

from shared.markets import (
    MARKET_JAPAN,
    MARKET_KOREA,
    MARKET_SEA,
    MARKET_UNKNOWN,
    add_market_column,
    classify_market,
)


class TestClassify:
    def test_shopee_sea(self):
        for s in ["Shopee PH", "Shopee SG", "Shopee TW", "Shopee VN", "Shopee BR",
                  "Shopee Mall PH", "Shopee Cosme SG", "Shopee Kurashi-Mart.PH",
                  "Shopee J-Beauty Hub PH"]:
            assert classify_market(s) == MARKET_SEA, s

    def test_lazada_sea(self):
        for s in ["Lazada PH", "Lazada SG", "Lazada MY"]:
            assert classify_market(s) == MARKET_SEA, s

    def test_coupang_korea(self):
        assert classify_market("Smikiejapan COUPANG") == MARKET_KOREA
        assert classify_market("COUPANG") == MARKET_KOREA
        assert classify_market("coupang") == MARKET_KOREA

    def test_japan_default(self):
        assert classify_market("25:WINGEAR Amazon") == MARKET_JAPAN
        assert classify_market("6:ヤフー　SONIC PLAZA") == MARKET_JAPAN
        assert classify_market("楽天") == MARKET_JAPAN

    def test_empty_or_none(self):
        assert classify_market(None) == MARKET_UNKNOWN
        assert classify_market("") == MARKET_UNKNOWN
        assert classify_market("   ") == MARKET_UNKNOWN


class TestAddMarketColumn:
    def test_adds_column(self):
        df = pd.DataFrame({"store": ["Shopee PH", "COUPANG", "Yahoo"]})
        out = add_market_column(df)
        assert list(out["market"]) == [MARKET_SEA, MARKET_KOREA, MARKET_JAPAN]

    def test_preserves_other_columns(self):
        df = pd.DataFrame({"store": ["Shopee PH"], "qty": [100], "rev": [1000]})
        out = add_market_column(df)
        assert "qty" in out.columns
        assert "rev" in out.columns
        assert out.loc[0, "market"] == MARKET_SEA

    def test_empty_df_does_not_crash(self):
        out = add_market_column(pd.DataFrame())
        assert "market" in out.columns
