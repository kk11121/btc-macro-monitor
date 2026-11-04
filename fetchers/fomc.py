import logging
import re

import requests
from bs4 import BeautifulSoup

from fetchers.validation import valid_date

logger = logging.getLogger(__name__)

BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BtcMonitor/1.0)"}

# 議事録の URL は会合日付なので、同じ日付の声明に隠れないよう種類ごとに最新を取る。
_DOCUMENT_PATTERNS = {
    "minutes": re.compile(r"/fomcminutes(\d{8})\.htm$"),
    "statement": re.compile(r"pressreleases/monetary(\d{8})a\.htm$"),
}


def _to_iso_date(raw: str) -> str | None:
    """8桁の数字を YYYY-MM-DD にする。実在しない日付や未来の日付は None。"""
    try:
        return valid_date(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    except ValueError:
        return None


def _fed_url(href: str) -> str | None:
    """Fed のサイト内のリンクだけを絶対 URL にする。それ以外は None。"""
    if href.startswith("/"):
        return BASE_URL + href
    return href if href.startswith(f"{BASE_URL}/") else None


def _latest_document(hrefs: list[str], pattern: re.Pattern) -> dict | None:
    candidates = []
    for href in hrefs:
        match = pattern.search(href)
        url = _fed_url(href) if match else None
        iso_date = _to_iso_date(match.group(1)) if url else None
        if iso_date:
            candidates.append({"url": url, "date": iso_date})
    return max(candidates, key=lambda doc: doc["date"], default=None)


def fetch_fomc_documents() -> dict:
    """
    カレンダーページから、声明と議事録それぞれの最新を返す。

    Returns:
        {"statement": {url, date} | None, "minutes": {url, date} | None}
    """
    resp = requests.get(CALENDAR_URL, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    hrefs = [link["href"] for link in soup.find_all("a", href=True)]

    documents = {
        doc_type: _latest_document(hrefs, pattern)
        for doc_type, pattern in _DOCUMENT_PATTERNS.items()
    }
    if not any(documents.values()):
        raise ValueError("FOMC calendar page has no statement or minutes links")
    return documents


def fetch_content(url: str) -> str | None:
    """
    本文を取り出す。ページを取得できなければ例外を出し、状態を進めず次回やり直す。
    取得できても本文の場所が見つからなければ None（判定不可として通知する）。
    """
    resp = requests.get(url, timeout=30, headers=HEADERS)
    resp.raise_for_status()

    # Fed のページはヘッダーに文字コードがなく、resp.text だと Latin-1 として化ける
    soup = BeautifulSoup(resp.content, "lxml")
    for selector in ["#article", ".col-xs-12.col-sm-8", "#leftText", "article", "main", "body"]:
        element = soup.select_one(selector)
        if element:
            return element.get_text(separator="\n", strip=True)
    return None
