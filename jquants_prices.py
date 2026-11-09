from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

import pandas as pd
import requests


JQUANTS_V2_BASE = "https://api.jquants.com/v2"


def _code4(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.endswith(".T"):
        text = text[:-2]
    if len(text) == 5 and text.endswith("0"):
        text = text[:4]
    return text


def _date_text(value) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%d")


@dataclass
class JQuantsPriceClient:
    api_key: str
    base_url: str = JQUANTS_V2_BASE
    timeout: int = 30
    min_interval_seconds: float = 1.05

    def __post_init__(self):
        self._last_request_at = 0.0
        self._session = requests.Session()
        self._session.headers.update({"x-api-key": self.api_key})

    @classmethod
    def from_env(cls) -> "JQuantsPriceClient | None":
        key = os.environ.get("JQUANTS_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key)

    def _wait_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict) -> list[dict]:
        url = f"{self.base_url}{path}"
        query = {k: v for k, v in params.items() if v not in (None, "")}
        rows: list[dict] = []

        while True:
            response = None
            for attempt in range(5):
                self._wait_rate_limit()
                response = self._session.get(url, params=query, timeout=self.timeout)
                self._last_request_at = time.monotonic()

                if response.status_code == 403:
                    raise PermissionError(
                        "J-Quants returned 403. Check JQUANTS_API_KEY and plan access."
                    )
                if response.status_code != 429:
                    break

                retry_after = response.headers.get("Retry-After", "").strip()
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(30.0, 2.0 * (2 ** attempt))
                print(
                    f"[J-Quants] 429 rate limit; retry {attempt + 1}/5 after {wait:.1f}s",
                    flush=True,
                )
                time.sleep(max(1.0, wait))

            if response is None:
                raise RuntimeError("J-Quants request ended without a response")
            response.raise_for_status()
            payload = response.json()

            batch = payload.get("data", [])
            if isinstance(batch, list):
                rows.extend(batch)

            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                break
            query["pagination_key"] = pagination_key

        return rows

    def daily_bars(self, ticker: str, start_date, end_date=None) -> pd.DataFrame:
        code = _code4(ticker)
        if not code:
            return pd.DataFrame()

        rows = self._get(
            "/equities/bars/daily",
            {
                "code": code,
                "from": _date_text(start_date),
                "to": _date_text(end_date or date.today()),
            },
        )
        if not rows:
            return pd.DataFrame()

        raw = pd.DataFrame.from_records(rows)
        if "Date" not in raw.columns:
            return pd.DataFrame()

        out = pd.DataFrame(index=pd.to_datetime(raw["Date"], errors="coerce"))
        field_map = {
            "Open": ("AdjO", "O"),
            "High": ("AdjH", "H"),
            "Low": ("AdjL", "L"),
            "Close": ("AdjC", "C"),
            "Volume": ("AdjVo", "Vo"),
        }
        for target, choices in field_map.items():
            series = None
            for source in choices:
                if source in raw.columns:
                    candidate = pd.to_numeric(raw[source], errors="coerce")
                    series = candidate if series is None else series.fillna(candidate)
            out[target] = series.values if series is not None else pd.NA

        out = out[~out.index.isna()].copy()
        out = out.dropna(subset=["Close"])
        out = out[~out.index.duplicated(keep="last")].sort_index()
        out.index = out.index.normalize()
        return out


def download_jquants_histories(
    tickers: Iterable[str],
    start_date,
    end_date=None,
) -> dict[str, pd.DataFrame]:
    client = JQuantsPriceClient.from_env()
    if client is None:
        return {}

    result: dict[str, pd.DataFrame] = {}
    unique = [str(t).strip() for t in dict.fromkeys(tickers) if str(t).strip()]

    print(
        f"[J-Quants] adjusted daily bars: {len(unique)} tickers "
        f"from {_date_text(start_date)} to {_date_text(end_date or date.today())}",
        flush=True,
    )

    for i, ticker in enumerate(unique, start=1):
        try:
            df = client.daily_bars(ticker, start_date, end_date=end_date)
            if not df.empty:
                result[ticker] = df
            else:
                print(f"[J-Quants] empty: {ticker}", flush=True)
        except Exception as exc:
            print(
                f"[J-Quants] failed {ticker}: {type(exc).__name__}: {exc}",
                flush=True,
            )

        if i % 20 == 0 or i == len(unique):
            print(
                f"[J-Quants] progress {i}/{len(unique)} success={len(result)}",
                flush=True,
            )

    return result
