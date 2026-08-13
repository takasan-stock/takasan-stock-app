"""
決算モメンタム分析エンジン v3
- 既存 scan.py が取得済みの日足データを再利用
- 決算イベントと「現在の買いタイミング」を分離
- 決算反応日は、決算発表時刻を考慮して可能な範囲で判定
- 決算反応時の出来高と現在の出来高を分離
- スコアを厳密に100点配分
- 決算後5営業日以内だけを「BUY」の対象にする
- yfinanceの欠損は推定せず、取得できた項目だけで評価

注意:
yfinanceの日本株決算日時・四半期財務には欠損や表記揺れがあります。
特に決算発表時刻が取れない場合は「次の取引日」を反応日として扱います。
"""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import time as dt_time

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

MOMENTUM_VERSION = "2026-08-13-v3-event-based"

# =========================================================
# 設定
# =========================================================

BUY_WINDOW_TRADING_DAYS = 5
WATCH_WINDOW_TRADING_DAYS = 20

# 東証の通常取引終了は15:30。
# 明確な発表時刻が取得できない場合は次の取引日を採用する。
MARKET_CLOSE = dt_time(15, 30)

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


def _get_line(stmt, names):
    for name in names:
        if name in stmt.index:
            return stmt.loc[name]
    return None


def _normalize_dt_index(df):
    d = df.copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index, errors="coerce")
    d = d[~d.index.isna()].sort_index()

    # 日足は日付比較が中心なので、timezoneを外す
    idx = d.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx
    return d


# =========================================================
# 決算イベント
# =========================================================

def get_latest_earnings_event(ticker: str) -> dict:
    out = {
        "earnings_date": pd.NaT,
        "eps_surprise": np.nan,
        "revenue_surprise": np.nan,
        "reported_eps": np.nan,
        "eps_estimate": np.nan,
        "has_time": False,
    }

    try:
        tk = yf.Ticker(ticker)
        ed = tk.get_earnings_dates(limit=12)

        if ed is None or ed.empty:
            return out

        ed = ed.reset_index()
        date_col = ed.columns[0]
        ed[date_col] = pd.to_datetime(ed[date_col], errors="coerce")
        ed = ed.dropna(subset=[date_col])

        # 現在時刻と比較するため一旦timezoneを統一
        now = pd.Timestamp.now(tz=None)
        dt_col = pd.to_datetime(ed[date_col], errors="coerce")
        if getattr(dt_col.dt, "tz", None) is not None:
            dt_col = dt_col.dt.tz_localize(None)
        ed[date_col] = dt_col
        ed = ed[ed[date_col] <= now]

        if ed.empty:
            return out

        row = ed.sort_values(date_col, ascending=False).iloc[0]
        ed_ts = pd.Timestamp(row[date_col])

        out["earnings_date"] = ed_ts

        # 時刻情報が実質的に入っているか判定
        out["has_time"] = not (
            ed_ts.hour == 0 and
            ed_ts.minute == 0 and
            ed_ts.second == 0
        )

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


# =========================================================
# 四半期業績
# =========================================================

def get_quarterly_growth(ticker: str) -> dict:
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

        try:
            stmt = stmt.reindex(sorted(stmt.columns, reverse=True), axis=1)
        except Exception:
            pass

        if stmt.shape[1] < 5:
            return out

        rev = _get_line(stmt, ["Total Revenue", "Operating Revenue"])
        op = _get_line(stmt, ["Operating Income", "Operating Profit"])
        ni = _get_line(
            stmt,
            ["Net Income", "Net Income Common Stockholders"]
        )
        eps = _get_line(stmt, ["Diluted EPS", "Basic EPS"])

        def yoy_pair(series):
            if series is None or len(series) < 5:
                return np.nan, np.nan

            current = _num(series.iloc[0])
            current_yoy = _num(series.iloc[4])
            previous = _num(series.iloc[1])
            previous_yoy = (
                _num(series.iloc[5])
                if len(series) >= 6 else np.nan
            )

            return (
                _growth(current, current_yoy),
                _growth(previous, previous_yoy),
            )

        (
            out["revenue_growth"],
            out["revenue_growth_prev"],
        ) = yoy_pair(rev)

        (
            out["operating_growth"],
            out["operating_growth_prev"],
        ) = yoy_pair(op)

        (
            out["net_income_growth"],
            out["net_income_growth_prev"],
        ) = yoy_pair(ni)

        (
            out["eps_growth"],
            out["eps_growth_prev"],
        ) = yoy_pair(eps)

        if rev is not None and op is not None:
            r0 = _num(rev.iloc[0])
            o0 = _num(op.iloc[0])
            r1 = _num(rev.iloc[1]) if len(rev) >= 2 else np.nan
            o1 = _num(op.iloc[1]) if len(op) >= 2 else np.nan

            if not pd.isna(r0) and r0 != 0 and not pd.isna(o0):
                out["operating_margin"] = o0 / r0 * 100

            if not pd.isna(r1) and r1 != 0 and not pd.isna(o1):
                out["operating_margin_prev"] = o1 / r1 * 100

    except Exception:
        pass

    return out


