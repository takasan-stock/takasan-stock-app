
"""
決算モメンタム分析エンジン v1
- 既存 scan.py の日足データを利用
- yfinance から最新四半期の業績・決算サプライズを取得
- 決算発表後の株価反応、出来高、SMA25、20日高値、52週高値を評価
- 100点満点の「決算モメンタム・スコア」を作成

注意:
yfinance の日本株決算データは欠損する場合があります。
欠損項目は無理に推定せず、スコアに反映しません。
"""

from __future__ import annotations

import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

MOMENTUM_VERSION = "2026-08-13-v2-tz-safe"


# =========================================================
# 共通
# =========================================================

def _num(v):
    try:
        if v is None:
            return np.nan
        return float(str(v).replace(",", "").replace("%", ""))
    except Exception:
        return np.nan


def _growth(now, old):
    if pd.isna(now) or pd.isna(old) or old == 0:
        return np.nan
    return (now / old - 1.0) * 100.0


def _latest_row(series):
    if series is None or len(series) == 0:
        return np.nan
    try:
        return _num(series.iloc[0])
    except Exception:
        return np.nan


def _get_line(stmt, names):
    for name in names:
        if name in stmt.index:
            return stmt.loc[name]
    return None


# =========================================================
# 最新決算情報
# =========================================================

def get_latest_earnings_event(ticker: str) -> dict:
    """
    直近の過去決算日と、取得できる場合のEPS/売上サプライズを返す。
    """
    out = {
        "earnings_date": pd.NaT,
        "eps_surprise": np.nan,
        "revenue_surprise": np.nan,
        "reported_eps": np.nan,
        "eps_estimate": np.nan,
    }

    try:
        tk = yf.Ticker(ticker)
        ed = tk.get_earnings_dates(limit=12)

        if ed is None or ed.empty:
            return out

        ed = ed.copy()
        ed = ed.reset_index()
        date_col = ed.columns[0]
        ed[date_col] = pd.to_datetime(ed[date_col], errors="coerce")
        ed = ed.dropna(subset=[date_col])

        now = pd.Timestamp.now(tz=None)
        # 決算日時をtz-naiveに揃える（tz-aware/naiveのどちらで返ってきても動くように）
        dt_col = pd.to_datetime(ed[date_col], errors="coerce")
        if getattr(dt_col.dt, "tz", None) is not None:
            dt_col = dt_col.dt.tz_localize(None)
        ed[date_col] = dt_col
        ed = ed[ed[date_col] <= now]
        if ed.empty:
            return out

        row = ed.sort_values(date_col, ascending=False).iloc[0]
        ed_ts = pd.Timestamp(row[date_col])
        if ed_ts.tz is not None:
            ed_ts = ed_ts.tz_localize(None)
        out["earnings_date"] = ed_ts

        lower_map = {str(c).lower(): c for c in ed.columns}

        def find_col(*keys):
            for k in keys:
                for low, original in lower_map.items():
                    if k in low:
                        return original
            return None

        c = find_col("eps surprise(%)", "eps surprise")
        if c:
            out["eps_surprise"] = _num(row[c])

        c = find_col("revenue surprise(%)", "revenue surprise")
        if c:
            out["revenue_surprise"] = _num(row[c])

        c = find_col("reported eps")
        if c:
            out["reported_eps"] = _num(row[c])

        c = find_col("eps estimate")
        if c:
            out["eps_estimate"] = _num(row[c])

    except Exception:
        pass

    return out


