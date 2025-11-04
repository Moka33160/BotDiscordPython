#!/usr/bin/env python3
from datetime import datetime, UTC
from sqlalchemy import Numeric, literal, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from create_db import UserActivity, UserMessageDaily

def _increment_daily_counter(session, user_id: int, guild_id: int, when: datetime):
    """Incrémente le compteur journalier normalisé (table user_message_daily)."""
    umd = UserMessageDaily.__table__
    stmt = insert(umd).values(
        user_id=user_id,
        guild_id=guild_id,
        day=when.date(),
        count=1
    ).on_conflict_do_update(
        index_elements=[umd.c.user_id, umd.c.guild_id, umd.c.day],
        set_={"count": umd.c.count + 1}
    )
    session.execute(stmt)

def process_new_message(user_id: int, guild_id: int, channel_name: str, content: str, session=None):
    """
    Upsert analytique message:
      - message_count += 1
      - average_message_length (moyenne pondérée)
      - most_used_channel = dernier canal vu
      - last_message_time = now
      - + incrément du compteur journalier (table user_message_daily)
    """
    close_after = False
    if session is None:
        session = SessionLocal()
        close_after = True
    try:
        now = datetime.now(UTC)
        L = len(content or "")
        ua = UserActivity.__table__

        stmt = insert(ua).values(
            user_id=user_id,
            guild_id=guild_id,
            message_count=1,
            average_message_length=L,
            most_used_channel=channel_name,
            last_message_time=now,
            reaction_count=0,
            received_reactions=0,
            last_update=now,
        ).on_conflict_do_update(
            index_elements=[ua.c.user_id, ua.c.guild_id],
            set_={
                "message_count": ua.c.message_count + 1,
                "average_message_length": (
                    (ua.c.average_message_length * ua.c.message_count + literal(L, type_=Numeric))
                    / (ua.c.message_count + 1)
                ),
                "most_used_channel": literal(channel_name),
                "last_message_time": literal(now),
                "last_update": func.now(),
            }
        )
        session.execute(stmt)

        # compteur journalier normalisé
        _increment_daily_counter(session, user_id, guild_id, now)

        if close_after:
            session.commit()
    except SQLAlchemyError as e:
        if close_after:
            session.rollback()
        print("⚠️ Erreur upsert user_activity:", e)
    finally:
        if close_after:
            session.close()

def process_reaction_add(reactor_id: int, target_author_id: int, guild_id: int, session=None):
    """
    Upsert analytique réactions :
      - pour le réacteur: reaction_count += 1
      - pour l’auteur cible: received_reactions += 1
    """
    close_after = False
    if session is None:
        session = SessionLocal()
        close_after = True
    try:
        ua = UserActivity.__table__

        # Réacteur
        stmt_actor = insert(ua).values(
            user_id=reactor_id,
            guild_id=guild_id,
            message_count=0,
            average_message_length=0.0,
            reaction_count=1,
            received_reactions=0,
            last_update=func.now(),
        ).on_conflict_do_update(
            index_elements=[ua.c.user_id, ua.c.guild_id],
            set_={
                "reaction_count": ua.c.reaction_count + 1,
                "last_update": func.now(),
            }
        )
        session.execute(stmt_actor)

        # Auteur cible
        stmt_target = insert(ua).values(
            user_id=target_author_id,
            guild_id=guild_id,
            message_count=0,
            average_message_length=0.0,
            reaction_count=0,
            received_reactions=1,
            last_update=func.now(),
        ).on_conflict_do_update(
            index_elements=[ua.c.user_id, ua.c.guild_id],
            set_={
                "received_reactions": ua.c.received_reactions + 1,
                "last_update": func.now(),
            }
        )
        session.execute(stmt_target)

        if close_after:
            session.commit()
    except SQLAlchemyError as e:
        if close_after:
            session.rollback()
        print("⚠️ Erreur upsert reaction:", e)
    finally:
        if close_after:
            session.close()
