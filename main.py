import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import jobs_hourly  # noqa: E402
import jobs_morning  # noqa: E402
import notifier  # noqa: E402
from runtime import Runtime, redact  # noqa: E402
from state_store import StateCorruptError, StateStore  # noqa: E402

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
ETF_HISTORY_FILE = ROOT / "history" / "etf_flows.json"
JOB_LABELS = {"morning": "朝のまとめ", "hourly": "毎時の監視"}


def _report_corrupt_state(dry_run: bool) -> None:
    """止まる理由を、送れるなら Discord にも伝える。送れなくてもログには残っている。"""
    if dry_run:
        return
    try:
        notifier.send(notifier.format_state_corrupt())
    except notifier.NotificationError as exc:
        logger.error("state.json の異常を通知できず: %s", redact(str(exc)))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BTC Macro Monitor")
    parser.add_argument(
        "--mode",
        default="hourly",
        choices=list(JOB_LABELS),
        help="morning = 朝のまとめ、hourly = 毎時の監視（既定）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="取得と判定だけ行い、通知の本文を画面に出す。送信も保存もしない",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """終了コード：0 = 正常、1 = 通知の失敗または state.json の異常。"""
    args = _parse_args(argv)
    if args.dry_run and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.info("=== BTC Monitor start (mode: %s) ===", args.mode)

    store = StateStore(STATE_FILE, dry_run=args.dry_run)
    try:
        store.load()
    except StateCorruptError as exc:
        logger.error("実行を止めます: %s", exc)
        _report_corrupt_state(args.dry_run)
        return 1

    rt = Runtime(store, ETF_HISTORY_FILE, sender=notifier.send, dry_run=args.dry_run)
    job = jobs_morning.run_morning if args.mode == "morning" else jobs_hourly.run_hourly
    rt.guard(f"job_{args.mode}", JOB_LABELS[args.mode], lambda: job(rt))

    logger.info("=== BTC Monitor done ===")
    return 1 if rt.notification_failed else 0


if __name__ == "__main__":
    sys.exit(main())
