#!/usr/bin/env python3
from __future__ import annotations
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, and_, desc, cast, Date
from db import SessionLocal
from create_db import (
    User,
    UserActivity,
    UserEngagement,
    UserAIAnalysis,
    UserMessageDaily,
    UserVoice,
    Message,
)

UTC = timezone.utc

# ---------- Helpers temps / formats ----------

def _today() -> date:
    # on garde la logique UTC pour la cohérence BDD
    return datetime.now(UTC).date()

def _sum_umd(session, user_id: int, guild_id: int, d0: date, d1: date) -> int:
    q = (
        session.query(func.coalesce(func.sum(UserMessageDaily.count), 0))
        .filter(
            UserMessageDaily.user_id == user_id,
            UserMessageDaily.guild_id == guild_id,
            UserMessageDaily.day >= d0,
            UserMessageDaily.day <= d1,
        )
    )
    return int(q.scalar() or 0)

def _count_messages_fallback(session, user_id: int, guild_id: int, d0: date, d1: date) -> int:
    q = (
        session.query(func.count(Message.id))
        .filter(
            Message.user_id == user_id,
            Message.guild_id == guild_id,
            func.date(Message.timestamp) >= d0,
            func.date(Message.timestamp) <= d1,
        )
    )
    return int(q.scalar() or 0)

def _sum_msgs(session, user_id: int, guild_id: int, days: int) -> int:
    if days <= 0:
        return 0
    end = _today()
    start = end - timedelta(days=days - 1)
    s = _sum_umd(session, user_id, guild_id, start, end)
    if s == 0:
        # fallback si UMD vide
        s = _count_messages_fallback(session, user_id, guild_id, start, end)
    return s

def _streak_days(session, user_id: int, guild_id: int, max_lookback: int = 180) -> int:
    """Compte les jours consécutifs (en partant d’aujourd’hui) avec au moins 1 message."""
    today = _today()
    # On récupère toutes les dates non nulles récentes
    rows = (
        session.query(UserMessageDaily.day)
        .filter(
            UserMessageDaily.user_id == user_id,
            UserMessageDaily.guild_id == guild_id,
            UserMessageDaily.day >= today - timedelta(days=max_lookback),
            UserMessageDaily.count > 0,
        )
        .order_by(UserMessageDaily.day.desc())
        .all()
    )
    days = set(d for (d,) in rows)
    # Si UMD vide, fallback messages
    if not days:
        rows = (
            session.query(func.date(Message.timestamp))
            .filter(
                Message.user_id == user_id,
                Message.guild_id == guild_id,
                func.date(Message.timestamp) >= today - timedelta(days=max_lookback),
            )
            .group_by(func.date(Message.timestamp))
            .all()
        )
        days = set(d for (d,) in rows)

    streak = 0
    cur = today
    while cur in days:
        streak += 1
        cur -= timedelta(days=1)
    return streak

def _rank_and_total_messages(session, guild_id: int, user_id: int) -> Tuple[Optional[int], int]:
    """Calcule le rang de l’utilisateur sur le volume de messages (UserActivity.message_count)."""
    # Récup valeur de l’utilisateur
    ua = (
        session.query(UserActivity.message_count)
        .filter(UserActivity.guild_id == guild_id, UserActivity.user_id == user_id)
        .first()
    )
    total_users = session.query(func.count(UserActivity.user_id)).filter(UserActivity.guild_id == guild_id).scalar() or 0
    if not ua or total_users == 0:
        return None, total_users
    my_count = int(ua[0] or 0)
    # nombre d’utilisateurs avec plus de messages
    above = (
        session.query(func.count(UserActivity.user_id))
        .filter(UserActivity.guild_id == guild_id, UserActivity.message_count > my_count)
        .scalar()
        or 0
    )
    rank = int(above) + 1
    return rank, int(total_users)

def _top_channels(session, user_id: int, guild_id: int, limit: int = 3) -> List[Tuple[int, int]]:
    rows = (
        session.query(Message.channel_id, func.count(Message.id))
        .filter(Message.user_id == user_id, Message.guild_id == guild_id)
        .group_by(Message.channel_id)
        .order_by(desc(func.count(Message.id)))
        .limit(limit)
        .all()
    )
    out = []
    for cid, cnt in rows:
        if cid is None:
            continue
        out.append((int(cid), int(cnt)))
    return out

def _hours_histogram(session, user_id: int, guild_id: int) -> Dict[int, int]:
    rows = (
        session.query(func.extract("hour", Message.timestamp), func.count(Message.id))
        .filter(Message.user_id == user_id, Message.guild_id == guild_id)
        .group_by(func.extract("hour", Message.timestamp))
        .all()
    )
    return {int(h): int(c) for (h, c) in rows}

