"""EARLY_TURN最優先候補の事後成績を自動追跡する。

Google Sheets:
- EARLY検証
- EARLY検証サマリー

対象:
app.py の EARLY TOP20 と同等条件を通過した銘柄。

追跡:
- 5 / 10 / 20営業日後（約1 / 2 / 4週）の騰落率
- 各期間のMFE（最大上昇率）/ MAE（最大逆行率）
- 長期ステージ別の勝率・平均騰落率・平均MFE/MAE
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from performance_tracker import (
    as_num,
    download_histories,
    get_client,
    read_sheet,
    write_sheet,
)

TRACK_SHEET = "EARLY検証"
SUMMARY_SHEET = "EARLY検証サマリー"
SOURCE_SHEET = "週足転換H"
JST = ZoneInfo("Asia/Tokyo")

HORIZONS = {
    5: "1週",
    10: "2週",
    20: "4週",
}


def _num_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def early_top_mask(df: pd.DataFrame) -> pd.Series:
    """app.py のEARLY最優先条件と同じ判定を返す。"""
    if df.empty:
        return pd.Series(False, index=df.index)

    out = df.loc[:, ~df.columns.duplicated(keep="first")].copy()
    mask = pd.Series(True, index=out.index)

    if "週足転換タイプ" in out.columns:
        mask &= out["週足転換タイプ"].astype(str).eq("EARLY_TURN")
    else:
        return pd.Series(False, index=out.index)

    score = _num_series(out, "週足転換スコア")
    priority = _num_series(out, "初動優先スコア")
    mask &= score.ge(70)
    mask &= priority.ge(70)

    if "急落判定" in out.columns:
        mask &= ~out["急落判定"].astype(str).eq("⚠️急落ダメージ")
    if "反転確認" in out.columns:
        mask &= out["反転確認"].astype(str).eq("✅反転確認")
    if "押し目再始動判定" in out.columns:
        mask &= ~out["押し目再始動判定"].astype(str).eq("↗️押し目再始動")
    if "下降継続判定" in out.columns:
        mask &= ~out["下降継続判定"].astype(str).eq("⬇️下降継続")
    if "反転定着" in out.columns:
        mask &= out["反転定着"].astype(str).eq("✅定着確認")
    if "EARLY形状判定" in out.columns:
        mask &= out["EARLY形状判定"].astype(str).eq("✅EARLY適合")
    if "長期ハード除外" in out.columns:
        mask &= out["長期ハード除外"].astype(str).eq("✅なし")

    r4 = _num_series(out, "完成4週騰落率%")
    w20 = _num_series(out, "20週MA再奪回から週数")
    w40 = _num_series(out, "40週MA再奪回から週数")
    rr = _num_series(out, "RR目安")
    stop = _num_series(out, "実戦想定損切り距離%")

    mask &= r4.between(-8.0, 12.0, inclusive="both")
    mask &= w20.notna() & w20.ge(0) & w20.le(8) & w20.lt(999)
    mask &= w40.notna() & w40.ge(0) & w40.le(8) & w40.lt(999)
    mask &= rr.notna() & rr.ge(2.0)
    mask &= stop.notna() & stop.le(7.5)

    # TRANSITION実戦効率ゲート
    stage = out.get("長期ステージ", pd.Series("", index=out.index)).astype(str)
    stage4 = _num_series(out, "長期Stage4点")
    transition_bad = (
        stage.eq("TRANSITION")
        & stage4.ge(3)
        & (rr.lt(2.5) | stop.ge(7.0))
    )
    mask &= ~transition_bad

    return mask.fillna(False)


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    base = [
        "キー", "シグナル日", "証券コード", "Ticker", "銘柄名",
        "初動優先スコア", "初動ランク", "EARLYスコア",
        "長期ステージ", "長期Stage4点", "長期押し目点", "長期押し目分類",
        "RR目安", "実戦想定損切り距離%", "52週高値まで上値余地%",
        "シグナル終値",
    ]
    for _, label in HORIZONS.items():
        base += [
            f"{label}後日付", f"{label}後終値", f"{label}後騰落率%",
            f"{label}MFE%", f"{label}MAE%",
        ]
    base += ["記録日時", "最終更新日時"]

    if df.empty:
        return pd.DataFrame(columns=base)
    out = df.copy()
    for c in base:
        if c not in out.columns:
            out[c] = ""
    return out[base].copy()


def add_new_records(records: pd.DataFrame, current: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if current.empty:
        return records

    selected = current[early_top_mask(current)].copy()
    if selected.empty:
        return records

    existing = set(records.get("キー", pd.Series(dtype=str)).astype(str))
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    rows = []

    for _, row in selected.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        hist = histories.get(ticker, pd.DataFrame())
        if not ticker or hist.empty:
            continue

        signal_ts = hist.index[-1]
        signal_date = signal_ts.strftime("%Y-%m-%d")
        key = f"{signal_date}|{ticker}"
        if key in existing:
            continue

        entry = float(pd.to_numeric(hist["Close"], errors="coerce").dropna().iloc[-1])
        item = {
            "キー": key,
            "シグナル日": signal_date,
            "証券コード": str(row.get("証券コード", "")).strip(),
            "Ticker": ticker,
            "銘柄名": str(row.get("銘柄名", "")).strip(),
            "初動優先スコア": row.get("初動優先スコア", ""),
            "初動ランク": row.get("初動ランク", ""),
            "EARLYスコア": row.get("EARLYスコア", ""),
            "長期ステージ": row.get("長期ステージ", ""),
            "長期Stage4点": row.get("長期Stage4点", ""),
            "長期押し目点": row.get("長期押し目点", ""),
            "長期押し目分類": row.get("長期押し目分類", ""),
            "RR目安": row.get("RR目安", ""),
            "実戦想定損切り距離%": row.get("実戦想定損切り距離%", ""),
            "52週高値まで上値余地%": row.get("52週高値まで上値余地%", ""),
            "シグナル終値": round(entry, 4),
            "記録日時": now_str,
            "最終更新日時": now_str,
        }
        for _, label in HORIZONS.items():
            item[f"{label}後日付"] = ""
            item[f"{label}後終値"] = ""
            item[f"{label}後騰落率%"] = ""
            item[f"{label}MFE%"] = ""
            item[f"{label}MAE%"] = ""
        rows.append(item)
        existing.add(key)

    if rows:
        records = pd.concat([records, pd.DataFrame(rows)], ignore_index=True)
    return ensure_columns(records)


def update_records(records: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if records.empty:
        return records

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")

    for idx, row in records.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        signal_date = str(row.get("シグナル日", "")).strip()
        hist = histories.get(ticker, pd.DataFrame())
        if not ticker or not signal_date or hist.empty:
            continue

        hist = hist.copy()
        for c in ["Close", "High", "Low"]:
            if c in hist.columns:
                hist[c] = pd.to_numeric(hist[c], errors="coerce")

        signal_ts = pd.Timestamp(signal_date)
        positions = np.where(hist.index == signal_ts)[0]
        if len(positions) == 0:
            continue
        pos0 = int(positions[0])

        entry = as_num(row.get("シグナル終値"))
        if pd.isna(entry) or entry <= 0:
            entry = float(hist["Close"].iloc[pos0])
            records.at[idx, "シグナル終値"] = round(entry, 4)

        changed = False
        for days, label in HORIZONS.items():
            ret_col = f"{label}後騰落率%"
            if str(row.get(ret_col, "")).strip() not in ("", "nan"):
                continue
            target_pos = pos0 + days
            if target_pos >= len(hist):
                continue

            target_date = hist.index[target_pos]
            target_close = float(hist["Close"].iloc[target_pos])
            ret = (target_close / entry - 1.0) * 100.0

            window = hist.iloc[pos0 + 1 : target_pos + 1]
            high_series = window["High"] if "High" in window.columns else window["Close"]
            low_series = window["Low"] if "Low" in window.columns else window["Close"]
            high_max = pd.to_numeric(high_series, errors="coerce").max()
            low_min = pd.to_numeric(low_series, errors="coerce").min()
            mfe = (high_max / entry - 1.0) * 100.0 if pd.notna(high_max) else np.nan
            mae = (low_min / entry - 1.0) * 100.0 if pd.notna(low_min) else np.nan

            records.at[idx, f"{label}後日付"] = target_date.strftime("%Y-%m-%d")
            records.at[idx, f"{label}後終値"] = round(target_close, 4)
            records.at[idx, ret_col] = round(ret, 2)
            records.at[idx, f"{label}MFE%"] = round(float(mfe), 2) if pd.notna(mfe) else ""
            records.at[idx, f"{label}MAE%"] = round(float(mae), 2) if pd.notna(mae) else ""
            changed = True

        if changed:
            records.at[idx, "最終更新日時"] = now_str

    return records


def sort_records(records: pd.DataFrame) -> pd.DataFrame:
    if records.empty:
        return records
    out = records.copy()
    out["_date"] = pd.to_datetime(out["シグナル日"], errors="coerce")
    out["_score"] = pd.to_numeric(out["初動優先スコア"], errors="coerce")
    out = out.sort_values(["_date", "_score", "Ticker"], ascending=[False, False, True], na_position="last")
    return out.drop(columns=["_date", "_score"], errors="ignore").reset_index(drop=True)


def build_summary(records: pd.DataFrame) -> pd.DataFrame:
    if records.empty:
        return pd.DataFrame()

    work = records.copy()
    for _, label in HORIZONS.items():
        for suffix in ["後騰落率%", "MFE%", "MAE%"]:
            c = f"{label}{suffix}"
            work[c] = pd.to_numeric(work[c], errors="coerce")

    groups = [("ALL", work)]
    if "長期ステージ" in work.columns:
        for name, g in work.groupby("長期ステージ", dropna=False):
            label = str(name).strip() or "不明"
            groups.append((label, g))

    rows = []
    for stage, g in groups:
        row = {"長期ステージ": stage, "登録件数": int(len(g))}
        for _, label in HORIZONS.items():
            ret = g[f"{label}後騰落率%"].dropna()
            mfe = g[f"{label}MFE%"].dropna()
            mae = g[f"{label}MAE%"].dropna()
            row[f"{label}件数"] = int(len(ret))
            row[f"{label}平均%"] = round(float(ret.mean()), 2) if len(ret) else ""
            row[f"{label}中央値%"] = round(float(ret.median()), 2) if len(ret) else ""
            row[f"{label}勝率%"] = round(float((ret > 0).mean() * 100), 1) if len(ret) else ""
            row[f"{label}平均MFE%"] = round(float(mfe.mean()), 2) if len(mfe) else ""
            row[f"{label}平均MAE%"] = round(float(mae.mean()), 2) if len(mae) else ""
        rows.append(row)

    return pd.DataFrame(rows)


def main():
    gc, spreadsheet_id = get_client()
    sh = gc.open_by_key(spreadsheet_id)

    current = read_sheet(sh, SOURCE_SHEET)
    records = ensure_columns(read_sheet(sh, TRACK_SHEET))

    selected = current[early_top_mask(current)].copy() if not current.empty else pd.DataFrame()

    tickers = set(records.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
    if not selected.empty:
        tickers.update(selected.get("Ticker", pd.Series(dtype=str)).astype(str).str.strip())
    tickers.discard("")
    tickers.discard("nan")

    if not records.empty:
        dates = pd.to_datetime(records["シグナル日"], errors="coerce").dropna()
        start_date = (dates.min() - pd.Timedelta(days=10)).date() if not dates.empty else datetime.now(JST).date() - timedelta(days=500)
    else:
        start_date = datetime.now(JST).date() - timedelta(days=500)

    print(f"[EARLY検証] TOP候補={len(selected)} / 対象Ticker={len(tickers)} / 開始={start_date}", flush=True)
    histories = download_histories(sorted(tickers), start_date)

    before = len(records)
    records = add_new_records(records, current, histories)
    print(f"[EARLY検証] 新規登録={len(records)-before}件", flush=True)

    records = update_records(records, histories)
    records = sort_records(records)
    summary = build_summary(records)

    write_sheet(sh, TRACK_SHEET, records)
    write_sheet(sh, SUMMARY_SHEET, summary)
    print(f"[EARLY検証] 累計={len(records)}件 / サマリー={len(summary)}行", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::warning::EARLY検証の更新を中断: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        raise SystemExit(0)
