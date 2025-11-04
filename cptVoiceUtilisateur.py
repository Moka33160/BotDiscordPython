#!/usr/bin/env python3
import os
from datetime import datetime, UTC, timedelta
from dotenv import load_dotenv
import discord
from discord.ext import commands
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from create_db import Base, Guild, User, UserVoice  # tes modèles existants

# =======================
# Config & initialisation
# =======================
load_dotenv("config.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not DATABASE_URL:
    raise ValueError("❌ BOT_TOKEN ou DATABASE_URL manquant dans config.env")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True  # important pour on_voice_state_update
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)

# sessions vocales actives en RAM : clé = (guild_id, user_id)
active_sessions: dict[tuple[int, int], dict] = {}

# =======================
# Helpers BD optimisés
# =======================
def upsert_guild(session, guild: discord.Guild):
    session.merge(Guild(
        guild_id=guild.id,
        guild_name=guild.name,
        owner_id=guild.owner_id,
        member_count=guild.member_count,
    ))

def upsert_user(session, guild_id: int, member: discord.Member):
    roles = [r.name for r in getattr(member, "roles", []) if r.name != "@everyone"]
    session.merge(User(
        user_id=member.id,
        guild_id=guild_id,
        username=str(member),
        avatar_url=member.avatar.url if member.avatar else None,
        join_date=datetime.now(UTC),
        is_active=True,
        roles=roles
    ))

def upsert_user_voice_add_session(session, guild_id: int, user_id: int,
                                  channel_name: str, duration: timedelta):
    """
    Upsert atomique sur user_voice :
      - sessions_count += 1
      - time_in_voice += duration
      - last_voice_session = "<timestamp> (<durée>)"
      - most_used_voice_channel = channel_name (dernier)
    Utilise ON CONFLICT pour éviter tout doublon/condition de course.
    """
    now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    duration_text = f"{now_text} ({duration})"

    stmt = pg_insert(UserVoice).values(
        user_id=user_id,
        guild_id=guild_id,
        time_in_voice=duration,
        sessions_count=1,
        last_voice_session=duration_text,
        most_used_voice_channel=channel_name,
        last_update=datetime.now(UTC)
    ).on_conflict_do_update(
        index_elements=[UserVoice.user_id, UserVoice.guild_id],
        set_={
            "time_in_voice": UserVoice.time_in_voice + duration,
            "sessions_count": UserVoice.sessions_count + 1,
            "last_voice_session": duration_text,
            "most_used_voice_channel": channel_name,
            "last_update": datetime.now(UTC),
        }
    )
    session.execute(stmt)

# =======================
# Events
# =======================
@bot.event
async def on_ready():
    print(f"\n🎤 Bot vocal connecté : {bot.user}")
    print("✅ Collecte vocale prête (PostgreSQL).")
    print("=" * 60)

@bot.event
async def on_voice_state_update(member: discord.Member,
                                before: discord.VoiceState,
                                after: discord.VoiceState):
    """
    Gère 3 cas :
      - Join  : before.channel is None, after.channel is not None
      - Leave : before.channel is not None, after.channel is None
      - Switch: before.channel != after.channel (deux opérations : close + open)
    """
    if member.bot:
        return

    guild = member.guild
    guild_id = guild.id
    user_id = member.id
    key = (guild_id, user_id)

    session = SessionLocal()
    try:
        # Garantit la présence des FK (guild & user) avant toute écriture user_voice
        upsert_guild(session, guild)
        upsert_user(session, guild_id, member)
        session.commit()

        joined = before.channel is None and after.channel is not None
        left   = before.channel is not None and after.channel is None
        moved  = before.channel is not None and after.channel is not None and before.channel.id != after.channel.id

        # --- JOIN ---
        if joined:
            active_sessions[key] = {
                "start_time": datetime.now(UTC),
                "channel_id": after.channel.id,
                "channel_name": after.channel.name
            }
            # Log léger
            print(f"🎧 JOIN  | {member} → #{after.channel.name}")

        # --- LEAVE ---
        elif left:
            data = active_sessions.pop(key, None)
            if data:
                duration = datetime.now(UTC) - data["start_time"]
                upsert_user_voice_add_session(session, guild_id, user_id, data["channel_name"], duration)
                session.commit()
                print(f"🔇 LEAVE | {member} ← #{data['channel_name']} | dur: {duration}")
            else:
                # Pas d'entrée active (ex: redémarrage bot) → on ignore
                print(f"ℹ️ LEAVE sans session active pour {member} (probable restart)")

        # --- SWITCH ---
        elif moved:
            # Clôture de l'ancienne
            data = active_sessions.get(key)
            if data:
                duration = datetime.now(UTC) - data["start_time"]
                upsert_user_voice_add_session(session, guild_id, user_id, data["channel_name"], duration)
                session.commit()
                print(f"🔁 MOVE  | {member} : #{data['channel_name']} → #{after.channel.name} | dur: {duration}")
            # Démarre la nouvelle
            active_sessions[key] = {
                "start_time": datetime.now(UTC),
                "channel_id": after.channel.id,
                "channel_name": after.channel.name
            }

        # Mutedeaf change sans changement de salon → on ignore
        # (before.channel == after.channel)

    except SQLAlchemyError as e:
        session.rollback()
        print("⚠️ Erreur BD (voice):", e)
    finally:
        session.close()

# =======================
# Commandes utiles
# =======================
@bot.command()
async def vocstats(ctx):
    """Top 5 des utilisateurs (temps total vocal) dans ce serveur."""
    from sqlalchemy import select
    session = SessionLocal()
    try:
        # Jointure users/user_voice sur le serveur courant
        stmt = (
            select(User.username, UserVoice.time_in_voice, UserVoice.sessions_count)
            .join(UserVoice, (User.user_id == UserVoice.user_id) & (User.guild_id == UserVoice.guild_id))
            .where(User.guild_id == ctx.guild.id)
            .order_by(UserVoice.time_in_voice.desc())
            .limit(5)
        )
        rows = session.execute(stmt).all()
        if not rows:
            await ctx.send("Aucune activité vocale enregistrée pour ce serveur.")
            return

        lines = []
        for u, t, s in rows:
            hours = round(t.total_seconds() / 3600, 2) if isinstance(t, timedelta) else 0
            lines.append(f"- {u} → {hours} h ({s} sessions)")
        await ctx.send("🎙️ **Top voice**\n" + "\n".join(lines))
    finally:
        session.close()

@bot.command()
async def vocreset(ctx, member: discord.Member = None):
    """(Optionnel) Reset la session active en RAM si bloquée (ex: crash)."""
    target = member or ctx.author
    key = (ctx.guild.id, target.id)
    if key in active_sessions:
        active_sessions.pop(key, None)
        await ctx.send(f"Session vocale en RAM réinitialisée pour {target}.")
    else:
        await ctx.send(f"Aucune session active pour {target}.")

# =======================
# Run
# =======================
if __name__ == "__main__":
    print("🚀 Démarrage du bot vocal optimisé…")
    bot.run(BOT_TOKEN)