# =========================================================
# 決算反応日の判定
# =========================================================

def _reaction_position(d, earnings_ts, has_time):
    """
    決算発表日時から、株価が最初に反応するとみなす取引日の
    positional index を返す。

    - 明確な時刻あり & 15:30より前 → 同日
    - 明確な時刻あり & 15:30以降 → 次の取引日
    - 時刻なし → 次の取引日
    """
    if pd.isna(earnings_ts) or d.empty:
        return None

    ed = pd.Timestamp(earnings_ts)

    # 日付だけの情報しかない場合は、同日終値を「決算反応」と
    # 誤認しないため次の取引日を採用。
    if not has_time:
        candidates = np.where(d.index.normalize() > ed.normalize())[0]
        return int(candidates[0]) if len(candidates) else None

    # 15:30以前なら同日、それ以降なら次の取引日
    if ed.time() < MARKET_CLOSE:
        candidates = np.where(d.index.normalize() == ed.normalize())[0]
        if len(candidates):
            return int(candidates[0])

    candidates = np.where(d.index.normalize() > ed.normalize())[0]
    return int(candidates[0]) if len(candidates) else None


# =========================================================
# 株価メトリクス
# =========================================================

def price_metrics(dfd: pd.DataFrame, earnings_event: dict) -> dict:
    out = {
        "price": np.nan,
        "reaction_date": pd.NaT,
        "reaction_change": np.nan,
        "reaction_volume_ratio": np.nan,
        "reaction_high_close_ratio": np.nan,
        "reaction_window_days": np.nan,
        "reaction_status": "データ不足",

        # 現在テクニカル
        "current_volume_ratio": np.nan,
        "above_sma25": False,
        "breakout20": False,
        "near_52w_high": False,
        "return_20d": np.nan,
        "sma25": np.nan,
        "current_high_close_ratio": np.nan,
    }

    if dfd is None or dfd.empty:
        return out

    d = _normalize_dt_index(dfd)

    required = ["Close", "Volume"]
    if any(c not in d.columns for c in required):
        return out

    d = d.dropna(subset=["Close"]).copy()
    if d.empty:
        return out

    c = pd.to_numeric(d["Close"], errors="coerce")
    v = pd.to_numeric(d["Volume"], errors="coerce")

    d["SMA25"] = c.rolling(25).mean()
    d["VOL20"] = v.rolling(20).mean()

    if "High" in d.columns:
        d["HIGH20_PREV"] = (
            pd.to_numeric(d["High"], errors="coerce")
            .shift(1)
            .rolling(20)
            .max()
        )
        d["HIGH252"] = (
            pd.to_numeric(d["High"], errors="coerce")
            .rolling(252)
            .max()
        )
    else:
        d["HIGH20_PREV"] = np.nan
        d["HIGH252"] = np.nan

    latest = d.iloc[-1]

    out["price"] = _num(latest["Close"])
    out["sma25"] = _num(latest["SMA25"])

    if not pd.isna(out["sma25"]):
        out["above_sma25"] = out["price"] > out["sma25"]

    if len(d) >= 21:
        out["return_20d"] = _growth(c.iloc[-1], c.iloc[-21])

    if not pd.isna(latest["VOL20"]) and latest["VOL20"] > 0:
        out["current_volume_ratio"] = (
            _num(latest["Volume"]) / _num(latest["VOL20"])
        )

    if not pd.isna(latest["HIGH20_PREV"]):
        out["breakout20"] = (
            out["price"] > _num(latest["HIGH20_PREV"])
        )

    if not pd.isna(latest["HIGH252"]) and latest["HIGH252"] > 0:
        out["near_52w_high"] = (
            out["price"] >= _num(latest["HIGH252"]) * 0.90
        )

    # 現在の終値が当日高値圏か
    if "High" in d.columns and not pd.isna(latest["High"]):
        high = _num(latest["High"])
        if high > 0:
            out["current_high_close_ratio"] = (
                out["price"] / high * 100
            )

    # -----------------------------------------------------
    # 決算反応
    # -----------------------------------------------------
    pos = _reaction_position(
        d,
        earnings_event["earnings_date"],
        earnings_event["has_time"],
    )

    if pos is None or pos <= 0 or pos >= len(d):
        return out

    reaction = d.iloc[pos]
    prev = d.iloc[pos - 1]

    reaction_close = _num(reaction["Close"])
    prev_close = _num(prev["Close"])

    if prev_close <= 0:
        return out

    reaction_change = (
        reaction_close / prev_close - 1
    ) * 100

    out["reaction_date"] = d.index[pos]
    out["reaction_change"] = reaction_change

    # 決算反応日の高値に対して終値がどれだけ高いか
    if "High" in d.columns:
        reaction_high = _num(reaction["High"])
        if reaction_high > 0:
            out["reaction_high_close_ratio"] = (
                reaction_close / reaction_high * 100
            )

    # 決算反応日だけの出来高。
    # 比較対象は反応日前20営業日で、反応日自身は含めない。
    start = max(0, pos - 20)
    baseline = pd.to_numeric(
        d["Volume"].iloc[start:pos],
        errors="coerce"
    ).dropna()

    if len(baseline) >= 5 and baseline.mean() > 0:
        out["reaction_volume_ratio"] = (
            _num(reaction["Volume"]) / baseline.mean()
        )

    # 決算後の経過営業日
    out["reaction_window_days"] = (
        len(d) - 1 - pos
    )

    if reaction_change >= 10:
        out["reaction_status"] = "🔥 強烈"
    elif reaction_change >= 7:
        out["reaction_status"] = "🔥 非常に強い"
    elif reaction_change >= 5:
        out["reaction_status"] = "🟢 強い"
    elif reaction_change >= 3:
        out["reaction_status"] = "🟢 良好"
    elif reaction_change > -3:
        out["reaction_status"] = "🟡 中立"
    else:
        out["reaction_status"] = "🔴 弱い"

    return out


