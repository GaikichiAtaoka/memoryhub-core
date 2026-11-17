# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Temporal continuity projection for model-visible dialogue context.

Stored dialogue remains immutable. This module projects timestamp metadata into a
separate system/evidence block so the model can reason about elapsed time,
civil-date relation, local daypart, and conversation/lived episode boundaries
without altering role=user content.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
import re


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_EPISODE_GAP_SECONDS = 4 * 60 * 60

_SLEEP_RE = re.compile(
    r"(?:おやすみ|寝よう|寝る(?:ね|よ|ぞ|か|わ|こと)?|寝なきゃ|寝ないと|"
    r"眠ろう|眠る|睡眠(?:に|を|へ)|布団(?:に|へ)|ゲーム(?:を)?やめた.{0,20}寝)"
)
_WAKE_RE = re.compile(
    r"(?:おはよ|おはよう|起きた|起きて|起床|目(?:が)?覚め|"
    r"寝れた|眠れた|寝てた|寝ていて)"
)


@dataclass(frozen=True)
class TemporalPoint:
    index: int
    role: str
    message_id: str
    content: str
    at: datetime
    episode: int


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def daypart(dt: datetime) -> str:
    hour = dt.astimezone(JST).hour
    if hour < 4:
        return "post_midnight"
    if hour < 8:
        return "early_morning"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "late_night"


def daypart_ja(dt: datetime) -> str:
    return {
        "post_midnight": "日付直後の深夜",
        "early_morning": "早朝",
        "morning": "朝",
        "afternoon": "昼〜午後",
        "evening": "夕方〜夜",
        "late_night": "深夜",
    }[daypart(dt)]


def _duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 48:
        return f"{hours}時間{rem_minutes:02d}分"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}日{rem_hours}時間"


def _civil_relation(at: datetime, reference: datetime) -> str:
    delta = (reference.date() - at.date()).days
    if delta == 0:
        return "same_civil_date"
    if delta == 1:
        return "previous_civil_date"
    if delta > 1:
        return f"{delta}_civil_days_ago"
    if delta == -1:
        return "next_civil_date"
    return f"{abs(delta)}_civil_days_ahead"


def _has_sleep_marker(text: str) -> bool:
    return bool(_SLEEP_RE.search(str(text or "")))


def _has_wake_marker(text: str) -> bool:
    return bool(_WAKE_RE.search(str(text or "")))


def _preview(text: str, limit: int = 64) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)] + "…"


def _episode_points(
    messages: list[dict[str, Any]],
    *,
    reference: datetime,
    gap_seconds: int = DEFAULT_EPISODE_GAP_SECONDS,
) -> tuple[list[TemporalPoint], list[dict[str, Any]]]:
    raw: list[tuple[int, dict[str, Any], datetime]] = []
    for index, row in enumerate(messages or []):
        if str(row.get("role") or "") not in {"user", "assistant"}:
            continue
        at = parse_timestamp(row.get("time"))
        if at is None or at > reference + timedelta(minutes=5):
            continue
        raw.append((index, row, at))

    points: list[TemporalPoint] = []
    boundaries: list[dict[str, Any]] = []
    episode = 0
    previous: tuple[int, dict[str, Any], datetime] | None = None
    for index, row, at in raw:
        if previous is not None:
            prev_index, prev_row, prev_at = previous
            gap = max(0.0, (at - prev_at).total_seconds())
            if gap >= gap_seconds:
                before_window = [
                    str(item[1].get("content") or "")
                    for item in raw
                    if prev_index - 2 <= item[0] <= prev_index
                ]
                after_text = str(row.get("content") or "")
                sleep_evidence = any(_has_sleep_marker(t) for t in before_window)
                wake_evidence = _has_wake_marker(after_text)
                episode += 1
                reasons = ["long_gap"]
                if sleep_evidence:
                    reasons.append("sleep_marker_before")
                if wake_evidence:
                    reasons.append("wake_marker_after")
                boundaries.append({
                    "before_index": prev_index,
                    "after_index": index,
                    "at": at,
                    "gap_seconds": int(gap),
                    "gap_text": _duration_text(gap),
                    "reasons": reasons,
                    "sleep_marker_before": sleep_evidence,
                    "wake_marker_after": wake_evidence,
                })
        points.append(TemporalPoint(
            index=index,
            role=str(row.get("role") or ""),
            message_id=str(row.get("message_id") or ""),
            content=str(row.get("content") or ""),
            at=at,
            episode=episode,
        ))
        previous = (index, row, at)

    # If the current turn starts after a long gap from the last history message,
    # current time itself begins a new episode even before the current user
    # message has been appended to session history.
    if points:
        gap_to_now = max(0.0, (reference - points[-1].at).total_seconds())
        if gap_to_now >= gap_seconds:
            sleep_evidence = any(
                _has_sleep_marker(point.content) for point in points[-3:]
            )
            episode += 1
            boundaries.append({
                "before_index": points[-1].index,
                "after_index": None,
                "at": reference,
                "gap_seconds": int(gap_to_now),
                "gap_text": _duration_text(gap_to_now),
                "reasons": ["long_gap"] + (
                    ["sleep_marker_before"] if sleep_evidence else []
                ),
                "sleep_marker_before": sleep_evidence,
                "wake_marker_after": False,
                "boundary_to_current_turn": True,
            })

    return points, boundaries


