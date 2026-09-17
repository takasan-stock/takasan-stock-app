"""
4系統サマリーへ市場モメンタムを加えた総合スコア v2 を付与する。

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

import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials


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
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
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
            records.append({
                "Ticker": ticker,
                "RS原点": rs_raw,
                "出来高モメンタム": vol_score,
                "当日出来高倍率": latest_vr,
                "5日出来高倍率": five_vr,
                "上昇日出来高比率%": up_share,
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

        technical_total = technical_structure_score(tech_scores)
        earnings_score = earnings_map.get(ticker, {}).get("score", np.nan)
        m = market_map.get(ticker, {})
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
        r["テクニカル総合"] = technical_total
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

    front = [
        "証券コード", "Ticker", "銘柄名", "終値", "総合スコア", "総合ランク",
        "RS Rating", "出来高モメンタム", "テクニカル総合", "EARNINGSスコア",
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
    print(f"総合スコアv2付与: {len(scored)}銘柄", flush=True)
    if not scored.empty and "RS母集団" in scored.columns:
        print(f"RS Rating母集団: {scored['RS母集団'].iloc[0]}", flush=True)


if __name__ == "__main__":
    main()