# =========================================================
# RS風指標
# =========================================================

def calculate_rs(dfd: pd.DataFrame) -> float:
    if dfd is None:
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
# 100点スコア
#
# 業績・決算イベント = 85点
# 現在テクニカル      = 15点
# =========================================================

def score_financials(fin: dict, event: dict) -> tuple[int, dict]:
    score = 0
    detail = {}

    # ① 売上成長 12点
    x = fin["revenue_growth"]
    if not pd.isna(x):
        if x >= 20:
            pts = 12
        elif x >= 10:
            pts = 10
        elif x >= 5:
            pts = 6
        elif x > 0:
            pts = 3
        else:
            pts = 0
        score += pts
        detail["売上成長"] = pts

    # ② 営業利益成長 18点
    x = fin["operating_growth"]
    if not pd.isna(x):
        if x >= 30:
            pts = 18
        elif x >= 20:
            pts = 15
        elif x >= 10:
            pts = 9
        elif x > 0:
            pts = 4
        else:
            pts = 0
        score += pts
        detail["営業利益成長"] = pts

    # ③ EPS/純利益成長 12点
    x = fin["eps_growth"]
    if pd.isna(x):
        x = fin["net_income_growth"]

    if not pd.isna(x):
        if x >= 30:
            pts = 12
        elif x >= 20:
            pts = 10
        elif x >= 10:
            pts = 6
        elif x > 0:
            pts = 3
        else:
            pts = 0
        score += pts
        detail["EPS/純利益成長"] = pts

    # ④ 成長加速 13点
    pts = 0

    if (
        not pd.isna(fin["operating_growth"])
        and not pd.isna(fin["operating_growth_prev"])
        and fin["operating_growth"] > fin["operating_growth_prev"]
    ):
        pts += 7

    if (
        not pd.isna(fin["revenue_growth"])
        and not pd.isna(fin["revenue_growth_prev"])
        and fin["revenue_growth"] > fin["revenue_growth_prev"]
    ):
        pts += 3

    eps_now = (
        fin["eps_growth"]
        if not pd.isna(fin["eps_growth"])
        else fin["net_income_growth"]
    )
    eps_prev = (
        fin["eps_growth_prev"]
        if not pd.isna(fin["eps_growth_prev"])
        else fin["net_income_growth_prev"]
    )

    if (
        not pd.isna(eps_now)
        and not pd.isna(eps_prev)
        and eps_now > eps_prev
    ):
        pts += 3

    score += pts
    detail["成長加速"] = pts

    # ⑤ 利益率改善 5点
    margin = fin["operating_margin"]
    prev = fin["operating_margin_prev"]

    pts = 0
    if not pd.isna(margin):
        if not pd.isna(prev) and margin > prev:
            pts = 5
        elif margin >= 10:
            pts = 3

    score += pts
    detail["利益率改善"] = pts

    # ⑥ EPSサプライズ 10点
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

    return score, detail


