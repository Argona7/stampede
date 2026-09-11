"""fx_rates: ETH hourly history + spot for other quote assets, and the nearest-hour lookup."""
from types import SimpleNamespace as NS

from stampede import chain
from stampede.context import fx
from stampede.store import Store

USDG = "0x5fc5e5e4f9a4d7b9b1a2c3d4e5f60718293a4b5c"


class FakeSession:
    def get(self, url, params=None, timeout=None, headers=None):
        if "coingecko.com" in url:
            base = 1_789_000_000 * 1000
            return NS(status_code=200, raise_for_status=lambda: None, json=lambda: {"prices": [[base + i * 3_600_000, 2500.0 + i] for i in range(48)]})
        return NS(status_code=200, raise_for_status=lambda: None, json=lambda: {"data": {"attributes": {"token_prices": {USDG: "0.9959"}}}})


def test_load_fx_and_lookup(tmp_path):
    s = Store(tmp_path / "fx.sqlite")
    s.db.execute("INSERT INTO curves(curve,token,pair_token,resolved_via) VALUES('0xc','0xt',?, 'test')", (USDG,))
    s.commit()
    now = 1_789_000_000 + 47 * 3600
    res = fx.load_fx(s, days=1.0, session=FakeSession(), now=now)
    assert res["spot_tokens"] == 1 and res["spot_missing"] == [] and res["eth_hours"] >= 24
    f = fx.Fx(s)
    assert f.usd(chain.NATIVE, 1_789_000_000 + 30 * 3600 + 100) == 2530.0  # latest hour <= ts
    assert f.usd(chain.WETH, 1_789_000_000 + 30 * 3600) == 2530.0
    assert f.usd(chain.NATIVE, 1_789_000_000 + 5 * 3600) is None  # before the loaded range
    assert f.usd(USDG, now) == 0.9959
    assert f.usd("0xunknown", now) is None
    assert round(f.to_usd(chain.NATIVE, 2 * 10**18, 18, 1_789_000_000 + 30 * 3600), 1) == 5060.0
    assert s.db.execute("SELECT DISTINCT source FROM fx_rates WHERE token=?", (USDG,)).fetchone() == ("geckoterminal_spot",)
    s.close()