def get_quarterly_growth(ticker: str) -> dict:
    """
    最新四半期と前年同期を比較。
    さらに1四半期前のYoYを計算して「成長加速」を判定する。
    """
    out = {
        "revenue_growth": np.nan,
        "revenue_growth_prev": np.nan,
        "operating_growth": np.nan,
        "operating_growth_prev": np.nan,
        "net_income_growth": np.nan,
        "net_income_growth_prev": np.nan,
        "eps_growth": np.nan,
        "eps_growth_prev": np.nan,
        "operating_margin": np.nan,
        "operating_margin_prev": np.nan,
    }

    try:
        tk = yf.Ticker(ticker)
        stmt = tk.quarterly_income_stmt

        if stmt is None or stmt.empty:
            return out

        # yfinance は通常、新しい四半期が左側。
        # 日付順を明示してから使う。
        try:
            stmt = stmt.reindex(sorted(stmt.columns, reverse=True), axis=1)
        except Exception:
            pass

        if stmt.shape[1] < 5:
            return out

        rev = _get_line(stmt, ["Total Revenue", "Operating Revenue"])
        op = _get_line(stmt, ["Operating Income", "Operating Profit"])
        ni = _get_line(stmt, ["Net Income", "Net Income Common Stockholders"])
        eps = _get_line(stmt, ["Diluted EPS", "Basic EPS"])

        def yoy_pair(series):
            if series is None or len(series) < 5:
                return np.nan, np.nan
            current = _num(series.iloc[0])
            current_yoy = _num(series.iloc[4])
            previous = _num(series.iloc[1])
            previous_yoy = _num(series.iloc[5]) if len(series) >= 6 else np.nan
            return _growth(current, current_yoy), _growth(previous, previous_yoy)

        out["revenue_growth"], out["revenue_growth_prev"] = yoy_pair(rev)
        out["operating_growth"], out["operating_growth_prev"] = yoy_pair(op)
        out["net_income_growth"], out["net_income_growth_prev"] = yoy_pair(ni)
        out["eps_growth"], out["eps_growth_prev"] = yoy_pair(eps)

        if rev is not None and op is not None:
            r0 = _num(rev.iloc[0])
            o0 = _num(op.iloc[0])
            r1 = _num(rev.iloc[1]) if len(rev) >= 2 else np.nan
            o1 = _num(op.iloc[1]) if len(op) >= 2 else np.nan

            if r0 != 0 and not pd.isna(r0) and not pd.isna(o0):
                out["operating_margin"] = o0 / r0 * 100
            if r1 != 0 and not pd.isna(r1) and not pd.isna(o1):
                out["operating_margin_prev"] = o1 / r1 * 100

    except Exception:
        pass

    return out


# =========================================================
# 株価側
# =========================================================

