"""Overseas landed cost and resale margin, in AUD."""
from __future__ import annotations

import requests

_fx_cache: dict[str, float] = {}


def fx_to_aud(currency: str) -> float:
    currency = currency.upper()
    if currency == "AUD":
        return 1.0
    if currency not in _fx_cache:
        r = requests.get("https://api.frankfurter.app/latest", params={"from": currency, "to": "AUD"}, timeout=20)
        r.raise_for_status()
        _fx_cache[currency] = r.json()["rates"]["AUD"]
    return _fx_cache[currency]


def landed_cost(offer: dict, cfg: dict, fx: float | None = None) -> dict:
    """offer: {store, country, price, currency, qty?, shipping?, forwarder_fee?, url?}"""
    lc = cfg["landed_cost"]
    fx = fx if fx is not None else fx_to_aud(offer["currency"])
    qty = offer.get("qty", 1)
    goods = offer["price"] * qty * fx
    shipping = offer.get("shipping", 0) * fx
    fwd = offer.get("forwarder_fee", lc["default_forwarder_fee"].get(offer.get("country", ""), 0))
    subtotal = goods + shipping + fwd
    gst = subtotal * lc["gst_pct"] / 100
    import_fee = lc["import_processing_fee_aud"] if goods > lc["low_value_threshold_aud"] else 0
    total = subtotal + gst + import_fee
    return {"goods": goods, "shipping": shipping, "forwarder": fwd, "gst": gst,
            "import_fee": import_fee, "total": total, "per_unit": total / qty, "qty": qty}


def resale_margin(per_unit_cost: float, au_sale_price: float, cfg: dict) -> dict:
    lc = cfg["landed_cost"]
    fees = au_sale_price * lc["ebay_fee_pct"] / 100 + lc["au_postage_out"]
    net = au_sale_price - fees
    profit = net - per_unit_cost
    return {"net_after_fees": net, "profit": profit, "margin_pct": profit / per_unit_cost * 100 if per_unit_cost else 0}