def score_event_price(price: dict) -> tuple[int, dict]:
    score = 0
    detail = {}

    # ⑦ 決算反応 10点
    x = price["reaction_change"]

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
            pts = 0
        else:
            pts = 0

        score += pts
        detail["決算翌日反応"] = pts

    # ⑧ 決算反応日の出来高 5点
    x = price["reaction_volume_ratio"]

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
        detail["決算反応出来高"] = pts

    return score, detail


def score_current_technical(price: dict, rs: float) -> tuple[int, dict]:
    score = 0
    detail = {}

    # ⑨ SMA25 3点
    if price["above_sma25"]:
        score += 3
        detail["SMA25"] = 3

    # ⑩ 20日高値 4点
    if price["breakout20"]:
        score += 4
        detail["20日高値"] = 4

    # ⑪ 52週高値 2点
    if price["near_52w_high"]:
        score += 2
        detail["52週高値"] = 2

    # ⑫ RS風 6点
    if not pd.isna(rs):
        if rs >= 30:
            pts = 6
        elif rs >= 20:
            pts = 5
        elif rs >= 10:
            pts = 3
        elif rs > 0:
            pts = 1
        else:
            pts = 0

        score += pts
        detail["RS風"] = pts

    return score, detail


# =========================================================
# ランク
# =========================================================

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


# =========================================================
# BUY判定
# =========================================================

def buy_signal(score: int, price: dict, rs: float) -> str:
    """
    BUYは「決算後5営業日以内」に限定。
    それを超えた銘柄は、スコアが高くてもWATCH扱いにする。
    """

    days = price["reaction_window_days"]
    chg = price["reaction_change"]
    vol = price["reaction_volume_ratio"]

    if pd.isna(days):
        return "🟡 WATCH"

    if days > BUY_WINDOW_TRADING_DAYS:
        return "🟡 WATCH"

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