def format_seconds(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

# ---------- Public API: snapshot ----------

def get_user_snapshot(guild_id: int, user_id: int) -> Dict:
    """Retourne un dict complet pour construire un embed riche 'profil utilisateur'."""
    session = SessionLocal()
    try:
        user = (
            session.query(User)
            .filter(User.guild_id == guild_id, User.user_id == user_id)
            .first()
        )

        ua = (
            session.query(UserActivity)
            .filter(UserActivity.guild_id == guild_id, UserActivity.user_id == user_id)
            .first()
        )

        ue = (
            session.query(UserEngagement)
            .filter(UserEngagement.guild_id == guild_id, UserEngagement.user_id == user_id)
            .first()
        )

        ai = (
            session.query(UserAIAnalysis)
            .filter(UserAIAnalysis.guild_id == guild_id, UserAIAnalysis.user_id == user_id)
            .first()
        )

        uv = (
            session.query(UserVoice)
            .filter(UserVoice.guild_id == guild_id, UserVoice.user_id == user_id)
            .first()
        )

        # Messages (aujourd’hui / 7j / 30j)
        msgs_today = _sum_msgs(session, user_id, guild_id, 1)
        msgs_7 = _sum_msgs(session, user_id, guild_id, 7)
        msgs_30 = _sum_msgs(session, user_id, guild_id, 30)

        # Delta 7j vs 7j précédents
        today = _today()
        sum_curr = _sum_umd(session, user_id, guild_id, today - timedelta(days=6), today)
        sum_prev = _sum_umd(session, user_id, guild_id, today - timedelta(days=13), today - timedelta(days=7))
        if sum_curr == 0 and sum_prev == 0:
            delta7 = 0.0
        elif sum_prev == 0:
            delta7 = 100.0
        else:
            delta7 = (sum_curr - sum_prev) * 100.0 / max(1, sum_prev)

        # Streak
        streak = _streak_days(session, user_id, guild_id)

        # Classement messages
        rank, total = _rank_and_total_messages(session, guild_id, user_id)

        # Top channels & heures de pointe
        top_ch = _top_channels(session, user_id, guild_id, limit=3)
        hours = _hours_histogram(session, user_id, guild_id)
        peak_hour = max(hours.items(), key=lambda kv: kv[1])[0] if hours else None

        # Réactions
        reactions_given = int(getattr(ua, "reaction_count", 0) or 0)
        reactions_recv = int(getattr(ua, "received_reactions", 0) or 0)

        # Engagement
        mentions_made = int(getattr(ue, "mentions_made", 0) or 0)
        mentions_recv = int(getattr(ue, "mentions_received", 0) or 0)
        eng_score = float(getattr(ue, "engagement_score", 0.0) or 0.0)

        # IA
        tox = float(getattr(ai, "toxicity_level", 0.0) or 0.0)
        sent = (getattr(ai, "dominant_sentiment", None) or "neutral").lower()
        topics = dict(getattr(ai, "topics_of_interest", {}) or {})
        style = getattr(ai, "communication_style", None)

        # Vocal
        total_voice_seconds = 0
        sessions_count = 0
        most_used_voice_channel = None
        if uv:
            sessions_count = int(uv.sessions_count or 0)
            most_used_voice_channel = uv.most_used_voice_channel
            # uv.time_in_voice est un INTERVAL; on tente d’en déduire des secondes
            tiv = uv.time_in_voice
            try:
                total_voice_seconds = int(tiv.total_seconds())  # type: ignore[attr-defined]
            except Exception:
                total_voice_seconds = 0

        # Rôles (liste)
        roles = []
        if user and user.roles:
            if isinstance(user.roles, list):
                roles = user.roles
            elif isinstance(user.roles, dict):
                # si stocké sous forme d’objet JSON, on tente de prendre la clé 'roles' ou les valeurs
                roles = user.roles.get("roles", list(user.roles.values())) if hasattr(user.roles, "get") else []
        roles = roles[:6]  # pas de spam

        return {
            "user": {
                "id": user_id,
                "username": getattr(user, "username", f"User {user_id}"),
                "avatar_url": getattr(user, "avatar_url", None),
                "join_date": getattr(user, "join_date", None),
                "roles": roles,
            },
            "messages": {
                "today": msgs_today,
                "last_7d": msgs_7,
                "last_30d": msgs_30,
                "streak_days": streak,
                "delta7": round(delta7, 1),
                "total_count": int(getattr(ua, "message_count", 0) or 0),
                "avg_len": float(getattr(ua, "average_message_length", 0.0) or 0.0),
                "most_used_channel": getattr(ua, "most_used_channel", None),
                "last_message_time": getattr(ua, "last_message_time", None),
                "top_channels": top_ch,  # list[(channel_id, count)]
                "peak_hour": peak_hour,
                "rank": rank,
                "rank_total": total,
            },
            "engagement": {
                "mentions_made": mentions_made,
                "mentions_received": mentions_recv,
                "reactions_given": reactions_given,
                "reactions_received": reactions_recv,
                "threads_created": int(getattr(ue, "threads_created", 0) or 0),
                "invitations_sent": int(getattr(ue, "invitations_sent", 0) or 0),
                "active_days_in_month": int(getattr(ue, "active_days_in_month", 0) or 0),
                "streak_days": int(getattr(ue, "streak_days", 0) or 0),
                "engagement_score": eng_score,
            },
            "ai": {
                "toxicity": tox,
                "sentiment": sent,
                "topics_top": sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:5],
                "style": style,
            },
            "voice": {
                "total_seconds": total_voice_seconds,
                "total_human": format_seconds(total_voice_seconds),
                "sessions_count": sessions_count,
                "most_used_voice_channel": most_used_voice_channel,
            },
        }
    finally:
        session.close()
