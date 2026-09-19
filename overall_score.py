"""
4系統サマリーへ市場モメンタムを加えた総合スコア v3 を付与する。

総合スコアの基本配分:
- テクニカル構造 40%
- RS Rating       25%
- 出来高モメンタム 15%
- EARNINGS        20%（決算データがある場合）

EARNINGS が無い銘柄は減点せず、残り3要素へ比率を再配分する。
RS Rating は4系統サマリー内の候補銘柄を母集団にした1〜99の相対順位。
既存A〜Gの判定ロジックは変更しない。
"""

import os
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials


SCORE_VERSION = "v3-continuous"

TECH_MAX_PATTERNS = {
    "TURNAROUND": 2,
    "PULLBACK": 3,
    "BREAKOUT": 3,
}


def get_client():
    sa_key_str = os.environ.get("GCP_SA_KEY")
    if not sa_key_str:
        raise RuntimeError("環境変数 GCP_SA_KEY が設定されていません")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません")

    sa_info = json.loads(sa_key_str)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds), spreadsheet_id


def read_sheet(sh, sheet_name):
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()
    if values[0] in (["該当銘柄なし"], ["該当データなし"]):
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])


def write_sheet(sh, sheet_name, df):
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=3000, cols=50)

    if df.empty:
        ws.update([["該当データなし"]])
        return

    out = df.copy().replace([np.inf, -np.inf], np.nan).fillna("").astype(str)
    ws.update([out.columns.tolist()] + out.values.tolist())


def count_patterns(cell) -> int:
    s = str(cell).strip()
    if not s or s.lower() == "nan":
        return 0
    return len([x for x in s.split("+") if x.strip()])


def technical_category_score(pattern_count: int, max_patterns: int) -> int:
    if pattern_count <= 0:
        return 0
    if max_patterns <= 1:
        return 100
    extra = min(pattern_count, max_patterns) - 1
    return int(round(60 + 40 * extra / (max_patterns - 1)))


def technical_structure_score(tech_scores: dict) -> int:
    """単一カテゴリ満点だけで総合100にならないよう、広がりも評価する。"""
    active = [v for v in tech_scores.values() if v > 0]
    if not active:
        return 0
    best = max(active)
    avg = sum(active) / len(active)
    breadth_bonus = max(0, len(active) - 1) * 5
    # 1カテゴリだけ100点でも90点。複数カテゴリ確認で最大100へ近づく。
    score = best * 0.75 + avg * 0.15 + breadth_bonus
    return int(round(max(0, min(100, score))))


def rank_score(score: float) -> str:
    # v3: 実戦で上位候補が見えやすいよう閾値を再調整
    if score >= 85:
        return "S"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 55:
        return "C"
    if score >= 45:
        return "D"
    return "E"


def _safe_series(df: pd.DataFrame, name: str) -> pd.Series:
    if df is None or df.empty or name not in df.columns:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df[name], errors="coerce").dropna()
    return s


def _extract_one(downloaded: pd.DataFrame, ticker: str, single_ticker: bool) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    try:
        if single_ticker:
            df = downloaded.copy()
        elif isinstance(downloaded.columns, pd.MultiIndex):
            level0 = downloaded.columns.get_level_values(0)
            level1 = downloaded.columns.get_level_values(1)
            if ticker in level0:
                df = downloaded[ticker].copy()
            elif ticker in level1:
                df = downloaded.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        return df
    except Exception:
        return pd.DataFrame()


def weighted_price_strength(close: pd.Series) -> float:
    """直近3か月を重めにした 3/6/9/12か月 加重リターン。"""
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < 65:
        return np.nan
    last = float(close.iloc[-1])
    windows = [(63, 0.40), (126, 0.20), (189, 0.20), (252, 0.20)]
    total = 0.0
    used = 0.0
    for days, weight in windows:
        if len(close) > days:
            base = float(close.iloc[-days - 1])
            if base > 0:
                total += ((last / base) - 1.0) * 100.0 * weight
                used += weight
    return total / used if used > 0 else np.nan


def _linear_score(x: float, low: float, high: float) -> float:
    if pd.isna(x):
        return np.nan
    if high <= low:
        return 0.0
    return max(0.0, min(100.0, (x - low) / (high - low) * 100.0))


