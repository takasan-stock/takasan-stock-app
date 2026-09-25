"""実戦スコアの事後検証を自動記録する。

対象:
- 4系統サマリーのうち「今日見るべき」条件を満たす銘柄
  実戦スコア >= 82
  RS Rating >= 80
  出来高モメンタム >= 60
  過熱判定 = 適正

記録:
- シグナル日の終値
- 1 / 5 / 20 営業日後の終値と騰落率
- 主力シグナル、実戦スコア、RS、出来高モメンタム等

Google Sheets:
- 実戦検証
- 実戦検証サマリー
"""

import os
import json
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials


TRACK_SHEET = "実戦検証"
SUMMARY_SHEET = "実戦検証サマリー"
JST = ZoneInfo("Asia/Tokyo")


def get_client():
    sa_key_str = os.environ.get("GCP_SA_KEY")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not sa_key_str:
        raise RuntimeError("環境変数 GCP_SA_KEY が設定されていません")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません")

    info = json.loads(sa_key_str)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds), spreadsheet_id


def read_sheet(sh, name):
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(values[1:], columns=values[0])
    return df.loc[:, ~df.columns.duplicated(keep="first")].copy()


def write_sheet(sh, name, df):
    try:
        ws = sh.worksheet(name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=max(1000, len(df) + 50), cols=max(30, len(df.columns) + 5))

    if df.empty:
        ws.update([["該当データなし"]])
        return

    out = df.loc[:, ~df.columns.duplicated(keep="first")].copy()
    out = out.replace([np.inf, -np.inf], np.nan).fillna("").astype(str)
    ws.update([out.columns.tolist()] + out.values.tolist())


def as_num(v):
    try:
        return float(v)
    except Exception:
        return np.nan


def today_watch_mask(df):
    practical = pd.to_numeric(df.get("実戦スコア", pd.Series(np.nan, index=df.index)), errors="coerce")
    rs = pd.to_numeric(df.get("RS Rating", pd.Series(np.nan, index=df.index)), errors="coerce")
    volm = pd.to_numeric(df.get("出来高モメンタム", pd.Series(np.nan, index=df.index)), errors="coerce")
    heat = df.get("過熱判定", pd.Series("", index=df.index)).astype(str)
    return (
        practical.ge(82)
        & rs.ge(80)
        & volm.ge(60)
        & heat.str.contains("適正", na=False)
    )


