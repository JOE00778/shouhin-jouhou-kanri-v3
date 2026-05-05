"""模块 #1 成本同步 · 业务规则全量单测。

覆盖：
- ceil_yen 边界
- classify_severity 三档边界
- 5 类 SKIP 优先级（先廃番 → 无主档 → avg 缺 → std 缺 → 阈值不达）
- UPDATE happy path
- diff/diff_pct 计算正确
"""
from __future__ import annotations

import math

import pytest

from modules.cost_sync.rules import (
    SEVERITY_RED_PCT,
    SEVERITY_YELLOW_PCT,
    THRESHOLD_PCT,
    THRESHOLD_YEN,
    ceil_yen,
    classify_severity,
    decide_action,
)


# ============================================================
# ceil_yen
# ============================================================
class TestCeilYen:
    def test_integer_in_integer_out(self):
        assert ceil_yen(286.0) == 286

    def test_fractional_rounds_up(self):
        assert ceil_yen(286.01) == 287

    def test_almost_one_rounds_up(self):
        assert ceil_yen(286.99) == 287

    def test_zero(self):
        assert ceil_yen(0.0) == 0

    def test_negative(self):
        # 数学上向 +∞ 取整：-1.5 → -1
        assert ceil_yen(-1.5) == -1


# ============================================================
# classify_severity
# ============================================================
class TestSeverity:
    def test_below_yellow_is_normal(self):
        assert classify_severity(0.0999) == "NORMAL"
        assert classify_severity(-0.0999) == "NORMAL"
        assert classify_severity(0.0) == "NORMAL"

    def test_at_yellow_threshold(self):
        assert classify_severity(0.10) == "YELLOW"
        assert classify_severity(-0.10) == "YELLOW"

    def test_in_yellow_zone(self):
        assert classify_severity(0.15) == "YELLOW"
        assert classify_severity(0.1999) == "YELLOW"

    def test_at_red_threshold(self):
        assert classify_severity(0.20) == "RED"
        assert classify_severity(-0.20) == "RED"

    def test_above_red(self):
        assert classify_severity(0.50) == "RED"
        assert classify_severity(-1.0) == "RED"


# ============================================================
# decide_action — happy path UPDATE
# ============================================================
def _master(handling="取扱中", display_name="Foo Item"):
    return {
        "item_code": "4901111310490",
        "handling_status": handling,
        "display_name": display_name,
        "maker": "AGF",
        "rank": "NEW",
    }


def _row(avg=300.0, std=286.0, internal_id="1234", item_code="4901111310490", **extra):
    return {
        "internal_id": internal_id,
        "item_code": item_code,
        "avg_cost": avg,
        "std_cost_old": std,
        **extra,
    }


class TestUpdate:
    def test_update_happy_path(self):
        r = decide_action(_row(avg=300.0, std=286.0), _master())
        assert r["action"] == "UPDATE"
        assert r["std_cost_new"] == 300
        assert r["diff"] == 14
        assert r["diff_pct"] == pytest.approx(14 / 286)
        assert r["severity"] == "NORMAL"
        assert r["skip_reason"] is None

    def test_update_with_fractional_avg_ceils(self):
        r = decide_action(_row(avg=286.01, std=200.0), _master())
        assert r["action"] == "UPDATE"
        assert r["std_cost_new"] == 287

    def test_update_negative_diff(self):
        # avg 比 std 低，触发更新
        r = decide_action(_row(avg=200.0, std=300.0), _master())
        assert r["action"] == "UPDATE"
        assert r["std_cost_new"] == 200
        assert r["diff"] == -100

    def test_red_severity_when_huge_swing(self):
        r = decide_action(_row(avg=400.0, std=200.0), _master())
        assert r["action"] == "UPDATE"
        assert r["severity"] == "RED"

    def test_yellow_severity(self):
        # +12% diff_pct
        r = decide_action(_row(avg=224.0, std=200.0), _master())
        assert r["action"] == "UPDATE"
        assert r["severity"] == "YELLOW"