def volume_momentum_score(df: pd.DataFrame) -> tuple[float, float, float, float]:
    """
    戻り値: (0-100点, 当日出来高倍率, 5日出来高倍率, 10日上昇日出来高比率)
    """
    close = _safe_series(df, "Close")
    vol = _safe_series(df, "Volume")
    if len(close) < 21 or len(vol) < 21:
        return np.nan, np.nan, np.nan, np.nan

    v20 = float(vol.iloc[-20:].mean())
    if not math.isfinite(v20) or v20 <= 0:
        return np.nan, np.nan, np.nan, np.nan

    latest_ratio = float(vol.iloc[-1]) / v20
    five_ratio = float(vol.iloc[-5:].mean()) / v20

    aligned = pd.DataFrame({"c": close, "v": vol}).dropna().tail(11)
    up_share = np.nan
    if len(aligned) >= 6:
        changes = aligned["c"].diff()
        recent = aligned.iloc[1:]
        up_mask = changes.iloc[1:] > 0
        denom = float(recent["v"].sum())
        if denom > 0:
            up_share = float(recent.loc[up_mask, "v"].sum()) / denom

    s_latest = _linear_score(latest_ratio, 0.8, 2.5)
    s_five = _linear_score(five_ratio, 0.8, 1.8)
    s_up = _linear_score(up_share, 0.40, 0.70)

    pieces = [(s_latest, 0.40), (s_five, 0.30), (s_up, 0.30)]
    valid = [(s, w) for s, w in pieces if not pd.isna(s)]
    if not valid:
        return np.nan, latest_ratio, five_ratio, up_share
    score = sum(s * w for s, w in valid) / sum(w for _, w in valid)
    return round(score, 1), round(latest_ratio, 2), round(five_ratio, 2), round(up_share * 100, 1) if not pd.isna(up_share) else np.nan


