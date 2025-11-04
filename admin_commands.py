#!/usr/bin/env python3
import asyncio
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import discord
from discord.ext import commands
from sqlalchemy import func, desc, and_, text
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from create_db import User, Message, UserEngagement, UserAIAnalysis
from bot_channel_manager import get_bot_channel

# =========================
# Monitored users (DDL auto)
# =========================
def _ensure_monitored_table() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS monitored_users (
        user_id BIGINT NOT NULL,
        guild_id BIGINT NOT NULL,
        threshold FLOAT DEFAULT 0.8,
        added_at TIMESTAMP DEFAULT NOW(),
        last_alert TIMESTAMP NULL,
        PRIMARY KEY (user_id, guild_id),
        FOREIGN KEY (user_id, guild_id) REFERENCES users(user_id, guild_id) ON DELETE CASCADE
    );
    """
    s = SessionLocal()
    try:
        s.execute(text(ddl))
        s.commit()
    finally:
        s.close()

_ensure_monitored_table()

# =========================
# Helpers
# =========================
def _fmt_pct(n: int, d: int) -> str:
    if d <= 0: return "0%"
    return f"{(n*100.0/d):.1f}%"

def _level_emoji(tox: float) -> str:
    if tox >= 0.85: return "🔴"
    if tox >= 0.60: return "🟠"
    if tox >= 0.30: return "🟡"
    return "🟢"

# =========================
# Engagement (admin)
# =========================
async def cmd_admin_engagement(ctx: commands.Context):
    guild_id = ctx.guild.id
    s = SessionLocal()
    try:
        total_users = s.query(User).filter(User.guild_id == guild_id, User.is_active.is_(True)).count()

        since_7d = datetime.utcnow() - timedelta(days=7)
        since_30d = datetime.utcnow() - timedelta(days=30)

        active_7d = s.query(Message.user_id)\
            .filter(Message.guild_id == guild_id, Message.timestamp >= since_7d)\
            .distinct().count()

        active_30d = s.query(Message.user_id)\
            .filter(Message.guild_id == guild_id, Message.timestamp >= since_30d)\
            .distinct().count()

        # Lurkers = présents mais silencieux sur 30j
        lurkers_30d = max(total_users - active_30d, 0)

        # Top 5 dernière semaine
        top_rows = (
            s.query(User.username, func.count(Message.id).label("c"))
            .join(User, and_(User.user_id == Message.user_id, User.guild_id == Message.guild_id))
            .filter(Message.guild_id == guild_id, Message.timestamp >= since_7d)
            .group_by(User.username)
            .order_by(desc("c"))
            .limit(5)
            .all()
        )
        top_txt = "\n".join([f"• **{u or 'Utilisateur'}** — {c} msg" for (u, c) in top_rows]) or "_Aucun_"

        # Moyennes simples
        total_msgs_7d = s.query(func.count(Message.id))\
            .filter(Message.guild_id == guild_id, Message.timestamp >= since_7d).scalar() or 0
        avg_per_active = (total_msgs_7d / active_7d) if active_7d > 0 else 0.0

        embed = discord.Embed(
            title="👥 Engagement du serveur (7 & 30 jours)",
            color=discord.Color.green()
        )
        embed.add_field(name="Membres actifs (7j)", value=f"{active_7d} / {total_users} ({_fmt_pct(active_7d, total_users)})", inline=True)
        embed.add_field(name="Membres actifs (30j)", value=f"{active_30d} / {total_users} ({_fmt_pct(active_30d, total_users)})", inline=True)
        embed.add_field(name="Lurkers (30j)", value=f"{lurkers_30d} ({_fmt_pct(lurkers_30d, total_users)})", inline=True)

        embed.add_field(name="Messages (7j)", value=str(total_msgs_7d), inline=True)
        embed.add_field(name="Moyenne / actif (7j)", value=f"{avg_per_active:.1f}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(name="Top 5 (7j)", value=top_txt, inline=False)
        embed.set_footer(text="Astuce: récompense les actifs avec un rôle, et relance les lurkers 😉")
        await ctx.send(embed=embed)
    except SQLAlchemyError as e:
        await ctx.send("❌ Erreur base lors du calcul d’engagement.")
        print("admin engagement error:", e)
    finally:
        s.close()

# =========================
# Top toxic + watch
# =========================
class MonitorSelect(discord.ui.Select):
    def __init__(self, options_data: List[Tuple[int, str]]):
        options = [discord.SelectOption(label=name, value=str(uid)) for uid, name in options_data]
        super().__init__(placeholder="Choisir un membre à surveiller…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        uid = int(self.values[0])
        s = SessionLocal()
        try:
            s.execute(
                text("""
                    INSERT INTO monitored_users (user_id, guild_id, threshold, added_at)
                    VALUES (:u, :g, :t, NOW())
                    ON CONFLICT (user_id, guild_id) DO UPDATE
                    SET threshold = EXCLUDED.threshold
                """),
                {"u": uid, "g": interaction.guild.id, "t": 0.8}
            )
            s.commit()
            await interaction.response.send_message(f"✅ Surveillance activée pour <@{uid}> (seuil 0.80).", ephemeral=True)
        except SQLAlchemyError as e:
            s.rollback()
            await interaction.response.send_message("❌ Impossible d’activer la surveillance.", ephemeral=True)
            print("monitor insert error:", e)
        finally:
            s.close()

class MonitorView(discord.ui.View):
    def __init__(self, options_data: List[Tuple[int, str]], timeout: int = 120):
        super().__init__(timeout=timeout)
        self.add_item(MonitorSelect(options_data))

async def cmd_admin_top_toxic(ctx: commands.Context):
    guild_id = ctx.guild.id
    s = SessionLocal()
    try:
        rows = (
            s.query(
                UserAIAnalysis.user_id,
                User.username,
                UserAIAnalysis.toxicity_level,
                UserAIAnalysis.dominant_sentiment,
                func.coalesce(UserEngagement.engagement_score, 0.0)
            )
            .join(User, and_(User.user_id == UserAIAnalysis.user_id, User.guild_id == UserAIAnalysis.guild_id))
            .outerjoin(UserEngagement, and_(UserEngagement.user_id == UserAIAnalysis.user_id,
                                            UserEngagement.guild_id == UserAIAnalysis.guild_id))
            .filter(UserAIAnalysis.guild_id == guild_id)
            .order_by(desc(UserAIAnalysis.toxicity_level))
            .limit(10)
            .all()
        )
        if not rows:
            await ctx.send("Aucune donnée de toxicité pour ce serveur.")
            return

        lines = []
        options = []
        for rank, (uid, name, tox, sent, eng) in enumerate(rows, start=1):
            name = name or f"User {uid}"
            options.append((uid, name))
            lines.append(
                f"**#{rank}** {_level_emoji(tox)} **{name}** — tox: {tox:.2f} | sent: {sent or 'n/a'} | eng: {eng:.2f}"
            )

        embed = discord.Embed(
            title="🧨 Top 10 — Toxicité",
            description="\n".join(lines),
            color=discord.Color.red()
        )
        embed.set_footer(text="Sélectionne un membre à surveiller (menu ci-dessous).")
        view = MonitorView(options)
        await ctx.send(embed=embed, view=view)
    except SQLAlchemyError as e:
        await ctx.send("❌ Erreur lors du calcul du top toxique.")
        print("admin top-toxic error:", e)
    finally:
        s.close()

# =========================
# Alerte automatique (à appeler après analyse IA)
# =========================
async def check_toxicity_and_alert(bot: commands.Bot, guild_id: int, user_id: int):
    """
    Si user_id est surveillé et qu'il dépasse le seuil, envoie une alerte
    dans le salon privé du bot (ou fallback dans le contexte courant).
    Anti-spam : 2h entre deux alertes pour la même personne.
    """
    s = SessionLocal()
    try:
        row = s.execute(
            text("""
                SELECT threshold, last_alert
                FROM monitored_users
                WHERE user_id=:u AND guild_id=:g
                LIMIT 1
            """),
            {"u": user_id, "g": guild_id}
        ).mappings().first()

        if not row:
            return  # pas surveillé

        threshold = float(row["threshold"] or 0.8)
        last_alert = row["last_alert"]

        tox = s.query(UserAIAnalysis.toxicity_level)\
               .filter(UserAIAnalysis.user_id == user_id,
                       UserAIAnalysis.guild_id == guild_id)\
               .scalar()
        if tox is None:
            return

        if tox < threshold:
            return

        # anti-spam: 2h
        now = datetime.utcnow()
        if last_alert and (now - last_alert) < timedelta(hours=2):
            return

        # update last_alert
        s.execute(
            text("""
                UPDATE monitored_users
                SET last_alert = NOW()
                WHERE user_id=:u AND guild_id=:g
            """),
            {"u": user_id, "g": guild_id}
        )
        s.commit()

        # Send alert in bot private channel
        guild = bot.get_guild(guild_id)
        if not guild:
            return
        chan = await get_bot_channel(guild)
        msg = f"⚠️ **Alerte toxicité** pour <@{user_id}> — score actuel: **{tox:.2f}** (seuil: {threshold:.2f})"
        if chan:
            await chan.send(msg)
        else:
            # fallback: essaie d’envoyer au propriétaire du serveur
            try:
                owner = guild.owner
                if owner:
                    await owner.send(f"[{guild.name}] {msg}")
            except Exception:
                pass
    except SQLAlchemyError as e:
        print("check_toxicity_and_alert error:", e)
    finally:
        s.close()

# =========================
# Setup (brancher dans main)
# =========================
def setup_admin_commands(bot: commands.Bot):
    @bot.group(name="admin", invoke_without_command=True)
    async def admin_root(ctx: commands.Context):
        await ctx.send("Commandes: `!admin engagement` | `!admin top-toxic`")

    @admin_root.command(name="engagement")
    async def _eng(ctx: commands.Context):
        await cmd_admin_engagement(ctx)

    @admin_root.command(name="top-toxic")
    async def _tox(ctx: commands.Context):
        await cmd_admin_top_toxic(ctx)
