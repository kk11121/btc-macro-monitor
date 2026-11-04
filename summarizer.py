"""FOMC議事録の要約。Claude を使うのはここだけ（年8回）。"""

import json
import logging

import anthropic

import config
from rules import BEARISH, BULLISH, NEUTRAL, UNKNOWN, Verdict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "あなたはビットコイン短期トレーダー向けのアナリストです。"
    "FOMC議事録を読み、金融政策の見通しがBTCに与える短期的な影響を"
    "強気・中立・弱気で判定し、根拠と合わせて日本語200字以内で要約してください。"
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "judgment": {"type": "string", "enum": ["強気", "中立", "弱気"]},
        "summary": {"type": "string"},
    },
    "required": ["judgment", "summary"],
    "additionalProperties": False,
}

_VERDICTS = {v.label: v for v in (BULLISH, NEUTRAL, BEARISH)}


def _result(verdict: Verdict, summary: str) -> dict:
    return {"judgment": verdict.label, "emoji": verdict.emoji, "summary": summary}


def _unknown(reason: str) -> dict:
    return _result(UNKNOWN, f"要約できませんでした（{reason}）。本文を確認してください")


def _parse(response: anthropic.types.Message) -> dict:
    if response.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next(block.text for block in response.content if block.type == "text")
    parsed = json.loads(text)
    verdict = _VERDICTS.get(parsed.get("judgment"))
    if verdict is None:
        raise ValueError(f"unexpected judgment: {parsed.get('judgment')}")
    return _result(verdict, parsed.get("summary", "")[: config.SUMMARY_MAX_CHARS])


def summarize_minutes(content: str | None, meeting_date: str) -> dict:
    """議事録を要約する。失敗時は「⚪ 判定不可」を返し、例外は外に出さない。"""
    if not content:
        return _unknown("本文を取得できず")
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=config.SUMMARY_MODEL,
            max_tokens=config.SUMMARY_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"FOMC議事録（{meeting_date} の会合）\n\n{content}",
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        )
        return _parse(response)
    except Exception as exc:  # 要約の失敗は通知を止めず「判定不可」として届ける
        logger.error("議事録の要約に失敗: %s", type(exc).__name__)
        return _unknown("Claude API の失敗")