# ============================================================
# SKIP 优先级（spec § 4.3 顺序：INACTIVE → NO_MASTER → AVG_ZERO → STD_ZERO → BELOW_THRESHOLD）
# ============================================================
class TestSkipPriority:
    def test_inactive_wins_over_avg_zero(self):
        """同时满足 INACTIVE 和 AVG_ZERO，记录 INACTIVE。"""
        r = decide_action(_row(avg=0), _master(handling="廃番"))
        assert r["action"] == "SKIP_INACTIVE"

    def test_inactive_wins_over_no_master(self):
        """row 自带 inactive_flag=T，即使 master 缺失也优先 INACTIVE。"""
        r = decide_action(_row(inactive_flag="T"), master=None)
        assert r["action"] == "SKIP_INACTIVE"

    def test_no_master_wins_over_avg_zero(self):
        r = decide_action(_row(avg=0), master=None)
        assert r["action"] == "SKIP_NO_MASTER"

    def test_avg_zero_wins_over_std_zero(self):
        r = decide_action(_row(avg=0, std=0), _master())
        assert r["action"] == "SKIP_AVG_ZERO"

    def test_std_zero_wins_over_below_threshold(self):
        r = decide_action(_row(avg=100, std=0), _master())
        assert r["action"] == "SKIP_STD_ZERO"


class TestSkipInactive:
    def test_master_handling_廃番(self):
        r = decide_action(_row(), _master(handling="廃番"))
        assert r["action"] == "SKIP_INACTIVE"
        assert "廃番" in r["skip_reason"]

    def test_row_inactive_flag_T(self):
        r = decide_action(_row(inactive_flag="T"), _master(handling="取扱中"))
        assert r["action"] == "SKIP_INACTIVE"

    def test_row_inactive_flag_true_lowercase(self):
        r = decide_action(_row(inactive_flag="true"), _master(handling="取扱中"))
        assert r["action"] == "SKIP_INACTIVE"

    def test_row_inactive_flag_F_does_not_skip(self):
        r = decide_action(_row(inactive_flag="F"), _master(handling="取扱中"))
        assert r["action"] == "UPDATE"

    def test_row_inactive_flag_empty_string_does_not_skip(self):
        r = decide_action(_row(inactive_flag=""), _master(handling="取扱中"))
        assert r["action"] == "UPDATE"


class TestSkipNoMaster:
    def test_no_master_skips(self):
        r = decide_action(_row(), master=None)
        assert r["action"] == "SKIP_NO_MASTER"
        assert "item_master" in r["skip_reason"]


class TestSkipAvgZero:
    def test_avg_none(self):
        r = decide_action(_row(avg=None), _master())
        assert r["action"] == "SKIP_AVG_ZERO"

    def test_avg_zero(self):
        r = decide_action(_row(avg=0), _master())
        assert r["action"] == "SKIP_AVG_ZERO"

    def test_avg_empty_string(self):
        r = decide_action(_row(avg=""), _master())
        assert r["action"] == "SKIP_AVG_ZERO"

    def test_avg_non_numeric_string(self):
        r = decide_action(_row(avg="abc"), _master())
        assert r["action"] == "SKIP_AVG_ZERO"

    def test_avg_negative_treated_as_missing(self):
        # 负成本毫无业务意义，按缺失处理
        r = decide_action(_row(avg=-1), _master())
        assert r["action"] == "SKIP_AVG_ZERO"


class TestSkipStdZero:
    def test_std_none(self):
        r = decide_action(_row(std=None), _master())
        assert r["action"] == "SKIP_STD_ZERO"

    def test_std_zero(self):
        r = decide_action(_row(std=0), _master())
        assert r["action"] == "SKIP_STD_ZERO"


