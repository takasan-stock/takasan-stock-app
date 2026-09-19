"""
4系統サマリーへ市場モメンタムを加えた総合スコア v5 を付与する。

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


SCORE_VERSION = "v5-actionability"

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


def _band_score(x: float, ideal_low: float, ideal_high: float, hard_low: float, hard_high: float) -> float:
    """理想帯を100点、許容帯の外端を0点として両側を線形補間する。"""
    if pd.isna(x):
        return np.nan
    if hard_high <= hard_low or ideal_high < ideal_low:
        return np.nan
    if ideal_low <= x <= ideal_high:
        return 100.0
    if x < ideal_low:
        if x <= hard_low:
            return 0.0
        return max(0.0, min(100.0, (x - hard_low) / (ideal_low - hard_low) * 100.0))
    if x >= hard_high:
        return 0.0
    return max(0.0, min(100.0, (hard_high - x) / (hard_high - ideal_high) * 100.0))


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
    v4: TURNAROUND / PULLBACK / BREAKOUT ごとに「良い価格位置」を別定義する。
    一律に「MAより上=100」「高値圏=100」とせず、過熱も減点する。
    """
    close = _safe_series(df, "Close")
    if len(close) < 55:
        return {
            "価格品質": np.nan,
            "TURNAROUND品質": np.nan,
            "PULLBACK品質": np.nan,
            "BREAKOUT品質": np.nan,
            "過熱ペナルティ": np.nan,
            "20MA乖離%": np.nan,
            "50MA乖離%": np.nan,
            "MA傾き": np.nan,
            "52週高値距離%": np.nan,
            "20日レンジ位置": np.nan,
            "20日ブレイク距離%": np.nan,
        }

    last = float(close.iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else np.nan

    dev20 = (last / ma20 - 1.0) * 100.0 if ma20 > 0 else np.nan
    dev50 = (last / ma50 - 1.0) * 100.0 if ma50 > 0 else np.nan
    dev200 = (last / ma200 - 1.0) * 100.0 if not pd.isna(ma200) and ma200 > 0 else np.nan

    ma20_prev = float(close.iloc[-25:-5].mean()) if len(close) >= 25 else np.nan
    ma50_prev = float(close.iloc[-55:-5].mean()) if len(close) >= 55 else np.nan
    slope20 = ((ma20 / ma20_prev) - 1.0) * 100.0 if ma20_prev and ma20_prev > 0 else np.nan
    slope50 = ((ma50 / ma50_prev) - 1.0) * 100.0 if ma50_prev and ma50_prev > 0 else np.nan
    slope_score = weighted_available([
        (_band_score(slope20, 0.10, 2.50, -2.0, 5.0), 0.60),
        (_band_score(slope50, 0.00, 1.80, -1.5, 3.5), 0.40),
    ])

    lookback = close.tail(min(252, len(close)))
    hi52 = float(lookback.max()) if not lookback.empty else np.nan
    dist52_pct = (last / hi52 - 1.0) * 100.0 if hi52 and hi52 > 0 else np.nan

    c20 = close.tail(20)
    lo20 = float(c20.min())
    hi20 = float(c20.max())
    range20 = ((last - lo20) / (hi20 - lo20) * 100.0) if hi20 > lo20 else 50.0

    prior20 = close.iloc[-21:-1] if len(close) >= 21 else close.iloc[:-1]
    prior20_hi = float(prior20.max()) if not prior20.empty else np.nan
    breakout20_pct = (last / prior20_hi - 1.0) * 100.0 if prior20_hi and prior20_hi > 0 else np.nan

    # 過熱ペナルティ: 20MAから+8%、50MAから+15%を超えると段階的に減点。
    p20 = _linear_score(dev20, 8.0, 18.0)
    p50 = _linear_score(dev50, 15.0, 30.0)
    p20 = 0.0 if pd.isna(p20) else p20 * 0.15
    p50 = 0.0 if pd.isna(p50) else p50 * 0.10
    overheat_penalty = min(25.0, p20 + p50)

    # TURNAROUND: 立ち上がりを評価。高値張り付きや上方乖離し過ぎは不要。
    turn = weighted_available([
        (_band_score(dev50, -2.0, 8.0, -10.0, 18.0), 0.25),
        (_band_score(dev200, -3.0, 10.0, -15.0, 25.0), 0.15),
        (slope_score, 0.30),
        (_band_score(dist52_pct, -22.0, -5.0, -45.0, 2.0), 0.15),
        (_band_score(range20, 55.0, 90.0, 20.0, 100.0), 0.15),
    ])
    turn = max(0.0, turn - overheat_penalty)

    # PULLBACK: 上昇トレンドを保ちながら20/50MA付近へ健全に押している形。
    pull = weighted_available([
        (_band_score(dev20, -2.5, 4.0, -8.0, 10.0), 0.30),
        (_band_score(dev50, 0.0, 10.0, -6.0, 20.0), 0.20),
        (slope_score, 0.25),
        (_band_score(dist52_pct, -15.0, -1.0, -32.0, 2.0), 0.15),
        (_band_score(range20, 40.0, 82.0, 15.0, 100.0), 0.10),
    ])
    pull = max(0.0, pull - overheat_penalty)

    # BREAKOUT: 20日高値付近/突破、上向きMA、52週高値圏を評価。
    # ブレイク局面では多少の乖離を許容するが、伸び過ぎは減点。
    brk = weighted_available([
        (_band_score(breakout20_pct, -1.5, 4.0, -7.0, 12.0), 0.30),
        (_band_score(dev20, 1.0, 7.0, -3.0, 14.0), 0.20),
        (slope_score, 0.20),
        (_band_score(dist52_pct, -8.0, 0.0, -22.0, 2.0), 0.20),
        (_band_score(range20, 78.0, 100.0, 50.0, 101.0), 0.10),
    ])
    brk = max(0.0, brk - overheat_penalty * 0.70)

    # 参考用の総合価格品質。実際の銘柄評価では該当系統の品質だけを採用する。
    generic = weighted_available([(turn, 1.0), (pull, 1.0), (brk, 1.0)])

    return {
        "価格品質": round(generic, 1),
        "TURNAROUND品質": round(turn, 1),
        "PULLBACK品質": round(pull, 1),
        "BREAKOUT品質": round(brk, 1),
        "過熱ペナルティ": round(overheat_penalty, 1),
        "20MA乖離%": round(dev20, 1) if not pd.isna(dev20) else np.nan,
        "50MA乖離%": round(dev50, 1) if not pd.isna(dev50) else np.nan,
        "MA傾き": round(slope_score, 1),
        "52週高値距離%": round(dist52_pct, 1) if not pd.isna(dist52_pct) else np.nan,
        "20日レンジ位置": round(range20, 1),
        "20日ブレイク距離%": round(breakout20_pct, 1) if not pd.isna(breakout20_pct) else np.nan,
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


def action_status(score: float) -> str:
    """実戦スコアを日々の確認優先度へ変換する。売買推奨ではなく監視優先度。"""
    if score >= 82:
        return "🔥 強い"
    if score >= 72:
        return "✅ 良好"
    if score >= 62:
        return "👀 監視"
    return "⏸ 低優先"


def heat_status(penalty: float) -> str:
    if pd.isna(penalty):
        return ""
    if penalty >= 15:
        return "⚠️ 高"
    if penalty >= 7:
        return "🟡 注意"
    return "🟢 適正"


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
        # 該当している系統だけ、その系統専用の価格品質を採用する。
        regime_quality_parts = []
        for cat, sc in tech_scores.items():
            if sc > 0:
                regime_quality_parts.append((m.get(f"{cat}品質", np.nan), sc))
        if regime_quality_parts:
            price_quality = weighted_available(regime_quality_parts)
        else:
            price_quality = m.get("価格品質", np.nan)

        # パターン構造60% + 系統別価格品質40%
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

        # 主力シグナル: 「実際に該当した系統」だけを対象に、
        # パターン強度45% + その系統専用の価格品質55%で最も強い局面を選ぶ。
        setup_scores = {}
        for cat, pattern_sc in tech_scores.items():
            if pattern_sc <= 0:
                continue
            q = m.get(f"{cat}品質", np.nan)
            setup_scores[cat] = weighted_available([
                (pattern_sc, 0.45),
                (q, 0.55),
            ])

        if setup_scores:
            main_signal = max(setup_scores, key=setup_scores.get)
            main_signal_score = setup_scores[main_signal]
        elif not pd.isna(earnings_score):
            main_signal = "EARNINGS"
            main_signal_score = float(earnings_score)
        else:
            main_signal = ""
            main_signal_score = 0.0

        # 実戦スコア: 総合評価65% + 主力セットアップ35%。
        # v4の過熱ペナルティは各系統品質へ既に反映済みなので二重減点しない。
        practical_score = weighted_available([
            (overall, 0.65),
            (main_signal_score, 0.35),
        ])
        overheat = m.get("過熱ペナルティ", np.nan)

        active_labels = [c for c, sc in tech_scores.items() if sc > 0]
        if not pd.isna(earnings_score):
            active_labels.append("EARNINGS")

        r["TURNAROUNDスコア"] = tech_scores["TURNAROUND"]
        r["PULLBACKスコア"] = tech_scores["PULLBACK"]
        r["BREAKOUTスコア"] = tech_scores["BREAKOUT"]
        r["EARNINGSスコア"] = int(round(earnings_score)) if not pd.isna(earnings_score) else 0
        r["パターン構造"] = int(round(pattern_structure))
        r["価格品質"] = round(float(price_quality), 1) if not pd.isna(price_quality) else ""
        r["TURNAROUND品質"] = m.get("TURNAROUND品質", "")
        r["PULLBACK品質"] = m.get("PULLBACK品質", "")
        r["BREAKOUT品質"] = m.get("BREAKOUT品質", "")
        r["過熱ペナルティ"] = m.get("過熱ペナルティ", "")
        r["20MA乖離%"] = m.get("20MA乖離%", "")
        r["50MA乖離%"] = m.get("50MA乖離%", "")
        r["MA傾き"] = m.get("MA傾き", "")
        r["52週高値距離%"] = m.get("52週高値距離%", "")
        r["20日レンジ位置"] = m.get("20日レンジ位置", "")
        r["20日ブレイク距離%"] = m.get("20日ブレイク距離%", "")
        r["テクニカル総合"] = int(round(technical_total))
        r["RS Rating"] = int(round(rs_rating)) if not pd.isna(rs_rating) else ""
        r["出来高モメンタム"] = round(float(vol_momentum), 1) if not pd.isna(vol_momentum) else ""
        r["当日出来高倍率"] = m.get("当日出来高倍率", "")
        r["5日出来高倍率"] = m.get("5日出来高倍率", "")
        r["上昇日出来高比率%"] = m.get("上昇日出来高比率%", "")
        r["RS母集団"] = m.get("RS母集団", "")
        r["主力シグナル"] = main_signal
        r["主力品質"] = round(float(main_signal_score), 1)
        r["実戦スコア"] = int(round(max(0, min(100, practical_score))))
        r["実戦ステータス"] = action_status(r["実戦スコア"])
        r["過熱判定"] = heat_status(overheat)
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
        "証券コード", "Ticker", "銘柄名", "終値",
        "実戦スコア", "実戦ステータス", "主力シグナル", "主力品質", "過熱判定", "過熱ペナルティ",
        "総合スコア", "総合ランク", "スコアバージョン", "スコア更新日時",
        "RS Rating", "出来高モメンタム", "テクニカル総合", "パターン構造", "価格品質",
        "TURNAROUND品質", "PULLBACK品質", "BREAKOUT品質", "過熱ペナルティ",
        "20MA乖離%", "50MA乖離%", "MA傾き", "52週高値距離%", "20日レンジ位置",
        "20日ブレイク距離%", "EARNINGSスコア",
        "TURNAROUNDスコア", "PULLBACKスコア", "BREAKOUTスコア",
        "当日出来高倍率", "5日出来高倍率", "上昇日出来高比率%", "RS母集団",
        "4系統該当", "該当系統数", "該当系統", "TURNAROUND", "PULLBACK", "BREAKOUT", "元パターン合計",
    ]
    rest = [c for c in out.columns if c not in front]
    out = out[[c for c in front if c in out.columns] + rest]
    return out.sort_values(
        ["実戦スコア", "総合スコア", "RS Rating", "出来高モメンタム", "Ticker"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    summary = read_sheet(sh, "4系統サマリー")
    earnings = read_sheet(sh, "決算モメンタム")
    scored = build_scores(summary, earnings)

    write_sheet(sh, "4系統サマリー", scored)
    print(f"総合スコアv5付与: {len(scored)}銘柄", flush=True)
    if not scored.empty and "RS母集団" in scored.columns:
        print(f"RS Rating母集団: {scored['RS母集団'].iloc[0]}", flush=True)


if __name__ == "__main__":
    main()