def price_metrics(dfd: pd.DataFrame, earnings_date) -> dict:
    out = {
        "price": np.nan,
        "earnings_day_change": np.nan,
        "earnings_reaction": "データ不足",
        "volume_ratio": np.nan,
        "above_sma25": False,
        "breakout20": False,
        "near_52w_high": False,
        "return_20d": np.nan,
        "sma25": np.nan,
    }

    if dfd is None or dfd.empty:
        return out

    d = dfd.copy()
    if "Close" not in d.columns:
        return out

    d = d.dropna(subset=["Close"]).copy()
    if d.empty:
        return out

    if not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index, errors="coerce")
    d = d[~d.index.isna()]
    d = d.sort_index()

    c = pd.to_numeric(d["Close"], errors="coerce")
    v = pd.to_numeric(d["Volume"], errors="coerce") if "Volume" in d.columns else pd.Series(index=d.index, dtype=float)

    d["SMA25"] = c.rolling(25).mean()
    d["VOL20"] = v.rolling(20).mean()
    d["HIGH20_PREV"] = d["High"].shift(1).rolling(20).max() if "High" in d.columns else np.nan
    d["HIGH252"] = d["High"].rolling(252).max() if "High" in d.columns else np.nan

    latest = d.iloc[-1]
    out["price"] = _num(latest["Close"])
    out["sma25"] = _num(latest["SMA25"])
    out["above_sma25"] = (
        not pd.isna(out["sma25"]) and out["price"] > out["sma25"]
    )

    if len(d) >= 21:
        out["return_20d"] = _growth(c.iloc[-1], c.iloc[-21])

    if not pd.isna(latest.get("VOL20", np.nan)) and latest["VOL20"] > 0:
        out["volume_ratio"] = _num(latest["Volume"]) / _num(latest["VOL20"])

    if not pd.isna(latest.get("HIGH20_PREV", np.nan)):
        out["breakout20"] = out["price"] > _num(latest["HIGH20_PREV"])

    if not pd.isna(latest.get("HIGH252", np.nan)) and latest["HIGH252"] > 0:
        out["near_52w_high"] = (
            out["price"] >= _num(latest["HIGH252"]) * 0.90
        )

    # -----------------------------------------------------
    # 決算反応
    # 決算日以降の最初の取引日を「決算反応日」とする。
    # 厳密な場中/引け後判定は v2 で追加する。
    # -----------------------------------------------------
    if pd.notna(earnings_date):
        # 決算日をtz-naiveに正規化（既にnaiveでもエラーにならないようにする）
        ed = pd.Timestamp(earnings_date)
        if ed.tz is not None:
            ed = ed.tz_localize(None)

        # 日足インデックスもtz-naiveに揃える（tz-aware同士/naive同士でないと比較できない）
        idx = d.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
            d = d.copy()
            d.index = idx

        after = d[d.index.normalize() >= ed.normalize()]
        if not after.empty:
            reaction_idx = after.index[0]
            pos = d.index.get_loc(reaction_idx)

            if pos > 0:
                prev_close = _num(d["Close"].iloc[pos - 1])
                reaction_close = _num(d["Close"].iloc[pos])
                if prev_close > 0:
                    change = (reaction_close / prev_close - 1) * 100
                    out["earnings_day_change"] = change

                    if change >= 10:
                        out["earnings_reaction"] = "🔥 強烈"
                    elif change >= 5:
                        out["earnings_reaction"] = "🟢 強い"
                    elif change >= 3:
                        out["earnings_reaction"] = "🟢 良好"
                    elif change > -3:
                        out["earnings_reaction"] = "🟡 中立"
                    else:
                        out["earnings_reaction"] = "🔴 弱い"

    return out


# =========================================================
# RS風スコア
# =========================================================

def calculate_rs(dfd: pd.DataFrame) -> float:
    if dfd is None or len(dfd) < 61:
        return np.nan

    d = dfd.dropna(subset=["Close"]).sort_index()
    if len(d) < 61:
        return np.nan

    c = pd.to_numeric(d["Close"], errors="coerce")
    r20 = _growth(c.iloc[-1], c.iloc[-21])
    r60 = _growth(c.iloc[-1], c.iloc[-61])

    if pd.isna(r20) or pd.isna(r60):
        return np.nan

    return r20 * 0.4 + r60 * 0.6


# =========================================================
# スコア
# =========================================================

