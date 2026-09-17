"""
Safe entrypoint for the scheduled stock scan.

This wrapper prevents a silent partial scan when the JPX universe cannot be
loaded correctly. It validates the JPX universe once, then reuses that exact
validated universe inside scan.main().
"""

import scan


MIN_EXPECTED_JPX_TICKERS = 1000


def get_validated_jpx_universe():
    tickers, name_map, diag = scan.get_jpx_tickers()

    print(f"[JPXガード] 取得診断: {diag}", flush=True)
    print(f"[JPXガード] 取得銘柄数: {len(tickers)}", flush=True)

    if len(tickers) < MIN_EXPECTED_JPX_TICKERS:
        raise RuntimeError(
            "JPX銘柄一覧の取得結果が異常です。"
            f"取得件数={len(tickers)}、最低期待件数={MIN_EXPECTED_JPX_TICKERS}。"
            "20銘柄フォールバック等の不完全な銘柄一覧ではスキャンを続行しません。"
        )

    return tickers, name_map, diag


def main():
    tickers, name_map, diag = get_validated_jpx_universe()

    # scan.main() が再度JPX取得を行うため、ここで検証済みの同一データを再利用する。
    # これにより、検証後の一時的な通信失敗で20銘柄フォールバックへ落ちることも防ぐ。
    scan.get_jpx_tickers = lambda: (tickers, name_map, diag)

    print("[JPXガード] 検証済みJPXユニバースで本スキャンを開始します。", flush=True)
    scan.main()


if __name__ == "__main__":
    main()
