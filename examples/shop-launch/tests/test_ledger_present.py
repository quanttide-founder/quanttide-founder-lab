"""present() 与共享数据层的回归测试。无图形环境可跑：python3 -m unittest discover -s tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ledger as L  # noqa: E402

FULL = {
    "mix": L.DEFAULT_MIX, "ticket": (32, 45), "traffic": (15, 20),
    "staff": 1200.0, "utility_other": 800.0, "other_fixed": 0.0,
    "rent": 3500.0, "food_rate": 0.35, "daily": None, "waste": None,
}


def stall_params(**kw):
    p = {"mix": L.DEFAULT_MIX, "daily": None, "waste": None, "ticket": None,
         "traffic": None, "staff": None, "utility_other": None,
         "other_fixed": 0.0, "rent": None, "food_rate": None}
    p.update(kw)
    return p


class TestFormulaSet(unittest.TestCase):
    """第一层三个数字必须同源导出，不能各自为政。"""

    def test_headline_matches_design(self):
        """设计表：保本 342/天、月利润 2221~8977、倍数 1.40~2.63。"""
        r = L.present("shop", FULL)
        self.assertTrue(r["computable"])
        (lab1, val1, note1), (lab2, val2, note2) = r["layer1"]
        self.assertEqual(lab1, "月利润")
        self.assertEqual(val1, "¥2,221~8,977")
        self.assertIn("1.40~2.63", note1)
        self.assertEqual(lab2, "保本线")
        self.assertEqual(val2, "每天卖 ¥342")

    def test_var_rate(self):
        varr, broth = L.var_rate(0.35, L.DEFAULT_MIX)
        self.assertAlmostEqual(varr, 0.464, places=3)
        self.assertAlmostEqual(broth, 0.079, places=3)

    def test_breakeven_identity(self):
        """保本流水代入后净利为 0。"""
        varr, _ = L.var_rate(0.35, L.DEFAULT_MIX)
        fixed = 5500.0
        be = L.pnl((0, 0), fixed, varr)["be_month"]
        net = be * (1 - varr) - fixed
        self.assertAlmostEqual(net, 0.0, places=6)

    def test_verdict_band(self):
        self.assertTrue(L.verdict((1.4, 2.6)).startswith("✓"))
        self.assertTrue(L.verdict((0.5, 0.8)).startswith("✗"))
        self.assertTrue(L.verdict((0.9, 1.2)).startswith("⚠"))

    def test_income_none_guard(self):
        self.assertIsNone(L.income(None, (15, 20)))
        self.assertIsNone(L.income((32, 45), None))


class TestCompleteness(unittest.TestCase):
    """完整度是可信度的唯一量化指标，缺失项不得计入已填。"""

    def test_missing_is_80(self):
        """线框场景：填 4 项、食材成本率留空 → 80%，且不可计算。"""
        p = dict(FULL, food_rate=None)
        r = L.present("shop", p)
        self.assertAlmostEqual(r["completeness"], 0.8)
        self.assertEqual(r["missing"], ["food_rate"])
        self.assertFalse(r["computable"])

    def test_empty_is_zero_not_negative(self):
        r = L.present("shop", dict(FULL, ticket=None, traffic=None, staff=None,
                                   utility_other=None, food_rate=None))
        self.assertGreaterEqual(r["completeness"], 0.0)

    def test_stall_ignores_food_rate(self):
        """摆摊的分母不含 food_rate，不能被它污染。"""
        r = L.present("stall", stall_params())
        self.assertNotIn("food_rate", r["missing"])
        self.assertGreaterEqual(r["completeness"], 0.0)

    def test_stall_half(self):
        r = L.present("stall", stall_params(daily=149))
        self.assertAlmostEqual(r["completeness"], 0.5)

    def test_full_is_one(self):
        r = L.present("shop", FULL)
        self.assertAlmostEqual(r["completeness"], 1.0)
        self.assertEqual(r["missing"], [])


class TestRefusePolicy(unittest.TestCase):
    """唯一策略：L1 缺失不代填，拒绝计算。"""

    def test_cli_refuses(self):
        _, missing = L.shop(L.DEFAULT_MIX, 45000, 3500, None, None, None, None)
        self.assertEqual(len(missing), 4)

    def test_missing_l1_not_computable(self):
        r = L.present("shop", dict(FULL, staff=None))
        self.assertFalse(r["computable"])
        self.assertIn("staff", r["missing"])

    def test_stall_always_computable(self):
        """摆摊公式恒可算（daily 缺失以达标线代入），waste 只影响成本侧。"""
        r = L.present("stall", stall_params())
        self.assertTrue(r["computable"])
        self.assertIn("waste", r["missing"])


class TestSharedData(unittest.TestCase):
    """report 文本层与共享数据常量，不允许分叉。"""

    def test_gaps_shape(self):
        self.assertEqual(len(L.GAPS), 7)
        for name, source, why in L.GAPS:
            self.assertTrue(name and source and why)

    def test_print_gaps_uses_constant(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            L.print_gaps(L.DEFAULT_MIX)
        out = buf.getvalue()
        for name, source, why in L.GAPS:
            self.assertIn(name, out)
            self.assertIn(source, out)

    def test_param_labels_cover_completeness_keys(self):
        for keys in L.COMPLETENESS.values():
            for k in keys:
                self.assertIn(k, L.PARAM_LABEL)


class TestLayer2(unittest.TestCase):
    def test_cost_rows_have_locked_rent(self):
        r = L.present("shop", FULL)
        cost = {row[0]: row for row in r["layer2"]["cost"]}
        self.assertEqual(cost["├ 房租/摊位"][1], "¥3,500")
        self.assertEqual(cost["├ 房租/摊位"][2], "锁")

    def test_income_derives_from_ticket_traffic(self):
        r = L.present("shop", FULL)
        inc = {row[0]: row for row in r["layer2"]["income"]}
        self.assertEqual(inc["日均流水"][1], "¥480~900")

    def test_stall_verdict_gap(self):
        r = L.present("stall", stall_params(daily=149))
        label, value, note = r["layer1"][0]
        self.assertIn("差 1 签/天", note)


if __name__ == "__main__":
    unittest.main()