def score_financials(fin: dict, event: dict) -> tuple[int, dict]:
    score = 0
    detail = {}

    # 売上成長 15
    x = fin["revenue_growth"]
    if not pd.isna(x):
        if x >= 20:
            pts = 15
        elif x >= 10:
            pts = 12
        elif x >= 5:
            pts = 8
        elif x > 0:
            pts = 4
        else:
            pts = 0
        score += pts
        detail["売上成長"] = pts

    # 営業利益成長 20
    x = fin["operating_growth"]
    if not pd.isna(x):
        if x >= 30:
            pts = 20
        elif x >= 20:
            pts = 16
        elif x >= 10:
            pts = 10
        elif x > 0:
            pts = 5
        else:
            pts = 0
        score += pts
        detail["営業利益成長"] = pts

    # EPS成長 15
    x = fin["eps_growth"]
    if pd.isna(x):
        # EPSが取得できない日本株では純利益成長を代替指標にする。
        x = fin["net_income_growth"]
    if not pd.isna(x):
        if x >= 30:
            pts = 15
        elif x >= 20:
            pts = 12
        elif x >= 10:
            pts = 8
        elif x > 0:
            pts = 4
        else:
            pts = 0
        score += pts
        detail["EPS/純利益成長"] = pts

    # 成長加速 15
    pts = 0
    if (
        not pd.isna(fin["operating_growth"])
        and not pd.isna(fin["operating_growth_prev"])
        and fin["operating_growth"] > fin["operating_growth_prev"]
    ):
        pts += 8
    if (
        not pd.isna(fin["revenue_growth"])
        and not pd.isna(fin["revenue_growth_prev"])
        and fin["revenue_growth"] > fin["revenue_growth_prev"]
    ):
        pts += 4
    eps_now = fin["eps_growth"] if not pd.isna(fin["eps_growth"]) else fin["net_income_growth"]
    eps_prev = fin["eps_growth_prev"] if not pd.isna(fin["eps_growth_prev"]) else fin["net_income_growth_prev"]
    if not pd.isna(eps_now) and not pd.isna(eps_prev) and eps_now > eps_prev:
        pts += 3
    score += pts
    detail["成長加速"] = pts

    # 決算サプライズ 10
    x = event["eps_surprise"]
    if not pd.isna(x):
        if x >= 20:
            pts = 10
        elif x >= 10:
            pts = 7
        elif x > 0:
            pts = 4
        else:
            pts = 0
        score += pts
        detail["EPSサプライズ"] = pts

    # 営業利益率 5
    margin = fin["operating_margin"]
    if not pd.isna(margin):
        prev = fin["operating_margin_prev"]
        if not pd.isna(prev) and margin > prev:
            score += 5
            detail["利益率改善"] = 5
        elif margin >= 10:
            score += 3
            detail["利益率"] = 3

    return score, detail


def score_price(price: dict, rs: float) -> tuple[int, dict]:
    score = 0
    detail = {}

    x = price["earnings_day_change"]
    if not pd.isna(x):
        if x >= 10:
            pts = 10
        elif x >= 7:
            pts = 8
        elif x >= 5:
            pts = 6
        elif x >= 3:
            pts = 4
        elif x < -5:
            pts = -10
        else:
            pts = 0
        score += pts
        detail["決算後株価反応"] = pts

    x = price["volume_ratio"]
    if not pd.isna(x):
        if x >= 3:
            pts = 5
        elif x >= 2:
            pts = 4
        elif x >= 1.5:
            pts = 3
        else:
            pts = 0
        score += pts
        detail["出来高"] = pts

    if price["above_sma25"]:
        score += 2
        detail["SMA25"] = 2

    if price["breakout20"]:
        score += 2
        detail["20日高値"] = 2

    if price["near_52w_high"]:
        score += 1
        detail["52週高値"] = 1

    if not pd.isna(rs) and rs >= 20:
        score += 5
        detail["RS"] = 5
    elif not pd.isna(rs) and rs >= 10:
        score += 3
        detail["RS"] = 3

    return score, detail


def rank(score: int) -> str:
    if score >= 90:
        return "S+"
    if score >= 80:
        return "S"
    if score >= 70:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def buy_signal(score: int, price: dict, rs: float) -> str:
    chg = price["earnings_day_change"]
    vol = price["volume_ratio"]

    if (
        score >= 80
        and not pd.isna(chg) and chg >= 7
        and not pd.isna(vol) and vol >= 2
        and price["above_sma25"]
        and price["breakout20"]
    ):
        return "🔥 BUY"

    if (
        score >= 70
        and not pd.isna(chg) and chg >= 3
        and not pd.isna(vol) and vol >= 1.5
        and price["above_sma25"]
    ):
        return "🟢 BUY"

    if score >= 60:
        return "🟡 WATCH"

    return "🔴 AVOID"


# =========================================================
# 1銘柄
# =========================================================

