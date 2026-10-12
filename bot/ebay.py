"""eBay AU price lookup via the Browse API (same OAuth pattern as card-bot).

The Browse API only sees ACTIVE listings, so this is an asking-price proxy:
trimmed median of the cheapest realistic listings. Good enough to spot a
preorder premium; not a sold-price comp.
"""
from __future__ import annotations

import base64
import os
import statistics

import requests

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_token: str | None = None


def _get_token() -> str | None:
    global _token
    if _token:
        return _token
    cid, secret = os.getenv("EBAY_CLIENT_ID"), os.getenv("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        return None
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = requests.post(TOKEN_URL, headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/x-www-form-urlencoded"},
                      data={"grant_type": "client_credentials",
                            "scope": "https://api.ebay.com/oauth/api_scope"}, timeout=20)
    r.raise_for_status()
    _token = r.json()["access_token"]
    return _token


def trimmed_median(prices: list[float], trim: float = 0.2) -> float | None:
    if not prices:
        return None
    p = sorted(prices)
    k = int(len(p) * trim)
    core = p[k:len(p) - k] or p
    return statistics.median(core)


def market_price_aud(query: str, limit: int = 40) -> float | None:
    token = _get_token()
    if not token or not query:
        return None
    r = requests.get(SEARCH_URL, headers={"Authorization": f"Bearer {token}",
                                          "X-EBAY-C-MARKETPLACE-ID": "EBAY_AU"},
                     params={"q": query, "limit": limit,
                             "filter": "buyingOptions:{FIXED_PRICE},itemLocationCountry:AU,priceCurrency:AUD"},
                     timeout=20)
    if r.status_code != 200:
        return None
    bad = ("single", "card lot", "empty", "sleeve", "case only", "japanese", "chinese", "korean", "break", "spot")
    sealed = ("box", "elite trainer", "etb", "bundle", "display", "collection")
    prices = []
    for it in r.json().get("itemSummaries", []):
        title = it.get("title", "").lower()
        if any(b in title for b in bad) or not any(s in title for s in sealed):
            continue
        try:
            prices.append(float(it["price"]["value"]))
        except (KeyError, ValueError):
            pass
    return trimmed_median(prices)