def analyze_one(
    ticker: str,
    dfd: pd.DataFrame,
    code: str = "",
    name: str = "",
) -> dict | None:

    try:
        event = get_latest_earnings_event(ticker)

        if pd.isna(event["earnings_date"]):
            return None

        # 直近120日以内
        age = (
            pd.Timestamp.now()
            - pd.Timestamp(event["earnings_date"])
        ).days

        if age < 0 or age > 120:
            return None

        fin = get_quarterly_growth(ticker)
        price = price_metrics(dfd, event)
        rs = calculate_rs(dfd)

        fin_score, fin_detail = score_financials(
            fin, event
        )
        event_score, event_detail = score_event_price(
            price
        )
        tech_score, tech_detail = score_current_technical(
            price, rs
        )

        # 厳密に100点
        # 業績70 + 決算イベント15 + 現在テクニカル15
        score = int(
            max(
                0,
                min(
                    100,
                    fin_score + event_score + tech_score
                ),
            )
        )

        rnk = rank(score)
        signal = buy_signal(score, price, rs)

        # 決算後-5%以下は市場評価が明確に悪いので除外
        chg = price["reaction_change"]
        if not pd.isna(chg) and chg <= -5:
            signal = "🔴 AVOID"

        eps_or_ni = (
            fin["eps_growth"]
            if not pd.isna(fin["eps_growth"])
            else fin["net_income_growth"]
        )

        growth_accel = "―"
        if (
            not pd.isna(fin["operating_growth"])
            and not pd.isna(fin["operating_growth_prev"])
            and fin["operating_growth"]
            > fin["operating_growth_prev"]
        ):
            growth_accel = "○"

        reaction_date = price["reaction_date"]

        return {
            "証券コード": code or ticker.replace(".T", ""),
            "Ticker": ticker,
            "銘柄名": name,

            "決算日": (
                event["earnings_date"].strftime("%Y-%m-%d")
                if not pd.isna(event["earnings_date"])
                else ""
            ),

            "決算反応日": (
                reaction_date.strftime("%Y-%m-%d")
                if not pd.isna(reaction_date)
                else ""
            ),

            "決算後経過営業日": (
                int(price["reaction_window_days"])
                if not pd.isna(price["reaction_window_days"])
                else np.nan
            ),

            "決算後騰落率%": (
                round(chg, 2)
                if not pd.isna(chg) else np.nan
            ),

            "決算反応": price["reaction_status"],

            "売上成長%": (
                round(fin["revenue_growth"], 1)
                if not pd.isna(fin["revenue_growth"])
                else np.nan
            ),

            "営業利益成長%": (
                round(fin["operating_growth"], 1)
                if not pd.isna(fin["operating_growth"])
                else np.nan
            ),

            "EPS/純利益成長%": (
                round(eps_or_ni, 1)
                if not pd.isna(eps_or_ni)
                else np.nan
            ),

            "前回営業利益成長%": (
                round(fin["operating_growth_prev"], 1)
                if not pd.isna(fin["operating_growth_prev"])
                else np.nan
            ),

            "成長加速": growth_accel,

            "営業利益率%": (
                round(fin["operating_margin"], 1)
                if not pd.isna(fin["operating_margin"])
                else np.nan
            ),

            "EPSサプライズ%": (
                round(event["eps_surprise"], 1)
                if not pd.isna(event["eps_surprise"])
                else np.nan
            ),

            # 決算イベント時の出来高
            "決算反応出来高倍率": (
                round(price["reaction_volume_ratio"], 2)
                if not pd.isna(price["reaction_volume_ratio"])
                else np.nan
            ),

            # 現在の出来高
            "現在出来高倍率": (
                round(price["current_volume_ratio"], 2)
                if not pd.isna(price["current_volume_ratio"])
                else np.nan
            ),

            "20日騰落率%": (
                round(price["return_20d"], 1)
                if not pd.isna(price["return_20d"])
                else np.nan
            ),

            "SMA25上": (
                "○" if price["above_sma25"] else "×"
            ),

            "20日高値更新": (
                "○" if price["breakout20"] else "×"
            ),

            "52週高値接近": (
                "○" if price["near_52w_high"] else "×"
            ),

            "RS風": (
                round(rs, 1)
                if not pd.isna(rs)
                else np.nan
            ),

            "業績点": fin_score,
            "決算イベント点": event_score,
            "現在テクニカル点": tech_score,

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

    rows = []

    if not candidates:
        return pd.DataFrame()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                analyze_one,
                ticker,
                dfd,
                code,
                name,
            ): ticker
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

    df = df.sort_values(
        ["スコア", "決算後騰落率%"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)

    df.insert(0, "順位", range(1, len(df) + 1))

    return df