def analyze_one(ticker: str, dfd: pd.DataFrame, code: str = "", name: str = "") -> dict | None:
    try:
        event = get_latest_earnings_event(ticker)

        if pd.isna(event["earnings_date"]):
            return None

        # 直近120日以内の決算だけ対象
        age = (pd.Timestamp.now() - event["earnings_date"]).days
        if age < 0 or age > 120:
            return None

        fin = get_quarterly_growth(ticker)
        price = price_metrics(dfd, event["earnings_date"])
        rs = calculate_rs(dfd)

        fin_score, fin_detail = score_financials(fin, event)
        price_score, price_detail = score_price(price, rs)

        # 理論上の最大100点に収める
        score = int(max(0, min(100, fin_score + price_score)))
        rnk = rank(score)
        signal = buy_signal(score, price, rs)

        # 決算後の株価がマイナス5%以上なら原則除外扱い
        chg = price["earnings_day_change"]
        if not pd.isna(chg) and chg <= -5:
            signal = "🔴 AVOID"

        return {
            "証券コード": code or ticker.replace(".T", ""),
            "Ticker": ticker,
            "銘柄名": name,
            "決算日": event["earnings_date"].strftime("%Y-%m-%d"),
            "決算後騰落率%": round(chg, 2) if not pd.isna(chg) else np.nan,
            "決算反応": price["earnings_reaction"],
            "売上成長%": round(fin["revenue_growth"], 1) if not pd.isna(fin["revenue_growth"]) else np.nan,
            "営業利益成長%": round(fin["operating_growth"], 1) if not pd.isna(fin["operating_growth"]) else np.nan,
            "EPS/純利益成長%": round(
                fin["eps_growth"] if not pd.isna(fin["eps_growth"]) else fin["net_income_growth"], 1
            ) if not pd.isna(fin["eps_growth"] if not pd.isna(fin["eps_growth"]) else fin["net_income_growth"]) else np.nan,
            "前回営業利益成長%": round(fin["operating_growth_prev"], 1) if not pd.isna(fin["operating_growth_prev"]) else np.nan,
            "成長加速": (
                "○" if (
                    not pd.isna(fin["operating_growth"])
                    and not pd.isna(fin["operating_growth_prev"])
                    and fin["operating_growth"] > fin["operating_growth_prev"]
                ) else "―"
            ),
            "営業利益率%": round(fin["operating_margin"], 1) if not pd.isna(fin["operating_margin"]) else np.nan,
            "EPSサプライズ%": round(event["eps_surprise"], 1) if not pd.isna(event["eps_surprise"]) else np.nan,
            "出来高倍率": round(price["volume_ratio"], 2) if not pd.isna(price["volume_ratio"]) else np.nan,
            "20日騰落率%": round(price["return_20d"], 1) if not pd.isna(price["return_20d"]) else np.nan,
            "SMA25上": "○" if price["above_sma25"] else "×",
            "20日高値更新": "○" if price["breakout20"] else "×",
            "52週高値接近": "○" if price["near_52w_high"] else "×",
            "RS風": round(rs, 1) if not pd.isna(rs) else np.nan,
            "業績点": fin_score,
            "株価点": price_score,
            "スコア": score,
            "ランク": rnk,
            "シグナル": signal,
        }

    except Exception:
        return None


# =========================================================
# 複数銘柄
# =========================================================

def build_momentum_results(
    candidates: list[tuple[str, str, str, pd.DataFrame]],
    max_workers: int = 4,
) -> pd.DataFrame:
    """
    candidates:
      [(ticker, code, name, daily_df), ...]
    """
    rows = []

    if not candidates:
        return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(analyze_one, ticker, dfd, code, name): ticker
            for ticker, code, name, dfd in candidates
        }

        for fut in as_completed(futures):
            try:
                row = fut.result()
                if row is not None:
                    rows.append(row)
            except Exception:
                pass

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # 決算モメンタムはスコア順、同点なら決算後騰落率順
    df = df.sort_values(
        ["スコア", "決算後騰落率%"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)

    df.insert(0, "順位", range(1, len(df) + 1))

    return df
