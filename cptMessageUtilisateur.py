#!/usr/bin/env python3
from datetime import datetime, UTC
from time import time
import discord
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from db import SessionLocal
from create_db import Guild, User, Message

# Petits caches TTL pour éviter de réécrire trop souvent
_GUILD_CACHE = {}  # {guild_id: expiry_ts}
_USER_CACHE = {}   # {(guild_id, user_id): expiry_ts}
_TTL = 300  # 5 min

def _recent(cache: dict, key, ttl=_TTL) -> bool:
    now = time()
    exp = cache.get(key, 0)
    if exp > now:
        return True
    cache[key] = now + ttl
    return False

def upsert_guild(session, guild: discord.Guild):
    if guild is None:
        return
    if _recent(_GUILD_CACHE, guild.id):
        return
    g = Guild.__table__
    stmt = insert(g).values(
        guild_id=guild.id,
        guild_name=guild.name,
        owner_id=guild.owner_id,
        member_count=guild.member_count,
        last_update=func.now(),
    ).on_conflict_do_update(
        index_elements=[g.c.guild_id],
        set_={
            "guild_name": guild.name,
            "owner_id": guild.owner_id,
            "member_count": guild.member_count,
            "last_update": func.now(),
        }
    )
    session.execute(stmt)

def upsert_user(session, guild_id: int, member: discord.Member):
    if member is None:
        return
    key = (guild_id, member.id)
    if _recent(_USER_CACHE, key):
        return

    roles = [r.name for r in member.roles if r.name != "@everyone"]
    u = User.__table__
    stmt = insert(u).values(
        user_id=member.id,
        guild_id=guild_id,
        username=str(member),
        avatar_url=(member.avatar.url if member.avatar else None),
        join_date=(member.joined_at if getattr(member, "joined_at", None) else None),
        roles=roles,
        is_active=True,
        last_update=func.now(),
    ).on_conflict_do_update(
        index_elements=[u.c.user_id, u.c.guild_id],
        set_={
            "username": str(member),
            "avatar_url": (member.avatar.url if member.avatar else None),
            "roles": roles,
            "is_active": True,
            "last_update": func.now(),
        }
    )
    session.execute(stmt)

def add_message(session, message: discord.Message):
    msg = Message(
        user_id=message.author.id,
        guild_id=message.guild.id if message.guild else 0,
        channel_id=message.channel.id if getattr(message, "channel", None) else None,
        message_content=message.content or "",
        message_length=len(message.content or ""),
        timestamp=message.created_at if getattr(message, "created_at", None) else datetime.now(UTC),
    )
    session.add(msg)
