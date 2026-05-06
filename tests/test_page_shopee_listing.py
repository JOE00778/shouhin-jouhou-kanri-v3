"""测试 page 21（T-309 · Shopee 上架）.

覆盖 4 个核心用例（按任务规范）：
  ① import page module 不崩（streamlit 在最小 mock 下也能导入）
  ② parse_simple_spu 集成（用 sample CSV 解析出预期 SPU 数）
  ③ Mock 模式下完整 pipeline 跑通（mock generate_listing + image_finder）
  ④ 缺 A/B 列报错

注意：page 21 顶部有 streamlit 的 `st.set_page_config` / `require_admin`，
直接 import 会触发 streamlit 启动副作用；这里采用「import page 内的纯函数」+
「load module via importlib + 拦截 streamlit」两种策略。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = REPO_ROOT / "pages" / "21_🚀_Shopee上架.py"
SAMPLE_CSV = REPO_ROOT / "assets" / "shopee_simple_spu_sample.csv"

# shopee-listing 在平行目录
SHOPEE_LISTING_ROOT = REPO_ROOT.parent / "shopee-listing"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ensure_shopee_listing_path() -> None:
    for p in (SHOPEE_LISTING_ROOT, SHOPEE_LISTING_ROOT / "scripts"):
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)


def _make_streamlit_stub() -> ModuleType:
    """生成一个不会真启 streamlit、但能让 page 顶部代码跑通的 stub."""
    stub = ModuleType("streamlit")
    sess: dict[str, Any] = {}

    class _SessionState(dict):
        def __getattr__(self, k):  # noqa: D401
            return self.get(k)

        def __setattr__(self, k, v):
            self[k] = v

    sess_state = _SessionState()
    stub.session_state = sess_state

    def _noop(*_a, **_k):
        return None

    class _Ctx:
        """Context manager + 任意属性都返回 noop / 也是 _Ctx.

        特殊属性（selectbox/radio 等）返回选项中的第一项，
        让 lang_selector / radio 这类返回字符串的控件正常 work.
        """

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __call__(self, *a, **k):
            return _Ctx()

        def selectbox(self, _label, options, *_a, **_k):
            opts = list(options)
            return opts[0] if opts else ""

        def radio(self, _label, options, *_a, **_k):
            opts = list(options)
            return opts[0] if opts else ""

        def checkbox(self, _label, *_a, value=False, **_k):
            return value

        def button(self, *a, **k):
            return False

        def text_input(self, *a, **k):
            return ""

        def page_link(self, *a, **k):
            return None

        def __getattr__(self, name):
            # 兜底：任何其它属性 → 返回 callable / ctx
            def _maybe(*a, **k):
                return _Ctx()

            return _maybe

    def _ctx(*_a, **_k):
        return _Ctx()

    # 大部分 streamlit API 直接桩成 noop / context manager
    for name in (
        "set_page_config",
        "title",
        "caption",
        "subheader",
        "header",
        "info",
        "success",
        "warning",
        "error",
        "code",
        "write",
        "markdown",
        "divider",
        "metric",
        "dataframe",
        "stop",
        "rerun",
        "page_link",
        "image",
    ):
        setattr(stub, name, _noop)

    for name in ("expander", "form", "container", "tabs"):
        setattr(stub, name, _ctx)

    # sidebar 是一个有 .selectbox / .button / .divider / .expander 等的对象
    stub.sidebar = _Ctx()

    # 控件返回合理默认值
    stub.checkbox = lambda *a, **k: k.get("value", False)
    stub.radio = lambda *a, **k: (a[1][0] if len(a) > 1 else "")
    stub.selectbox = lambda *a, **k: (a[1][0] if len(a) > 1 else "")
    stub.text_input = lambda *a, **k: ""
    stub.file_uploader = lambda *a, **k: None
    stub.button = lambda *a, **k: False
    stub.download_button = lambda *a, **k: False
    stub.progress = lambda *a, **k: MagicMock()
    stub.empty = lambda *a, **k: MagicMock()
    stub.form_submit_button = lambda *a, **k: False

    # secrets 映射
    class _Secrets:
        def get(self, k, default=None):
            return os.environ.get(k, default)

    stub.secrets = _Secrets()

    # columns 返回 N 个 ctx（也支持 .metric() 等 attribute）
    def _columns(n, *a, **k):
        if isinstance(n, int):
            return [_Ctx() for _ in range(n)]
        return [_Ctx() for _ in range(len(n))]

    stub.columns = _columns

    return stub


def _import_page_module() -> ModuleType:
    """Load page 21 as a module called `t309_page` for testing."""
    _ensure_shopee_listing_path()
    spec = importlib.util.spec_from_file_location("t309_page", PAGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t309_page"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _streamlit_stub(monkeypatch):
    """所有测试自动注入 streamlit stub."""
    stub = _make_streamlit_stub()
    monkeypatch.setitem(sys.modules, "streamlit", stub)
    # 同时桩 shared.auth / shared.i18n 里调用的 streamlit（已在 sys.modules，但 import 时已绑定）
    # 重新 import 以生效
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("shared.") or mod_name in ("t309_page",):
            sys.modules.pop(mod_name, None)
    yield stub


@pytest.fixture
def env_no_secrets(monkeypatch):
    """清掉 ANTHROPIC_API_KEY / RAKUTEN_APP_ID（mock 模式必须能 work)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RAKUTEN_APP_ID", raising=False)


