#!/usr/bin/env python3
from __future__ import annotations
from datetime import datetime, UTC, date, timedelta

from sqlalchemy import func, and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from create_db import (
    User,
    UserActivity,
    UserEngagement,
    UserMessageDaily,
)

# -- Helpers ------------------------------------------------

def _ensure_user(session, user_id: int, guild_id: int, username: str | None = None, avatar_url: str | None = None):
    """
    S'assure qu'une ligne existe dans 'users' (FK-safe pour user_engagement).
    Ne touche pas aux roles/join_date ici (léger).
    """
    u = session.query(User).filter_by(user_id=user_id, guild_id=guild_id).first()
    if u is None:
        session.add(User(
            user_id=user_id,
            guild_id=guild_id,
            username=username or "Unknown#0000",
            avatar_url=avatar_url,
            is_active=True,
        ))
        session.flush()

def _compute_active_days_and_streak(session, user_id: int, guild_id: int, today: date) -> tuple[int, int]:
    """
    Calcule:
      - active_days_in_month : nb de jours du mois courant où count > 0
      - streak_days : nb de jours consécutifs jusqu'à 'today' avec activité
    À partir de la table normalisée 'user_message_daily'.
    """
    start_month = today.replace(day=1)
    rows = (
        session.query(UserMessageDaily.day, UserMessageDaily.count)
        .filter(
            UserMessageDaily.user_id == user_id,
            UserMessageDaily.guild_id == guild_id,
            UserMessageDaily.day >= start_month,
            UserMessageDaily.day <= today,
            UserMessageDaily.count > 0,
        )
        .all()
    )
    active_days = len(rows)
    days_set = {r.day for r in rows}

    # streak rétrograde depuis today
    streak = 0
    d = today
    while d in days_set:
        streak += 1
        d = d - timedelta(days=1)

    return active_days, streak

def _safe_activity(session, user_id: int, guild_id: int) -> tuple[int, int, int]:
    """Retourne (messages, reaction_count, received_reactions) à partir de user_activity si existant."""
    ua = session.query(UserActivity).filter_by(user_id=user_id, guild_id=guild_id).first()
    if ua is None:
        return 0, 0, 0
    return int(ua.message_count or 0), int(ua.reaction_count or 0), int(ua.received_reactions or 0)

def _engagement_score(messages: int, reactions_made: int, reactions_received: int,
                      active_days: int, streak: int) -> float:
    """
    Formule simple (ajuste à ta guise):
      - messages: 0.5
      - reactions_received: 1.0
      - reactions_made: 0.2
      - active_days: 3
      - streak: 2
    """
    return (
        messages * 0.5
        + reactions_received * 1.0
        + reactions_made * 0.2
        + active_days * 3.0
        + streak * 2.0
    )

# -- API à appeler depuis main.py --------------------------

def process_message_engagement(
    author_id: int,
    guild_id: int,
    mentioned_user_ids: list[int] | None = None,
    author_name: str | None = None,
    author_avatar: str | None = None,
):
    """
    Met à jour l'engagement communautaire lors d'un message:
      - author: mentions_made += len(mentions)
      - mentioned: mentions_received += 1
      - recalcul de active_days_in_month, streak_days, engagement_score pour l'auteur
    """
    mentioned_user_ids = mentioned_user_ids or []
    session = SessionLocal()
    try:
        now = datetime.now(UTC)
        today = now.date()

        # FK-safe
        _ensure_user(session, author_id, guild_id, author_name, author_avatar)
        for mid in mentioned_user_ids:
            _ensure_user(session, mid, guild_id)

        # ---- Auteur : calcule métriques dérivées
        messages, react_made, react_recv = _safe_activity(session, author_id, guild_id)
        active_days, streak = _compute_active_days_and_streak(session, author_id, guild_id, today)
        score = _engagement_score(messages, react_made, react_recv, active_days, streak)

        ue = UserEngagement.__table__
        # UPSERT auteur (mentions_made += n, + dérivés)
        inc_made = len(mentioned_user_ids)

        stmt_author = insert(ue).values(
            user_id=author_id,
            guild_id=guild_id,
            mentions_made=inc_made,
            mentions_received=0,
            threads_created=0,
            invitations_sent=0,
            active_days_in_month=active_days,
            streak_days=streak,
            engagement_score=score,
            last_update=now,
        ).on_conflict_do_update(
            index_elements=[ue.c.user_id, ue.c.guild_id],
            set_={
                "mentions_made": ue.c.mentions_made + inc_made,
                "active_days_in_month": active_days,
                "streak_days": streak,
                "engagement_score": score,
                "last_update": func.now(),
            }
        )
        session.execute(stmt_author)

        # ---- Chaque mentionné : +1 received
        if mentioned_user_ids:
            for mid in mentioned_user_ids:
                stmt_m = insert(ue).values(
                    user_id=mid,
                    guild_id=guild_id,
                    mentions_made=0,
                    mentions_received=1,
                    threads_created=0,
                    invitations_sent=0,
                    # On ne recalcule pas leurs métriques ici pour rester léger
                    last_update=now,
                ).on_conflict_do_update(
                    index_elements=[ue.c.user_id, ue.c.guild_id],
                    set_={
                        "mentions_received": ue.c.mentions_received + 1,
                        "last_update": func.now(),
                    }
                )
                session.execute(stmt_m)

        session.commit()

    except SQLAlchemyError as e:
        session.rollback()
        print("⚠️ Erreur process_message_engagement (DB):", e)
    except Exception as e:
        session.rollback()
        print("⚠️ Erreur process_message_engagement:", e)
    finally:
        session.close()