def extract_one(downloaded, ticker, single):
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    try:
        if single:
            df = downloaded.copy()
        elif isinstance(downloaded.columns, pd.MultiIndex):
            l0 = downloaded.columns.get_level_values(0)
            l1 = downloaded.columns.get_level_values(1)
            if ticker in l0:
                df = downloaded[ticker].copy()
            elif ticker in l1:
                df = downloaded.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        if "Close" not in df.columns:
            return pd.DataFrame()

        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna(subset=["Close"]).copy()
        if df.empty:
            return df

        idx = pd.to_datetime(df.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("Asia/Tokyo").tz_localize(None)
        df.index = idx.normalize()
        return df[~df.index.duplicated(keep="last")].sort_index()
    except Exception:
        return pd.DataFrame()


def download_histories(tickers, start_date):
    tickers = [t for t in dict.fromkeys(tickers) if t]
    result = {}
    if not tickers:
        return result

    end_date = datetime.now(JST).date() + timedelta(days=3)
    start_str = pd.Timestamp(start_date).strftime("%Y-%m-%d")
    end_str = pd.Timestamp(end_date).strftime("%Y-%m-%d")

    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        try:
            data = yf.download(
                chunk,
                start=start_str,
                end=end_str,
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            print(f"検証用株価取得失敗 chunk={i}: {e}", flush=True)
            continue

        single = len(chunk) == 1
        for ticker in chunk:
            result[ticker] = extract_one(data, ticker, single)

    return result


def ensure_record_columns(df):
    cols = [
        "キー", "シグナル日", "証券コード", "Ticker", "銘柄名",
        "主力シグナル", "実戦スコア", "総合スコア", "RS Rating",
        "出来高モメンタム", "主力品質", "過熱判定", "過熱ペナルティ",
        "シグナル終値",
        "1日後日付", "1日後終値", "1日後騰落率%",
        "5日後日付", "5日後終値", "5日後騰落率%",
        "20日後日付", "20日後終値", "20日後騰落率%",
        "スコアバージョン", "記録日時", "最終更新日時",
    ]
    if df.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols].copy()


def add_new_signals(records, current, histories):
    if current.empty:
        return records

    flagged = current[today_watch_mask(current)].copy()
    if flagged.empty:
        return records

    existing = set(records.get("キー", pd.Series(dtype=str)).astype(str))

    new_rows = []
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")

    for _, row in flagged.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        hist = histories.get(ticker, pd.DataFrame())
        if not ticker or hist.empty:
            continue

        signal_ts = hist.index[-1]
        signal_date = signal_ts.strftime("%Y-%m-%d")
        key = f"{signal_date}|{ticker}"
        if key in existing:
            continue

        entry = float(hist["Close"].iloc[-1])
        new_rows.append({
            "キー": key,
            "シグナル日": signal_date,
            "証券コード": str(row.get("証券コード", "")).strip(),
            "Ticker": ticker,
            "銘柄名": str(row.get("銘柄名", "")).strip(),
            "主力シグナル": str(row.get("主力シグナル", "")).strip(),
            "実戦スコア": row.get("実戦スコア", ""),
            "総合スコア": row.get("総合スコア", ""),
            "RS Rating": row.get("RS Rating", ""),
            "出来高モメンタム": row.get("出来高モメンタム", ""),
            "主力品質": row.get("主力品質", ""),
            "過熱判定": row.get("過熱判定", ""),
            "過熱ペナルティ": row.get("過熱ペナルティ", ""),
            "シグナル終値": round(entry, 4),
            "1日後日付": "", "1日後終値": "", "1日後騰落率%": "",
            "5日後日付": "", "5日後終値": "", "5日後騰落率%": "",
            "20日後日付": "", "20日後終値": "", "20日後騰落率%": "",
            "スコアバージョン": str(row.get("スコアバージョン", "")).strip(),
            "記録日時": now_str,
            "最終更新日時": now_str,
        })
        existing.add(key)

    if new_rows:
        records = pd.concat([records, pd.DataFrame(new_rows)], ignore_index=True)

    return records


def update_forward_returns(records, histories):
    if records.empty:
        return records

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    horizons = [1, 5, 20]

    for idx, row in records.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        signal_date = str(row.get("シグナル日", "")).strip()
        hist = histories.get(ticker, pd.DataFrame())
        if not ticker or not signal_date or hist.empty:
            continue

        signal_ts = pd.Timestamp(signal_date)
        eligible = hist[hist.index >= signal_ts]
        if eligible.empty:
            continue

        # シグナル日が非営業日だった場合も、最初の営業日を基準にしない。
        # 記録時に実際の最新営業日を使うため、通常は完全一致する。
        positions = np.where(eligible.index == signal_ts)[0]
        if len(positions) == 0:
            continue

        pos0 = int(positions[0])
        entry = as_num(row.get("シグナル終値"))
        if pd.isna(entry) or entry <= 0:
            entry = float(eligible["Close"].iloc[pos0])
            records.at[idx, "シグナル終値"] = round(entry, 4)

        changed = False
        for h in horizons:
            ret_col = f"{h}日後騰落率%"
            if str(row.get(ret_col, "")).strip() not in ("", "nan"):
                continue

            target_pos = pos0 + h
            if target_pos >= len(eligible):
                continue

            target_date = eligible.index[target_pos]
            target_close = float(eligible["Close"].iloc[target_pos])
            ret = (target_close / entry - 1.0) * 100.0

            records.at[idx, f"{h}日後日付"] = target_date.strftime("%Y-%m-%d")
            records.at[idx, f"{h}日後終値"] = round(target_close, 4)
            records.at[idx, ret_col] = round(ret, 2)
            changed = True

        if changed:
            records.at[idx, "最終更新日時"] = now_str

    return records


def normalize_records_for_sort(records):
    """Google Sheets由来の文字列と新規数値行が混在しても安定して並べ替える。"""
    if records.empty:
        return records

    out = records.copy()
    out["_sort_signal_date"] = pd.to_datetime(out.get("シグナル日"), errors="coerce")
    out["_sort_practical"] = pd.to_numeric(out.get("実戦スコア"), errors="coerce")
    out["_sort_ticker"] = out.get("Ticker", pd.Series("", index=out.index)).astype(str)

    out = out.sort_values(
        ["_sort_signal_date", "_sort_practical", "_sort_ticker"],
        ascending=[False, False, True],
        na_position="last",
    ).reset_index(drop=True)
    return out.drop(columns=["_sort_signal_date", "_sort_practical", "_sort_ticker"], errors="ignore")


def build_summary(records):
    if records.empty:
        return pd.DataFrame()

    work = records.copy()
    for h in [1, 5, 20]:
        work[f"{h}日後騰落率%"] = pd.to_numeric(work[f"{h}日後騰落率%"], errors="coerce")
    work["実戦スコア"] = pd.to_numeric(work["実戦スコア"], errors="coerce")

    groups = [("ALL", work)]
    if "主力シグナル" in work.columns:
        for name, g in work.groupby("主力シグナル", dropna=False):
            label = str(name).strip() or "不明"
            groups.append((label, g))

    rows = []
    for label, g in groups:
        row = {
            "区分": label,
            "登録件数": int(len(g)),
            "平均実戦スコア": round(float(g["実戦スコア"].mean()), 1) if g["実戦スコア"].notna().any() else "",
        }
        for h in [1, 5, 20]:
            c = f"{h}日後騰落率%"
            valid = g[c].dropna()
            row[f"{h}日後件数"] = int(len(valid))
            row[f"{h}日後平均%"] = round(float(valid.mean()), 2) if len(valid) else ""
            row[f"{h}日後中央値%"] = round(float(valid.median()), 2) if len(valid) else ""
            row[f"{h}日後勝率%"] = round(float((valid > 0).mean() * 100), 1) if len(valid) else ""
        rows.append(row)

    return pd.DataFrame(rows)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    current = read_sheet(sh, "4系統サマリー")
    records = ensure_record_columns(read_sheet(sh, TRACK_SHEET))

    # 株価取得は「既存の検証銘柄 + 今日見るべき銘柄」だけに限定する。
    # 4系統サマリー全銘柄を毎回取得しないことで、yfinance負荷と失敗率を下げる。
    tickers = set(records.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
    flagged_current = pd.DataFrame()
    if not current.empty:
        try:
            flagged_current = current[today_watch_mask(current)].copy()
            tickers.update(flagged_current.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
        except Exception as e:
            print(f"::warning::今日見るべき抽出に失敗: {type(e).__name__}: {e}", flush=True)
    tickers.discard("")
    tickers.discard("nan")

    if not records.empty and "シグナル日" in records.columns:
        dates = pd.to_datetime(records["シグナル日"], errors="coerce").dropna()
        start_date = (dates.min() - pd.Timedelta(days=10)).date() if not dates.empty else (datetime.now(JST).date() - timedelta(days=400))
    else:
        start_date = datetime.now(JST).date() - timedelta(days=400)

    print(f"[実戦検証] 対象Ticker数: {len(tickers)} / 株価取得開始: {start_date}", flush=True)
    histories = download_histories(sorted(tickers), start_date)
    print(f"[実戦検証] 株価取得成功: {sum(1 for v in histories.values() if v is not None and not v.empty)}", flush=True)

    before = len(records)
    try:
        records = add_new_signals(records, current, histories)
        print(f"[実戦検証] 新規登録: {len(records) - before}件", flush=True)
    except Exception as e:
        print(f"::warning::新規シグナル登録をスキップ: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    try:
        records = update_forward_returns(records, histories)
    except Exception as e:
        print(f"::warning::事後騰落率更新をスキップ: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    try:
        records = normalize_records_for_sort(records)
    except Exception as e:
        print(f"::warning::検証履歴ソートをスキップ: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    try:
        summary = build_summary(records)
    except Exception as e:
        print(f"::warning::検証サマリー集計をスキップ: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        summary = pd.DataFrame()

    # 履歴とサマリーは個別に保存。片方が失敗してももう片方は残す。
    try:
        write_sheet(sh, TRACK_SHEET, records)
    except Exception as e:
        print(f"::warning::実戦検証シート保存失敗: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    try:
        write_sheet(sh, SUMMARY_SHEET, summary)
    except Exception as e:
        print(f"::warning::実戦検証サマリー保存失敗: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    new_today = 0
    if not current.empty:
        new_today = int(today_watch_mask(current).sum())
    print(f"実戦検証更新: 累計{len(records)}件 / 現在条件該当{new_today}件", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::warning::実戦検証の更新を中断しました: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        # 実戦検証は補助機能。コアスキャンを赤判定にしない。
        raise SystemExit(0)
