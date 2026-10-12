import datetime as dt
import os
import tempfile
import unittest

import yaml

from bot import calendar_sync, landed, sources
from bot.ebay import trimmed_median
from bot.scoring import score_release

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))


def rel(**kw):
    base = {"id": "x", "game": "pokemon", "name": "Test", "set_type": "main",
            "en_release": dt.date(2026, 12, 1), "chase": [], "reprint_risk": "medium"}
    base.update(kw)
    return base


class Scoring(unittest.TestCase):
    def test_hot_special_set_is_buy(self):
        s = score_release(rel(set_type="special", chase=["Charizard", "Umbreon", "Pikachu"],
                              reprint_risk="low", au_rrp={"etb": 90},
                              jp_box_rrp_jpy=5400, jp_box_market_jpy=11000), CFG, preorder_market_aud=160)
        self.assertEqual(s.tier, "BUY")
        self.assertGreater(s.est_profit, 30)

    def test_plain_main_set_is_pass(self):
        s = score_release(rel(chase=["Bidoof"], reprint_risk="high", au_rrp={"booster_box": 250}), CFG,
                          preorder_market_aud=230)
        self.assertEqual(s.tier, "PASS")
        self.assertIn("no margin", s.reasons[0])

    def test_hype_but_no_margin_is_pass(self):
        # great chase, but eBay only +15% over RRP → fees eat it
        s = score_release(rel(set_type="special", chase=["Charizard", "Umbreon", "Pikachu"], reprint_risk="low",
                              au_rrp={"etb": 90}), CFG, preorder_market_aud=104)
        self.assertEqual(s.tier, "PASS")

    def test_thin_margin_is_pass(self):
        s = score_release(rel(set_type="special", chase=["Charizard", "Umbreon"], reprint_risk="low",
                              au_rrp={"etb": 90}), CFG, preorder_market_aud=125)   # ~A$11 profit, 12%
        self.assertEqual(s.tier, "PASS")
        self.assertIn("thin margin", s.reasons[0])

    def test_jp_flop_is_pass(self):
        s = score_release(rel(set_type="special", chase=["Charizard"], jp_box_rrp_jpy=5400, jp_box_market_jpy=5000), CFG)
        self.assertEqual(s.tier, "PASS")

    def test_unknown_is_watch_not_pass(self):
        s = score_release(rel(), CFG)
        self.assertLess(s.confidence, 0.4)
        self.assertEqual(s.tier, "WATCH")
        self.assertEqual(s.reasons[0], "not enough data to call yet")

    def test_no_price_data_holds_at_watch(self):
        s = score_release(rel(set_type="special", chase=["Charizard", "Umbreon", "Pikachu"], reprint_risk="low"), CFG)
        self.assertEqual(s.tier, "WATCH")


class Landed(unittest.TestCase):
    def test_low_value_us_order(self):
        lc = landed.landed_cost({"country": "US", "price": 150, "currency": "USD", "shipping": 0}, CFG, fx=1.5)
        # 225 goods + 45 fwd = 270, +10% GST = 297
        self.assertAlmostEqual(lc["total"], 297.0)
        self.assertEqual(lc["import_fee"], 0)

    def test_high_value_triggers_import_fee(self):
        lc = landed.landed_cost({"country": "JP", "price": 120000, "currency": "JPY", "qty": 6}, CFG, fx=0.01)
        self.assertEqual(lc["import_fee"], CFG["landed_cost"]["import_processing_fee_aud"])

    def test_margin(self):
        m = landed.resale_margin(200, 350, CFG)
        self.assertAlmostEqual(m["net_after_fees"], 350 - 350 * 0.134 - 12)
        self.assertGreater(m["margin_pct"], 40)


class Misc(unittest.TestCase):
    def test_trimmed_median(self):
        self.assertEqual(trimmed_median([1, 100, 101, 102, 1000]), 101)
        self.assertIsNone(trimmed_median([]))

    def test_merge_manual_wins(self):
        manual = [{"id": "pkm-delta-reign", "name": "Mega Evolution — Delta Reign", "code": "ME06"}]
        disc = [{"id": "pkm-me6", "name": "Delta Reign", "code": "ME6"},
                {"id": "pkm-new", "name": "Brand New", "code": "ME7"}]
        out = sources.merge(manual, disc)
        self.assertEqual([r["id"] for r in out], ["pkm-delta-reign", "pkm-new"])

    def test_events_and_ics(self):
        r = rel(preorder_open=dt.date(2026, 11, 1), jp_release=dt.date(2020, 1, 1))
        s = score_release(r, CFG)
        ev = calendar_sync.build_events(r, s)
        self.assertEqual(len(ev), 2)                      # past JP date skipped
        self.assertEqual(ev, calendar_sync.build_events(r, s))  # deterministic ids
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.ics")
            calendar_sync.write_ics(ev, p)
            with open(p) as f:
                txt = f.read()
            self.assertIn("DTSTART;VALUE=DATE:20261201", txt)
            self.assertEqual(txt.count("BEGIN:VEVENT"), 2)


if __name__ == "__main__":
    unittest.main()