# --------------------------------------------------------------------------- #
# ① Import page module 不崩
# --------------------------------------------------------------------------- #
def test_import_page_module(env_no_secrets):
    """page 21 顶部代码（含 set_page_config / require_admin / lang_selector）能跑通."""
    mod = _import_page_module()
    # page 模块必须暴露 run_pipeline 入口
    assert hasattr(mod, "run_pipeline")
    assert callable(mod.run_pipeline)


# --------------------------------------------------------------------------- #
# ② parse_simple_spu 集成
# --------------------------------------------------------------------------- #
def test_parse_sample_csv(env_no_secrets):
    """用 sample CSV 跑 parse_simple_spu_csv，得到 5 SPU × 12 SKU."""
    _ensure_shopee_listing_path()
    from parse_simple_spu import parse_simple_spu_csv

    assert SAMPLE_CSV.exists(), f"sample csv 缺失: {SAMPLE_CSV}"
    spus = parse_simple_spu_csv(SAMPLE_CSV)
    assert len(spus) == 5
    total_variants = sum(len(s.variants) for s in spus)
    assert total_variants == 12


# --------------------------------------------------------------------------- #
# ③ Mock 模式下完整 pipeline 跑通
# --------------------------------------------------------------------------- #
def test_run_pipeline_mock_end_to_end(env_no_secrets, tmp_path):
    """跑 run_pipeline(plan='B', mock_mode=True, skip_images=True) → 产出 xlsx."""
    mod = _import_page_module()

    progress_calls: list[tuple[float, str]] = []
    log_lines: list[str] = []

    out_dir = tmp_path / "out"
    result = mod.run_pipeline(
        SAMPLE_CSV,
        plan="B",
        mock_mode=True,
        skip_images=True,
        progress_cb=lambda pct, msg: progress_calls.append((pct, msg)),
        log_cb=lambda m: log_lines.append(m),
        out_dir=out_dir,
    )

    assert result["plan"] == "B"
    assert result["output_path"].exists()
    assert result["output_path"].suffix == ".xlsx"
    # 12 SKU → 12 行
    assert result["n_rows"] == 12
    # mock + skip_images 模式下所有主图都缺
    assert len(result["missing_image_jans"]) == 12
    # 至少打过若干阶段进度
    assert any("解析" in m or "Parsing" in m or "parse" in m.lower() for m in log_lines + [c[1] for c in progress_calls])
    # 进度起码到过 0.9 / 1.0
    assert any(p >= 0.85 for p, _ in progress_calls)


def test_run_pipeline_mock_plan_a(env_no_secrets, tmp_path):
    """方案 A 也能跑（产出 zip）."""
    mod = _import_page_module()

    out_dir = tmp_path / "out"
    result = mod.run_pipeline(
        SAMPLE_CSV,
        plan="A",
        mock_mode=True,
        skip_images=True,
        progress_cb=lambda *a, **k: None,
        log_cb=lambda *a, **k: None,
        out_dir=out_dir,
    )
    assert result["plan"] == "A"
    assert result["output_path"].exists()
    assert result["output_path"].suffix == ".zip"
    assert result["n_rows"] == 12


# --------------------------------------------------------------------------- #
# ④ 缺 A/B 列报错
# --------------------------------------------------------------------------- #
def test_validate_missing_columns(env_no_secrets):
    """单列 DataFrame → _validate_and_dedup 抛 ValueError."""
    mod = _import_page_module()

    df_one_col = pd.DataFrame({"only_one": ["a", "b"]})
    with pytest.raises(ValueError):
        mod._validate_and_dedup(df_one_col)


def test_validate_dedup_warns(env_no_secrets):
    """SKU 重复 → 自动去重 + warning."""
    mod = _import_page_module()

    df = pd.DataFrame(
        {
            "SPU": ["SPU1", "SPU1", "SPU2"],
            "SKU": ["1234567890123", "1234567890123", "9999999999999"],
        }
    )
    clean, warnings = mod._validate_and_dedup(df)
    assert len(clean) == 2
    assert len(warnings) == 1
    assert "1234567890123" in warnings[0]


# --------------------------------------------------------------------------- #
# extra: 缺 A 列（shape=(N, 0) 这种用 Series 直接 read_csv 出来不会发生；
# 但用户可能上传只有一列的 CSV，覆盖在 test_validate_missing_columns）
# --------------------------------------------------------------------------- #