# ============================================================
# 阈值边界（最容易出 bug 的地方）
# ============================================================
class TestThresholdBoundary:
    def test_diff_just_below_3yen_and_below_2pct_skips(self):
        # diff = 2.99, std=200 → diff_pct ~1.5% < 2%
        # 但 avg=202.99 → ceil=203 → diff=3.0 (整数化后)
        # 用 std=1000 让阈值更清晰：avg=1002.99 → ceil=1003 → diff=3.0 也是正好等于
        # 用 std=10000：avg=10002.99 → ceil=10003 → diff=3.0
        # 真正"不到 3 yen": avg=10002.0 → ceil=10002 → diff=2 < 3，pct=0.02% < 2%
        r = decide_action(_row(avg=10002.0, std=10000.0), _master())
        assert r["action"] == "SKIP_BELOW_THRESHOLD"
        assert r["diff"] == 2
        assert r["std_cost_new"] == 10002  # 即使 SKIP，新值也算出来给报表用

    def test_diff_exactly_3yen_triggers_update(self):
        r = decide_action(_row(avg=10003.0, std=10000.0), _master())
        assert r["action"] == "UPDATE"
        assert r["diff"] == 3

    def test_pct_below_2pct_with_small_diff_skips(self):
        # std=1000, avg=1019 → ceil=1019, diff=19, pct=1.9% — 但 |diff|=19 > 3 → 触发
        # 改：std=1000, avg=1002 → ceil=1002, diff=2, pct=0.2% — 都不到，跳过
        r = decide_action(_row(avg=1002.0, std=1000.0), _master())
        assert r["action"] == "SKIP_BELOW_THRESHOLD"

    def test_pct_exactly_2pct_triggers_update(self):
        # std=100, avg=102 → diff=2 < 3 yen，但 pct=2% 正好达标 → UPDATE（OR 条件）
        r = decide_action(_row(avg=102.0, std=100.0), _master())
        assert r["action"] == "UPDATE"
        assert r["diff"] == 2
        assert r["diff_pct"] == pytest.approx(0.02)

    def test_small_value_2yen_diff_3pct_triggers_update(self):
        # 小金额 SKU：std=50, avg=52 → diff=2, pct=4% → 触发（pct 满足）
        r = decide_action(_row(avg=52.0, std=50.0), _master())
        assert r["action"] == "UPDATE"

    def test_neither_threshold_met_skips(self):
        r = decide_action(_row(avg=300.5, std=300.0), _master())
        # diff=ceil(300.5)-300=1, pct=0.33% — 都不到
        assert r["action"] == "SKIP_BELOW_THRESHOLD"


# ============================================================
# 数据完整性
# ============================================================
class TestDataPreservation:
    def test_internal_id_preserved(self):
        r = decide_action(_row(internal_id="1234"), _master())
        assert r["internal_id"] == "1234"

    def test_item_code_preserved(self):
        r = decide_action(_row(item_code="4901111310490"), _master())
        assert r["item_code"] == "4901111310490"

    def test_display_name_from_row_takes_priority(self):
        r = decide_action(
            _row(display_name="From Row"),
            _master(display_name="From Master"),
        )
        assert r["display_name"] == "From Row"

    def test_display_name_falls_back_to_master(self):
        r = decide_action(_row(), _master(display_name="From Master"))
        assert r["display_name"] == "From Master"

    def test_skip_below_threshold_still_computes_diff(self):
        """SKIP_BELOW_THRESHOLD 也保留 diff/severity 给分析报表用。"""
        r = decide_action(_row(avg=1002.0, std=1000.0), _master())
        assert r["action"] == "SKIP_BELOW_THRESHOLD"
        assert r["diff"] == 2
        assert r["diff_pct"] == pytest.approx(0.002)
        assert r["severity"] == "NORMAL"


# ============================================================
# 综合场景（spec § 14 验收标准里的"5 种 SKIP 都能正确分类"）
# ============================================================
def test_all_skip_types_correctly_classified():
    """跑一组覆盖全部 6 种 action 的输入，每种都正确分类。"""
    cases = [
        (_row(avg=300, std=286), _master(),                         "UPDATE"),
        (_row(avg=300, std=286), _master(handling="廃番"),          "SKIP_INACTIVE"),
        (_row(avg=300, std=286), None,                              "SKIP_NO_MASTER"),
        (_row(avg=0,   std=286), _master(),                         "SKIP_AVG_ZERO"),
        (_row(avg=300, std=0),   _master(),                         "SKIP_STD_ZERO"),
        (_row(avg=10002, std=10000), _master(),                     "SKIP_BELOW_THRESHOLD"),
    ]
    for row, master, expected in cases:
        r = decide_action(row, master)
        assert r["action"] == expected, f"Expected {expected}, got {r['action']} for {row}"
