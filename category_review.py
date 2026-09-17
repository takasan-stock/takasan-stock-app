"""
既存A〜Gスクリーニング結果を壊さずに、4系統の上位分類と重複分析を追加する。

- TURNAROUND : 週足A / GC底打ちF
- PULLBACK   : 日足B1 / 日足B2 / 初押しD
- BREAKOUT   : ボリバンC / 出来高E / ポケピG
- EARNINGS   : 決算モメンタム（既存シートをそのまま参照）

このスクリプトは Google スプレッドシート上の既存結果を読み取り、
「4系統サマリー」と「重複分析」シートを書き出す。
既存のA〜Gシートは削除・変更しない。
"""

import os
import json
from itertools import combinations

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials


CATEGORY_MAP = {
    "TURNAROUND": {
        "label": "底打ち転換",
        "patterns": {
            "週足パターンA": "週A",
            "GC底打ちF21x200": "GC底打ちF",
        },
    },
    "PULLBACK": {
        "label": "上昇トレンド押し目",
        "patterns": {
            "日足B1押し目待ち": "日B1",
            "日足B2反発エントリー": "日B2",
            "初押しD下ひげ陽線": "初押しD",
        },
    },
    "BREAKOUT": {
        "label": "出来高ブレイク",
        "patterns": {
            "ボリバンCブレイク": "ボリバンC",
            "出来高E急増ブレイク": "出来高E",
            "ポケットピボットG": "ポケピG",
        },
    },
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
    if values[0] == ["該当銘柄なし"]:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])


def write_sheet(sh, sheet_name, df):
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=3000, cols=30)

    if df.empty:
        ws.update([["該当データなし"]])
        return

    out = df.copy().fillna("").astype(str)
    ws.update([out.columns.tolist()] + out.values.tolist())


def ticker_set(df):
    if df.empty or "Ticker" not in df.columns:
        return set()
    return set(df["Ticker"].astype(str).str.strip()) - {"", "-"}


def first_value(row, candidates):
    for c in candidates:
        if c in row and str(row[c]).strip() not in ("", "nan"):
            return row[c]
    return ""


def build_summary(sheet_frames):
    per_ticker = {}

    for category, meta in CATEGORY_MAP.items():
        for sheet_name, pattern_label in meta["patterns"].items():
            df = sheet_frames.get(sheet_name, pd.DataFrame())
            if df.empty or "Ticker" not in df.columns:
                continue
            for _, row in df.iterrows():
                ticker = str(row.get("Ticker", "")).strip()
                if not ticker:
                    continue
                rec = per_ticker.setdefault(ticker, {
                    "Ticker": ticker,
                    "証券コード": first_value(row, ["証券コード"]),
                    "銘柄名": first_value(row, ["銘柄名"]),
                    "終値": first_value(row, ["終値"]),
                    "TURNAROUND": [],
                    "PULLBACK": [],
                    "BREAKOUT": [],
                })
                if not rec["証券コード"]:
                    rec["証券コード"] = first_value(row, ["証券コード"])
                if not rec["銘柄名"]:
                    rec["銘柄名"] = first_value(row, ["銘柄名"])
                if not rec["終値"]:
                    rec["終値"] = first_value(row, ["終値"])
                rec[category].append(pattern_label)

    rows = []
    for ticker, rec in per_ticker.items():
        cats = [c for c in ("TURNAROUND", "PULLBACK", "BREAKOUT") if rec[c]]
        row = {
            "証券コード": rec["証券コード"],
            "Ticker": ticker,
            "銘柄名": rec["銘柄名"],
            "終値": rec["終値"],
            "該当系統数": len(cats),
            "該当系統": " + ".join(cats),
            "TURNAROUND": " + ".join(rec["TURNAROUND"]),
            "PULLBACK": " + ".join(rec["PULLBACK"]),
            "BREAKOUT": " + ".join(rec["BREAKOUT"]),
            "元パターン合計": sum(len(rec[c]) for c in ("TURNAROUND", "PULLBACK", "BREAKOUT")),
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["該当系統数", "元パターン合計", "Ticker"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def build_overlap(sheet_frames):
    pattern_sets = {}
    for category, meta in CATEGORY_MAP.items():
        for sheet_name, pattern_label in meta["patterns"].items():
            pattern_sets[pattern_label] = ticker_set(sheet_frames.get(sheet_name, pd.DataFrame()))

    rows = []
    labels = list(pattern_sets.keys())
    for a, b in combinations(labels, 2):
        sa, sb = pattern_sets[a], pattern_sets[b]
        inter = sa & sb
        union = sa | sb
        smaller = min(len(sa), len(sb))
        rows.append({
            "パターン1": a,
            "パターン2": b,
            "件数1": len(sa),
            "件数2": len(sb),
            "共通銘柄数": len(inter),
            "小さい側に対する重複率%": round((len(inter) / smaller * 100), 1) if smaller else 0.0,
            "Jaccard重複率%": round((len(inter) / len(union) * 100), 1) if union else 0.0,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(
        ["小さい側に対する重複率%", "共通銘柄数"],
        ascending=[False, False],
    ).reset_index(drop=True)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    all_sheet_names = []
    for meta in CATEGORY_MAP.values():
        all_sheet_names.extend(meta["patterns"].keys())

    frames = {name: read_sheet(sh, name) for name in all_sheet_names}

    summary = build_summary(frames)
    overlap = build_overlap(frames)

    write_sheet(sh, "4系統サマリー", summary)
    write_sheet(sh, "重複分析", overlap)

    print(f"4系統サマリー: {len(summary)}銘柄", flush=True)
    print(f"重複分析: {len(overlap)}組み合わせ", flush=True)
    print("既存A〜Gの結果は変更していません。", flush=True)


if __name__ == "__main__":
    main()
