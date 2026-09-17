"""
4系統サマリーへ各カテゴリスコアと総合スコアを付与する。

考え方:
- TURNAROUND / PULLBACK / BREAKOUT は、同カテゴリ内の元パターン数から0〜100点化。
  1パターン該当=60点を起点とし、カテゴリ内の確認材料が増えるほど80〜100点へ上げる。
- EARNINGS は既存の決算モメンタム「スコア」をそのまま0〜100点で利用。
- 総合スコアは「最も強いテクニカル系統」を主軸にし、複数系統合致を加点。
  決算モメンタムがある場合のみ20%をブレンドする。
  これにより、決算対象外の銘柄を一律に0点扱いして不利にしない。

既存A〜Gの判定ロジックは変更しない。
"""

import os
import json

import pandas as pd
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
        ws = sh.add_worksheet(title=sheet_name, rows=3000, cols=40)

    if df.empty:
        ws.update([["該当データなし"]])
        return

    out = df.copy().fillna("").astype(str)
    ws.update([out.columns.tolist()] + out.values.tolist())


def count_patterns(cell) -> int:
    s = str(cell).strip()
    if not s or s.lower() == "nan":
        return 0
    return len([x for x in s.split("+") if x.strip()])


def technical_category_score(pattern_count: int, max_patterns: int) -> int:
    """
    0件=0点。
    1件該当で60点、残りの確認材料を均等配分して最大100点。
    例:
      2要素カテゴリ: 1件=60, 2件=100
      3要素カテゴリ: 1件=60, 2件=80, 3件=100
    """
    if pattern_count <= 0:
        return 0
    if max_patterns <= 1:
        return 100
    extra = min(pattern_count, max_patterns) - 1
    return int(round(60 + 40 * extra / (max_patterns - 1)))


def rank_score(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def build_scores(summary: pd.DataFrame, earnings: pd.DataFrame) -> pd.DataFrame:
    # EARNINGS-only銘柄もサマリーへ含めるため、まず決算側を辞書化
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

    # 決算モメンタムだけに存在する銘柄を追加
    existing = set(summary.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
    extra_rows = []
    for ticker, info in earnings_map.items():
        if ticker in existing:
            continue
        extra_rows.append({
            "証券コード": info["code"],
            "Ticker": ticker,
            "銘柄名": info["name"],
            "終値": "",
            "該当系統数": 0,
            "該当系統": "EARNINGS",
            "TURNAROUND": "",
            "PULLBACK": "",
            "BREAKOUT": "",
            "元パターン合計": 0,
        })
    if extra_rows:
        summary = pd.concat([summary, pd.DataFrame(extra_rows)], ignore_index=True)

    rows = []
    for _, row in summary.iterrows():
        r = row.to_dict()
        ticker = str(r.get("Ticker", "")).strip()

        tech_scores = {}
        active_tech = 0
        for cat, max_patterns in TECH_MAX_PATTERNS.items():
            cnt = count_patterns(r.get(cat, ""))
            sc = technical_category_score(cnt, max_patterns)
            tech_scores[cat] = sc
            if sc > 0:
                active_tech += 1

        earnings_score = earnings_map.get(ticker, {}).get("score", 0.0)
        has_earnings = ticker in earnings_map

        best_tech = max(tech_scores.values()) if tech_scores else 0
        breadth_bonus = max(0, active_tech - 1) * 5
        technical_total = min(100, best_tech + breadth_bonus)

        if technical_total > 0 and has_earnings:
            overall = round(technical_total * 0.80 + earnings_score * 0.20)
        elif technical_total > 0:
            overall = round(technical_total)
        else:
            overall = round(earnings_score)

        active_labels = [c for c, sc in tech_scores.items() if sc > 0]
        if has_earnings:
            active_labels.append("EARNINGS")

        r["TURNAROUNDスコア"] = tech_scores["TURNAROUND"]
        r["PULLBACKスコア"] = tech_scores["PULLBACK"]
        r["BREAKOUTスコア"] = tech_scores["BREAKOUT"]
        r["EARNINGSスコア"] = int(round(earnings_score)) if has_earnings else 0
        r["テクニカル総合"] = int(round(technical_total))
        r["総合スコア"] = int(max(0, min(100, overall)))
        r["総合ランク"] = rank_score(r["総合スコア"])
        r["4系統該当"] = " + ".join(active_labels)
        rows.append(r)

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # 見やすい列順へ
    front = [
        "証券コード", "Ticker", "銘柄名", "終値", "総合スコア", "総合ランク",
        "TURNAROUNDスコア", "PULLBACKスコア", "BREAKOUTスコア", "EARNINGSスコア",
        "テクニカル総合", "4系統該当", "該当系統数", "該当系統",
        "TURNAROUND", "PULLBACK", "BREAKOUT", "元パターン合計",
    ]
    rest = [c for c in out.columns if c not in front]
    out = out[[c for c in front if c in out.columns] + rest]
    return out.sort_values(
        ["総合スコア", "テクニカル総合", "Ticker"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    summary = read_sheet(sh, "4系統サマリー")
    earnings = read_sheet(sh, "決算モメンタム")
    scored = build_scores(summary, earnings)

    write_sheet(sh, "4系統サマリー", scored)
    print(f"4系統サマリー総合スコア付与: {len(scored)}銘柄", flush=True)


if __name__ == "__main__":
    main()