def price_technical_quality(df: pd.DataFrame) -> dict:
    """
    パターン該当の有無とは別に、株価そのものの質を0〜100点で連続評価する。
    評価要素:
    - 終値の20/50/200日線に対する位置
    - 20/50日線の傾き
    - 52週高値からの距離
    - 20日レンジ内の位置
    """
    close = _safe_series(df, "Close")
    if len(close) < 55:
        return {
            "価格品質": np.nan, "MA位置": np.nan, "MA傾き": np.nan,
            "52週高値近接": np.nan, "20日レンジ位置": np.nan,
        }

    last = float(close.iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else np.nan

    # MA位置: 20日・50日を重視。200日は取得できる場合のみ加点。
    pos_parts = [
        (100.0 if last >= ma20 else _linear_score(last / ma20, 0.90, 1.00), 0.35),
        (100.0 if last >= ma50 else _linear_score(last / ma50, 0.88, 1.00), 0.35),
    ]
    if not pd.isna(ma200):
        pos_parts.append((100.0 if last >= ma200 else _linear_score(last / ma200, 0.85, 1.00), 0.30))
    ma_position = weighted_available(pos_parts)

    # MA傾き: 5営業日前との比較。20MAをやや重め。
    ma20_prev = float(close.iloc[-25:-5].mean()) if len(close) >= 25 else np.nan
    ma50_prev = float(close.iloc[-55:-5].mean()) if len(close) >= 55 else np.nan
    slope20 = ((ma20 / ma20_prev) - 1.0) * 100 if ma20_prev and ma20_prev > 0 else np.nan
    slope50 = ((ma50 / ma50_prev) - 1.0) * 100 if ma50_prev and ma50_prev > 0 else np.nan
    slope20_score = _linear_score(slope20, -2.0, 3.0)
    slope50_score = _linear_score(slope50, -1.5, 2.5)
    ma_slope = weighted_available([(slope20_score, 0.60), (slope50_score, 0.40)])

    # 52週高値に近いほど強い。ただし高値そのものだけで満点にしない。
    lookback = close.tail(min(252, len(close)))
    hi52 = float(lookback.max()) if not lookback.empty else np.nan
    dist52 = (last / hi52) if hi52 and hi52 > 0 else np.nan
    high52_score = _linear_score(dist52, 0.75, 1.00)

    # 20日レンジ内の位置。高値圏ほど高得点。
    c20 = close.tail(20)
    lo20 = float(c20.min())
    hi20 = float(c20.max())
    range20 = ((last - lo20) / (hi20 - lo20) * 100.0) if hi20 > lo20 else 50.0

    quality = weighted_available([
        (ma_position, 0.35),
        (ma_slope, 0.25),
        (high52_score, 0.20),
        (range20, 0.20),
    ])
    return {
        "価格品質": round(quality, 1),
        "MA位置": round(ma_position, 1),
        "MA傾き": round(ma_slope, 1),
        "52週高値近接": round(high52_score, 1),
        "20日レンジ位置": round(range20, 1),
    }


def fetch_market_scores(tickers: list[str]) -> pd.DataFrame:
    """候補銘柄をまとめて取得し、RS原点と出来高モメンタムを算出。"""
    tickers = [t for t in dict.fromkeys(tickers) if t]
    records = []

    for start in range(0, len(tickers), 80):
        chunk = tickers[start:start + 80]
        try:
            data = yf.download(
                chunk,
                period="13mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            print(f"市場スコア取得失敗 chunk={start}: {e}", flush=True)
            continue

        single = len(chunk) == 1
        for ticker in chunk:
            df = _extract_one(data, ticker, single)
            close = _safe_series(df, "Close")
            rs_raw = weighted_price_strength(close)
            vol_score, latest_vr, five_vr, up_share = volume_momentum_score(df)
            tq = price_technical_quality(df)
            records.append({
                "Ticker": ticker,
                "RS原点": rs_raw,
                "出来高モメンタム": vol_score,
                "当日出来高倍率": latest_vr,
                "5日出来高倍率": five_vr,
                "上昇日出来高比率%": up_share,
                **tq,
            })

    out = pd.DataFrame(records)
    if out.empty:
        return out

    valid = out["RS原点"].notna()
    n = int(valid.sum())
    out["RS Rating"] = np.nan
    if n >= 2:
        pct = out.loc[valid, "RS原点"].rank(method="average", pct=True)
        out.loc[valid, "RS Rating"] = (1 + pct * 98).round().clip(1, 99)
    elif n == 1:
        out.loc[valid, "RS Rating"] = 50

    out["RS母集団"] = n
    return out


def weighted_available(parts: list[tuple[float, float]]) -> float:
    valid = [(float(v), float(w)) for v, w in parts if not pd.isna(v)]
    if not valid:
        return 0.0
    return sum(v * w for v, w in valid) / sum(w for _, w in valid)


def build_scores(summary: pd.DataFrame, earnings: pd.DataFrame) -> pd.DataFrame:
    earnings_map = {}
    if not earnings.empty and "Ticker" in earnings.columns:
        for _, row in earnings.iterrows():
            ticker = str(row.get("Ticker", "")).strip()
            if not ticker:
                continue
            try:
                score = float(row.get("スコア", 0) or 0)
            except Exception:
                score = 0.0
            earnings_map[ticker] = {
                "score": max(0.0, min(100.0, score)),
                "code": str(row.get("証券コード", "")).strip(),
                "name": str(row.get("銘柄名", "")).strip(),
            }

    if summary.empty:
        summary = pd.DataFrame(columns=[
            "証券コード", "Ticker", "銘柄名", "終値", "該当系統数", "該当系統",
            "TURNAROUND", "PULLBACK", "BREAKOUT", "元パターン合計",
        ])

    existing = set(summary.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
    extra_rows = []
    for ticker, info in earnings_map.items():
        if ticker in existing:
            continue
        extra_rows.append({
            "証券コード": info["code"], "Ticker": ticker, "銘柄名": info["name"],
            "終値": "", "該当系統数": 0, "該当系統": "EARNINGS",
            "TURNAROUND": "", "PULLBACK": "", "BREAKOUT": "", "元パターン合計": 0,
        })
    if extra_rows:
        summary = pd.concat([summary, pd.DataFrame(extra_rows)], ignore_index=True)

    tickers = summary.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip().tolist()
    market = fetch_market_scores(tickers)
    market_map = market.set_index("Ticker").to_dict("index") if not market.empty else {}

    rows = []
    for _, row in summary.iterrows():
        r = row.to_dict()
        ticker = str(r.get("Ticker", "")).strip()

        tech_scores = {}
        for cat, max_patterns in TECH_MAX_PATTERNS.items():
            cnt = count_patterns(r.get(cat, ""))
            tech_scores[cat] = technical_category_score(cnt, max_patterns)

        pattern_structure = technical_structure_score(tech_scores)
        earnings_score = earnings_map.get(ticker, {}).get("score", np.nan)
        m = market_map.get(ticker, {})
        price_quality = m.get("価格品質", np.nan)
        # パターン構造60% + 株価の連続品質40%で横並びを解消
        technical_total = weighted_available([
            (pattern_structure, 0.60),
            (price_quality, 0.40),
        ])
        rs_rating = m.get("RS Rating", np.nan)
        vol_momentum = m.get("出来高モメンタム", np.nan)

        # 決算データが無い場合はEARNINGS 20%を自動再配分。
        overall = weighted_available([
            (technical_total, 0.40),
            (rs_rating, 0.25),
            (vol_momentum, 0.15),
            (earnings_score, 0.20),
        ])

        active_labels = [c for c, sc in tech_scores.items() if sc > 0]
        if not pd.isna(earnings_score):
            active_labels.append("EARNINGS")

        r["TURNAROUNDスコア"] = tech_scores["TURNAROUND"]
        r["PULLBACKスコア"] = tech_scores["PULLBACK"]
        r["BREAKOUTスコア"] = tech_scores["BREAKOUT"]
        r["EARNINGSスコア"] = int(round(earnings_score)) if not pd.isna(earnings_score) else 0
        r["パターン構造"] = int(round(pattern_structure))
        r["価格品質"] = round(float(price_quality), 1) if not pd.isna(price_quality) else ""
        r["MA位置"] = m.get("MA位置", "")
        r["MA傾き"] = m.get("MA傾き", "")
        r["52週高値近接"] = m.get("52週高値近接", "")
        r["20日レンジ位置"] = m.get("20日レンジ位置", "")
        r["テクニカル総合"] = int(round(technical_total))
        r["RS Rating"] = int(round(rs_rating)) if not pd.isna(rs_rating) else ""
        r["出来高モメンタム"] = round(float(vol_momentum), 1) if not pd.isna(vol_momentum) else ""
        r["当日出来高倍率"] = m.get("当日出来高倍率", "")
        r["5日出来高倍率"] = m.get("5日出来高倍率", "")
        r["上昇日出来高比率%"] = m.get("上昇日出来高比率%", "")
        r["RS母集団"] = m.get("RS母集団", "")
        r["総合スコア"] = int(round(max(0, min(100, overall))))
        r["総合ランク"] = rank_score(r["総合スコア"])
        r["4系統該当"] = " + ".join(active_labels)
        rows.append(r)

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    score_updated_at = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")
    out["スコアバージョン"] = SCORE_VERSION
    out["スコア更新日時"] = score_updated_at

    front = [
        "証券コード", "Ticker", "銘柄名", "終値", "総合スコア", "総合ランク",
        "スコアバージョン", "スコア更新日時",
        "RS Rating", "出来高モメンタム", "テクニカル総合", "パターン構造", "価格品質",
        "MA位置", "MA傾き", "52週高値近接", "20日レンジ位置", "EARNINGSスコア",
        "TURNAROUNDスコア", "PULLBACKスコア", "BREAKOUTスコア",
        "当日出来高倍率", "5日出来高倍率", "上昇日出来高比率%", "RS母集団",
        "4系統該当", "該当系統数", "該当系統", "TURNAROUND", "PULLBACK", "BREAKOUT", "元パターン合計",
    ]
    rest = [c for c in out.columns if c not in front]
    out = out[[c for c in front if c in out.columns] + rest]
    return out.sort_values(
        ["総合スコア", "RS Rating", "出来高モメンタム", "Ticker"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    summary = read_sheet(sh, "4系統サマリー")
    earnings = read_sheet(sh, "決算モメンタム")
    scored = build_scores(summary, earnings)

    write_sheet(sh, "4系統サマリー", scored)
    print(f"総合スコアv3付与: {len(scored)}銘柄", flush=True)
    if not scored.empty and "RS母集団" in scored.columns:
        print(f"RS Rating母集団: {scored['RS母集団'].iloc[0]}", flush=True)


if __name__ == "__main__":
    main()
