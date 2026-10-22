"""One small request to verify the ENTSO-E key. Never prints the key."""

import pandas as pd
from entsoe import EntsoePandasClient

from energy_forecasting.config import get_settings

s = get_settings()
assert s.entsoe_api_key.get_secret_value(), "ENTSOE_API_KEY missing"
c = EntsoePandasClient(api_key=s.entsoe_api_key.get_secret_value())
ts = c.query_day_ahead_prices(
    "FR", start=pd.Timestamp("2025-06-01", tz=s.tz), end=pd.Timestamp("2025-06-03", tz=s.tz)
)
print(
    "OK rows:",
    len(ts),
    "freq:",
    pd.infer_freq(ts.index),
    "first:",
    ts.index[0],
    "min/max:",
    ts.min(),
    ts.max(),
)
