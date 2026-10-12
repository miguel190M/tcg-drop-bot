"""Flip and hold scoring for a release.

Every signal is 0-1. Missing signals are skipped and lower the confidence
rather than being guessed. Weights are deliberately simple so the feedback
loop (outcomes.json) can retune them later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FLIP_WEIGHTS = {"preorder_premium": 0.35, "jp_premium": 0.25, "chase": 0.2, "set_type": 0.15, "reprint": 0.05}
HOLD_WEIGHTS = {"set_type": 0.3, "chase": 0.3, "reprint": 0.2, "jp_premium": 0.1, "game": 0.1}


@dataclass
class Score:
    flip: int
    hold: int
    combined: int
    tier: str
    confidence: float          # share of flip weight backed by real data
    reasons: list[str] = field(default_factory=list)
    est_profit: float | None = None   # per unit, AUD, buying at RRP and selling on eBay AU


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def chase_signal(release: dict, cfg: dict) -> float | None:
    chase = [c.lower() for c in release.get("chase") or []]
    if not chase:
        return None
    tiers = cfg["chase_tiers"].get(release["game"], {})
    t1 = {c.lower() for c in tiers.get("tier1", [])}
    t2 = {c.lower() for c in tiers.get("tier2", [])}
    pts = sum(1.0 if c in t1 else 0.4 if c in t2 else 0.1 for c in chase)
    return _clamp(pts / 2.5)   # ~2-3 tier-1 chases = maxed out


def premium_signal(market: float | None, rrp: float | None) -> float | None:
    """1.0 at >= +60% over RRP, 0 at or below RRP."""
    if not market or not rrp:
        return None
    return _clamp((market / rrp - 1.0) / 0.6)


def score_release(release: dict, cfg: dict, preorder_market_aud: float | None = None) -> Score:
    reasons: list[str] = []
    sig: dict[str, float | None] = {}

    sig["set_type"] = cfg["set_type_weight"].get(release.get("set_type", "main"), 0.4)
    if sig["set_type"] >= 0.8:
        reasons.append(f"{release.get('set_type')} set")

    sig["chase"] = chase_signal(release, cfg)
    if sig["chase"] is not None and sig["chase"] >= 0.6:
        reasons.append("strong chase: " + ", ".join(release["chase"][:3]))

    sig["reprint"] = {"low": 1.0, "medium": 0.5, "high": 0.0}.get(release.get("reprint_risk", "medium"), 0.5)
    if release.get("reprint_risk") == "high":
        reasons.append("high reprint risk")

    sig["jp_premium"] = premium_signal(release.get("jp_box_market_jpy"), release.get("jp_box_rrp_jpy"))
    if sig["jp_premium"] is not None and sig["jp_premium"] >= 0.5:
        reasons.append("JP box trading well above RRP")

    rrp = (release.get("au_rrp") or {}).get("booster_box") or (release.get("au_rrp") or {}).get("etb")
    sig["preorder_premium"] = premium_signal(preorder_market_aud, rrp)
    if sig["preorder_premium"] is not None and sig["preorder_premium"] >= 0.5:
        reasons.append(f"eBay preorders ~A${preorder_market_aud:.0f} vs RRP A${rrp:.0f}")

    sig["game"] = _clamp(0.5 + cfg["game_hold_bonus"].get(release["game"], 0) * 5)

    def weighted(weights: dict) -> tuple[float, float]:
        total = have = 0.0
        for k, w in weights.items():
            if sig.get(k) is not None:
                total += w * sig[k]
                have += w
        return (total / have if have else 0.0), have

    flip, flip_cov = weighted(FLIP_WEIGHTS)
    hold, hold_cov = weighted(HOLD_WEIGHTS)

    if not release.get("confirmed", True):
        reasons.append("date unconfirmed")

    flip_i, hold_i = round(flip * 100), round(hold * 100)
    t = cfg["tiers"]
    conf = round(flip_cov, 2)

    # Flip-first verdict. The flip score sets the starting point...
    verdict = "BUY" if flip_i >= t["buy"] else "WATCH" if flip_i >= t["watch"] else "PASS"
    pass_reasons: list[str] = []

    # ...then hard PASS rules override it.
    est_profit = None
    if preorder_market_aud and rrp:
        lc = cfg["landed_cost"]
        est_profit = preorder_market_aud * (1 - lc["ebay_fee_pct"] / 100) - lc["au_postage_out"] - rrp
        profit_pct = est_profit / rrp * 100
        if est_profit <= 0:
            pass_reasons.append(f"no margin after eBay fees + postage (est A${est_profit:.0f}/unit)")
        elif profit_pct < cfg["flip"]["min_profit_pct"]:
            pass_reasons.append(f"thin margin: est A${est_profit:.0f}/unit ({profit_pct:.0f}%) — not worth the effort")
    jp_mkt, jp_rrp = release.get("jp_box_market_jpy"), release.get("jp_box_rrp_jpy")
    if jp_mkt and jp_rrp and jp_mkt <= jp_rrp:
        pass_reasons.append("JP version already at/below RRP — hype didn't stick")
    if release.get("reprint_risk") == "high" and (sig["preorder_premium"] or 0) < 0.5:
        pass_reasons.append("high reprint risk with no strong premium")

    if pass_reasons:
        verdict = "PASS"
        reasons = pass_reasons + reasons
    elif verdict == "BUY" and est_profit is None and sig["jp_premium"] is None:
        verdict = "WATCH"   # never BUY on hype alone — needs a market price
        reasons.append("held at WATCH until eBay/JP price data confirms margin")
    elif verdict == "PASS" and conf < 0.4:
        verdict = "WATCH"   # unknown is not a pass — wait for chase list / prices
        reasons.insert(0, "not enough data to call yet")
    elif verdict == "PASS":
        reasons.insert(0, "weak flip signals (set type/chase/premium)")
    elif conf < 0.6:
        reasons.append("low data — verdict will firm up as chase/price data lands")

    if verdict != "BUY" and hold_i >= 70:
        reasons.append(f"not a flip, but a hold candidate (hold {hold_i})")

    return Score(flip_i, hold_i, flip_i, verdict, conf, reasons, est_profit)
