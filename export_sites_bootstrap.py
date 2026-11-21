from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from early_rules import early_ideal_mask, early_top20_pool

OUT_DIR = Path(os.getenv("SITES_BOOTSTRAP_DIR", "sites-bootstrap"))
PUBLIC_DROP_COLUMNS = {"メモ", "memo", "監視メモ", "watch_memo"}


def _client():
    raw = os.getenv("GCP_SA_KEY", "").strip()
    sheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    if not raw or not sheet_id:
        raise RuntimeError("GCP_SA_KEY / SPREADSHEET_ID is required")
    info = json.loads(raw)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds), sheet_id


def _sheet(gc, spreadsheet_id: str, name: str) -> pd.DataFrame:
    try:
        ws = gc.open_by_key(spreadsheet_id).worksheet(name)
        vals = ws.get_all_values()
    except gspread.WorksheetNotFound:
        return pd.DataFrame()
    if len(vals) < 2:
        return pd.DataFrame(columns=vals[0] if vals else [])
    df = pd.DataFrame(vals[1:], columns=vals[0])
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df.drop(columns=[c for c in df.columns if c in PUBLIC_DROP_COLUMNS], errors="ignore")


def _records(df: pd.DataFrame):
    if df is None or df.empty:
        return []
    safe = df.copy()
    numeric_cols = safe.select_dtypes(include=["number"]).columns
    if len(numeric_cols):
        safe[numeric_cols] = safe[numeric_cols].replace([float("inf"), float("-inf")], pd.NA)
    return json.loads(safe.to_json(orient="records", force_ascii=False, date_format="iso"))


def _write(name: str, payload: dict):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}", flush=True)


def main():
    gc, spreadsheet_id = _client()
    summary_df = _sheet(gc, spreadsheet_id, "4系統サマリー")
    weekly_df = _sheet(gc, spreadsheet_id, "週足転換H")

    early_df = early_top20_pool(weekly_df, 70, 70).head(20).copy()
    if not early_df.empty:
        early_df["理想本命"] = early_ideal_mask(early_df).map({True: "⭐ 理想本命", False: ""})

    generated_at = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    summary_rows = _records(summary_df)
    early_rows = _records(early_df)

    _write("summary.json", {"rows": summary_rows})
    _write("early.json", {"rows": early_rows})
    _write(
        "meta.json",
        {
            "schema_version": 1,
            "generated_at": generated_at,
            "source": "Google Sheets via GitHub Actions",
            "summary_count": len(summary_rows),
            "early_count": len(early_rows),
            "watchlist_included": False,
            "chart_included": False,
        },
    )


if __name__ == "__main__":
    main()
