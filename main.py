import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import jobs_hourly  # noqa: E402
import jobs_morning  # noqa: E402
import monthly_report  # noqa: E402
import notifier  # noqa: E402
from fetchers import sosovalue  # noqa: E402
from runtime import Runtime, redact  # noqa: E402
from state_store import StateCorruptError, StateStore  # noqa: E402

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
ETF_HISTORY_FILE = ROOT / "history" / "etf_flows.json"
LOG_DIR = ROOT / "logs"
JST = ZoneInfo("Asia/Tokyo")
JOB_LABELS = {"morning": "朝のまとめ", "hourly": "毎時の監視", "monthly": "月次レポート"}


def _report_corrupt_state(dry_run: bool) -> None:
    """止まる理由を、送れるなら Discord にも伝える。送れなくてもログには残っている。"""
    if dry_run:
        return
    try:
        notifier.send(notifier.format_state_corrupt())
    except notifier.NotificationError as exc:
        logger.error("state.json の異常を通知できず: %s", redact(str(exc)))


def _record_sosovalue_usage(store: StateStore) -> None:
    """SoSoValue の今月の残り回数を記録する（朝のまとめの稼働状況に出す）。"""
    usage = sosovalue.last_usage()
    if usage is not None:
        logger.info("SoSoValue 今月の残り %s / %s 回",
                    usage["remaining_month"], usage["limit_month"])
        store.set(("sosovalue",), usage)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BTC Macro Monitor")
    parser.add_argument(
        "--mode",
        default="hourly",
        choices=list(JOB_LABELS),
        help="morning = 朝のまとめ、hourly = 毎時の監視（既定）、monthly = 月次レポートの送り直し",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="取得と判定だけ行い、通知の本文を画面に出す。送信も保存もしない",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="朝のまとめ・月次レポートを、送信済みでも送り直す（hourly では無視）",
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

    rt = Runtime(store, ETF_HISTORY_FILE, sender=notifier.send, dry_run=args.dry_run,
                 log_dir=LOG_DIR)
    if args.mode == "hourly" and args.force:
        logger.info("--force は朝のまとめ・月次レポートだけに効くため無視します")

    def job() -> None:
        if args.mode == "morning":
            jobs_morning.run_morning(rt, force=args.force)
        elif args.mode == "monthly":
            today = datetime.now(JST).date().isoformat()
            monthly_report.run(rt, today, force=args.force)
        else:
            jobs_hourly.run_hourly(rt)

    rt.guard(f"job_{args.mode}", JOB_LABELS[args.mode], job)
    _record_sosovalue_usage(store)

    logger.info("=== BTC Monitor done ===")
    return 1 if rt.notification_failed else 0


if __name__ == "__main__":
    sys.exit(main())