def temporal_continuity_snapshot(
    messages: list[dict[str, Any]],
    *,
    reference: datetime | None = None,
    gap_seconds: int = DEFAULT_EPISODE_GAP_SECONDS,
    max_messages: int = 18,
) -> dict[str, Any]:
    ref = (reference or datetime.now(JST)).astimezone(JST)
    points, boundaries = _episode_points(
        messages, reference=ref, gap_seconds=gap_seconds
    )
    current_episode = 0
    if points:
        current_episode = max(
            [point.episode for point in points]
            + [
                max((point.episode for point in points), default=0)
                + (1 if any(b.get("boundary_to_current_turn") for b in boundaries) else 0)
            ]
        )

    selected = points[-max(1, int(max_messages)):] if points else []
    rows: list[dict[str, Any]] = []
    for point in selected:
        age_seconds = max(0, int((ref - point.at).total_seconds()))
        episode_distance = max(0, current_episode - point.episode)
        crossed_boundaries = [
            boundary for boundary in boundaries
            if (
                (
                    boundary.get("after_index") is not None
                    and int(boundary.get("after_index")) > point.index
                )
                or boundary.get("boundary_to_current_turn")
            )
        ]
        crossed_sleep_wake = any(
            bool(boundary.get("sleep_marker_before"))
            and bool(boundary.get("wake_marker_after"))
            for boundary in crossed_boundaries
        )
        if episode_distance > 0:
            recency_class = (
                "previous_sleep_wake_episode"
                if crossed_sleep_wake
                else "previous_episode"
            )
        elif age_seconds < 15 * 60:
            recency_class = "immediate_same_episode"
        elif age_seconds < 2 * 60 * 60:
            recency_class = "recent_same_episode"
        else:
            recency_class = "earlier_same_episode"

        rows.append({
            "index": point.index,
            "role": point.role,
            "message_id": point.message_id,
            "at": point.at.isoformat(timespec="seconds"),
            "daypart": daypart(point.at),
            "daypart_ja": daypart_ja(point.at),
            "age_seconds": age_seconds,
            "age_text": _duration_text(age_seconds),
            "civil_relation": _civil_relation(point.at, ref),
            "episode": point.episode,
            "episode_relation": (
                "current_episode"
                if episode_distance == 0
                else f"previous_episode_{episode_distance}"
            ),
            "recency_class": recency_class,
            "crossed_sleep_wake_boundary": crossed_sleep_wake,
            "preview": _preview(point.content),
        })

    visible_indices = {row["index"] for row in rows}
    visible_boundaries = [
        boundary for boundary in boundaries
        if (
            boundary.get("after_index") in visible_indices
            or boundary.get("before_index") in visible_indices
            or boundary.get("boundary_to_current_turn")
        )
    ]

    return {
        "reference_now": ref.isoformat(timespec="seconds"),
        "timezone": "Asia/Tokyo",
        "current_daypart": daypart(ref),
        "current_daypart_ja": daypart_ja(ref),
        "current_civil_date": ref.date().isoformat(),
        "current_episode": current_episode,
        "episode_gap_threshold_seconds": int(gap_seconds),
        "messages": rows,
        "boundaries": visible_boundaries,
    }


