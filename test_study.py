from datetime import date
import unittest

from study import deal,historical,realized_irr,sources_uses


class LBOTests(unittest.TestCase):
    def setUp(self):
        self.fin=historical()

    def test_sources_uses_and_no_expansion(self):
        e=sources_uses(self.fin)
        self.assertAlmostEqual(e["total_sources"],e["total_uses"])
        base=deal(self.fin,.06,.08,10)
        self.assertEqual(base["attributions"]["multiple"],0)
        self.assertEqual(base["attributions"]["interaction"],0)
        self.assertAlmostEqual(sum(base["attributions"].values()),base["net_sponsor"])

    def test_zero_debt(self):
        d=deal(self.fin,.03,.08,10,leverage=0)
        self.assertTrue(all(r["interest"]==r["debt"]==r["amortization_due"]==0 for r in d["rows"]))

    def test_early_cure_and_liquidity_reconciliation(self):
        d=deal(self.fin,-.05,.115,8.25)
        first=d["rows"][0]
        self.assertGreater(first["cure"],0)
        self.assertAlmostEqual(first["sponsor_flow"],-first["cure"])
        for r in d["rows"]:
            self.assertAlmostEqual(r["opening_cash"]+r["cfads"]-r["interest"]+r["cure"]
                 +r["unpaid_interest"]-r["amortization_paid"]-r["sweep"],r["cash"])
            self.assertAlmostEqual(r["opening_debt"]+r["unpaid_interest"]-r["amortization_paid"]-r["sweep"],r["debt"])
        self.assertAlmostEqual(sum(d["attributions"].values()),d["net_sponsor"])

    def test_irr_invalid_and_multiple_signs(self):
        ds=[date(2020+i,12,31) for i in range(3)]
        self.assertEqual(realized_irr(zip(ds,[-10,0,0])),(None,"no sign change"))
        self.assertEqual(realized_irr(zip(ds,[-10,44,-40])),(None,"ambiguous sign pattern"))
        self.assertAlmostEqual(realized_irr(zip(ds,[-100,110,0]))[0],.10,places=10)
