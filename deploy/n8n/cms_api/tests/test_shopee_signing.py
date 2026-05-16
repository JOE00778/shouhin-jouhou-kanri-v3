"""Shopee 签名 + OAuth callback HTML 渲染单元测试.

不依赖 FastAPI / DB / 网络 — 提取 cms_api/app.py 里的纯函数 source 后 exec 跑。
作用：拦截「v2.7→v2.10 那种 stale 全局变量引用」类型的 bug 进生产。

跑法：
    cd deploy/n8n/cms_api
    python -m pytest tests/ -v

或者直接 python：
    python tests/test_shopee_signing.py
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import os
import unittest
from pathlib import Path

APP_PY = Path(__file__).parent.parent / "app.py"


def _load_fn(fn_name: str, extra_globals: dict | None = None):
    """从 app.py 提取一个 top-level 函数定义并 exec 进新 namespace.

    避免 import app.py 触发 FastAPI / DB 初始化。
    """
    src = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            fn_src = ast.get_source_segment(src, node)
            ns: dict = {"hmac": hmac, "hashlib": hashlib, "os": os}
            if extra_globals:
                ns.update(extra_globals)
            exec(fn_src, ns)
            return ns[fn_name]
    raise LookupError(f"function {fn_name!r} not found in app.py")


class TestShopeeSign(unittest.TestCase):
    """_shopee_sign 算法跟 Shopee 官方 SDK (pyshopee2) 一致性。"""

    def setUp(self):
        # 模拟 cms-api 容器内 partner_key（含 shpk）+ partner_id
        self.partner_key = "shpk7344557669695a63534a456843424f6a486e686d4f58746a71676943646c"
        self.partner_id = "1232606"
        self._sign = _load_fn(
            "_shopee_sign",
            extra_globals={"SHOPEE_PARTNER_KEY": self.partner_key},
        )

    def test_sign_matches_pyshopee2(self):
        """cms-api 签名应跟 Shopee 官方 SDK 公式完全一致."""
        base = f"{self.partner_id}/api/v2/shop/auth_partner1778942777"
        actual = self._sign(base)
        expected = hmac.new(
            self.partner_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(actual, expected)

    def test_sign_does_not_strip_shpk(self):
        """v2.10 起 partner_key 应完整使用，不能 strip 'shpk' 前缀."""
        base = f"{self.partner_id}/api/v2/shop/auth_partner1778942777"
        actual = self._sign(base)
        # 反例：strip 后的 60 char key
        stripped_key = self.partner_key.removeprefix("shpk")
        wrong = hmac.new(
            stripped_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertNotEqual(actual, wrong, "v2.7 strip bug 回归了！")

    def test_sign_does_not_hex_decode(self):
        """v2.10 起不能用 bytes.fromhex(key) 当 HMAC key (v2.8 hex mode 是错的方向)."""
        # partner_key 含 'shpk' 不是合法 hex；strip 后 60 char 才是合法 hex
        stripped_key = self.partner_key.removeprefix("shpk")
        base = f"{self.partner_id}/api/v2/shop/auth_partner1778942777"
        actual = self._sign(base)
        wrong_hex = hmac.new(
            bytes.fromhex(stripped_key),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertNotEqual(actual, wrong_hex, "v2.8 hex mode bug 回归了！")


class TestOAuthResultHtml(unittest.TestCase):
    """v2.11 _oauth_result_html 友好结果页渲染."""

    def setUp(self):
        self._html = _load_fn("_oauth_result_html")

    def test_success_page_contains_title_and_rows(self):
        out = self._html(
            "✅ PH 授权成功",
            "#2e7d32",
            [("market", "PH"), ("shop_id", "227466553")],
        )
        self.assertIn("PH 授权成功", out)
        self.assertIn("market", out)
        self.assertIn("227466553", out)
        self.assertIn("#2e7d32", out)

    def test_xss_escaped(self):
        """partner_key 错误信息含 < > & 不能破 HTML."""
        out = self._html(
            "❌ 错",
            "#c62828",
            [("error", "<script>alert(1)</script>")],
        )
        self.assertNotIn("<script>alert", out)
        self.assertIn("&lt;script&gt;", out)

    def test_chinese_and_ampersand_escaped(self):
        out = self._html(
            "✅ TW 授权成功",
            "#2e7d32",
            [("说明", "已授权 7 国 & 完成 ✅")],
        )
        self.assertIn("TW (台灣)" if "台灣" in out else "TW 授权成功", out)
        self.assertIn("&amp;", out)

    def test_empty_lines(self):
        out = self._html("✅ 空", "#2e7d32", [])
        self.assertIn("<table>", out)
        self.assertIn("✅ 空", out)

    def test_int_value_auto_str(self):
        """int / None 等非 str 值应自动 str()."""
        out = self._html("✅", "#2e7d32", [("expire_in", 14258)])
        self.assertIn("14258", out)


class TestNoStaleReferences(unittest.TestCase):
    """v2.7→v2.10 那种 stale 全局变量引用回归测试.

    扫 app.py 看有没有引用已删除的全局变量（_PK_MODE / _RAW_PK / SHOPEE_PARTNER_KEY_MODE）.
    """

    def test_no_pk_mode_references(self):
        src = APP_PY.read_text(encoding="utf-8")
        # v2.10 起 _PK_MODE / _RAW_PK 已删除（直接用 SHOPEE_PARTNER_KEY env 即可）
        self.assertNotIn("_PK_MODE", src, "_PK_MODE 已废弃，发现引用回归（v2.7→v2.10 stale ref）")
        self.assertNotIn("_RAW_PK", src, "_RAW_PK 已废弃，发现引用回归")
        # 但允许在注释里出现（注释经过这次扫描会失败，必要时改为 AST-level 检查）

    def test_no_v1_old_host(self):
        """app.py 默认 SHOPEE_API_BASE 不能回退到 v1 老域名."""
        src = APP_PY.read_text(encoding="utf-8")
        # 检查 os.environ.get("SHOPEE_API_BASE", "...") 那一行的默认值
        # 注释里提到老域名是 OK 的（警告作用），默认值必须 openplatform.*
        for line in src.splitlines():
            if "os.environ.get" in line and "SHOPEE_API_BASE" in line:
                self.assertIn(
                    "openplatform", line,
                    f"SHOPEE_API_BASE 默认值必须用 v2 openplatform.* 域名，发现: {line}",
                )
                self.assertNotIn(
                    "partner.shopeemobile.com", line,
                    f"SHOPEE_API_BASE 默认值不能用 v1 老域名，发现: {line}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