def render_temporal_continuity_context(
    messages: list[dict[str, Any]],
    *,
    reference: datetime | None = None,
    gap_seconds: int = DEFAULT_EPISODE_GAP_SECONDS,
    max_messages: int = 18,
) -> str:
    snap = temporal_continuity_snapshot(
        messages,
        reference=reference,
        gap_seconds=gap_seconds,
        max_messages=max_messages,
    )
    lines = [
        "[TEMPORAL CONTINUITY MAP]",
        "source: dialogue timestamp metadata and deterministic elapsed-time projection",
        "message_role: system",
        "instruction_authority: temporal_evidence_only",
        f"reference_now: {snap['reference_now']}",
        f"timezone: {snap['timezone']}",
        f"current_civil_date: {snap['current_civil_date']}",
        f"current_daypart: {snap['current_daypart']} ({snap['current_daypart_ja']})",
        f"current_episode: {snap['current_episode']}",
        "interpretation:",
        "- 履歴上で隣り合う発言でも、実時間で近いとは限りません。相対表現は発言順ではなく age / recency / episode を根拠に判断してください。",
        "- recency=previous_episode / previous_sleep_wake_episode の履歴を、あなた自身から『さっき』『先ほど』とは呼ばないでください。『前回』『前の会話区切り』『約N時間前』など実時間に合う表現を使ってください。『昨夜』は当時の暦日・時間帯が本当に合う場合に限り使用してください。",
        "- recency=immediate_same_episode は数分程度の同一episodeです。この範囲なら『さっき』『先ほど』を自然に使えます。",
        "- historical message内の『今日』『昨日』『さっき』『明日』は、その発言時点から見た相対語です。現在時点へそのまま持ち越さないでください。",
        "- civil_date_relation と episode_relation は別物です。同じ暦日でも、長い空白や睡眠前後を跨いだ previous_episode になり得ます。",
        "- current_daypart は現在時刻から機械的に算出した時間帯です。過去の深夜会話に引きずられて現在の時間帯を上書きしないでください。",
        "- sleep_marker_before / wake_marker_after は会話上の証拠です。両方がなければ実際に睡眠したと断定せず、episode境界として扱ってください。",
        "- ユーザーが生活上の区切りを『昨日』等と明示した場合、その主観的な日区切りを civil date だけで否定しないでください。",
        "",
        "timeline:",
    ]
    boundary_by_after = {
        boundary.get("after_index"): boundary
        for boundary in snap.get("boundaries") or []
        if boundary.get("after_index") is not None
    }
    for row in snap.get("messages") or []:
        boundary = boundary_by_after.get(row["index"])
        if boundary:
            lines.append(
                "--- episode boundary: "
                f"gap={boundary.get('gap_text')} "
                f"reasons={','.join(boundary.get('reasons') or [])}"
            )
        at_compact = str(row["at"]).replace("T", " ")[:16]
        lines.append(
            "- "
            f"{row['role']} "
            f"at={at_compact} "
            f"age={row['age_text']} "
            f"civil={row['civil_relation']} "
            f"episode={row['episode_relation']} "
            f"recency={row['recency_class']} "
            f"period={row['daypart']} "
            f"preview={row['preview']}"
        )
    current_boundary = next(
        (
            boundary for boundary in snap.get("boundaries") or []
            if boundary.get("boundary_to_current_turn")
        ),
        None,
    )
    if current_boundary:
        lines.append(
            "--- current-turn episode boundary: "
            f"gap={current_boundary.get('gap_text')} "
            f"reasons={','.join(current_boundary.get('reasons') or [])}"
        )
    if not snap.get("messages"):
        lines.append("- no timestamped historical dialogue available")
    return "\n".join(lines)


def render_current_temporal_anchor(reference: datetime | None = None) -> str:
    ref = (reference or datetime.now(JST)).astimezone(JST)
    return (
        f"現在日時：{ref.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"タイムゾーン：Asia/Tokyo\n"
        f"現在の時間帯：{daypart(ref)}（{daypart_ja(ref)}）"
    )


__all__ = [
    "JST",
    "DEFAULT_EPISODE_GAP_SECONDS",
    "parse_timestamp",
    "daypart",
    "daypart_ja",
    "temporal_continuity_snapshot",
    "render_temporal_continuity_context",
    "render_current_temporal_anchor",
]
