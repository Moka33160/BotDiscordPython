#!/usr/bin/env python3
import os
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import discord
from discord.ext import commands
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from typing import List


# === Imports internes ===
from db import SessionLocal
from cptMessageUtilisateur import add_message, upsert_guild, upsert_user
from cptVoiceUtilisateur import on_voice_state_update as handle_voice_state_update
from user_activity import process_new_message, process_reaction_add
from user_engagement import process_message_engagement
from ai_analysis import analyze_and_update
from bot_channel_manager import ensure_private_channel, send_admin_setup_instructions, get_bot_channel
from charts import generate_chart
from user_profile import get_user_snapshot, format_seconds 
from admin_commands import setup_admin_commands, check_toxicity_and_alert
from rank_system import setup_rank_commands
 # << NEW


# === Config ===
load_dotenv("config.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN manquant dans config.env")

# === Bot ===
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

# === Thread pools ===
AI_EXECUTOR = ThreadPoolExecutor(max_workers=4)
CHART_EXECUTOR = ThreadPoolExecutor(max_workers=2)

# === Cooldown IA ===
USER_COOLDOWN = {}
COOLDOWN_SEC = 15

# === Déduplication des commandes (idempotence) ===
COMMAND_DEDUPE = OrderedDict()
COMMAND_DEDUPE_MAX = 500

def _mark_command_seen(msg_id: int) -> bool:
    if msg_id in COMMAND_DEDUPE:
        return False
    COMMAND_DEDUPE[msg_id] = None
    if len(COMMAND_DEDUPE) > COMMAND_DEDUPE_MAX:
        COMMAND_DEDUPE.popitem(last=False)
    return True

# ======================
# EVENTS
# ======================

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    print(f"🌍 Connecté à {len(bot.guilds)} serveurs")
    print("📡 En attente d’événements...")
    for guild in bot.guilds:
        await ensure_private_channel(guild, bot)
        await send_admin_setup_instructions(guild, bot)
        setup_admin_commands(bot)
        setup_rank_commands(bot)



@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    # Si c'est une commande -> une seule invocation, pas d'analytics doublés
    ctx = await bot.get_context(message)
    if ctx.command is not None:
        if not _mark_command_seen(message.id):
            return
        await bot.invoke(ctx)
        return

    # Messages normaux : analytics
    user_id = message.author.id
    guild_id = message.guild.id
    username = str(message.author)
    avatar_url = message.author.avatar.url if message.author.avatar else None
    channel_name = getattr(message.channel, "name", "unknown")
    content = message.content or ""
    mentioned_ids = [m.id for m in message.mentions if not m.bot]

    session = SessionLocal()
    try:
        with session.begin():
            upsert_guild(session, message.guild)
            upsert_user(session, guild_id, message.author)
            add_message(session, message)
            process_new_message(user_id, guild_id, channel_name, content, session=session)

        # IA (cooldown)
        now_ts = message.created_at.timestamp()
        key = (guild_id, user_id)
        last = USER_COOLDOWN.get(key, 0)
        if len(content) >= 6 and (now_ts - last > COOLDOWN_SEC):
            USER_COOLDOWN[key] = now_ts
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                AI_EXECUTOR,
                analyze_and_update, user_id, guild_id, content, username, avatar_url
            )
            # ✅ Vérifie et alerte si surveillé & seuil dépassé
            await check_toxicity_and_alert(bot, guild_id, user_id)


        process_message_engagement(
            author_id=user_id,
            guild_id=guild_id,
            mentioned_user_ids=mentioned_ids,
            author_name=username,
            author_avatar=avatar_url,
        )

    except SQLAlchemyError as e:
        session.rollback()
        print("⚠️ Erreur base de données :", e)
    except Exception as e:
        session.rollback()
        print("⚠️ Erreur inattendue on_message :", e)
    finally:
        session.close()

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or reaction.message.guild is None:
        return
    try:
        message_author = reaction.message.author
        guild_id = reaction.message.guild.id
        if not message_author.bot:
            process_reaction_add(user.id, message_author.id, guild_id)
    except Exception as e:
        print("⚠️ Erreur on_reaction_add :", e)

@bot.event
async def on_voice_state_update(member, before, after):
    try:
        await handle_voice_state_update(member, before, after)
    except Exception as e:
        print("⚠️ Erreur on_voice_state_update :", e)

# ======================
# COMMANDES UTILITAIRES
# ======================

@bot.command(name="stats")
async def stats(ctx):
    await ctx.send("📊 Les statistiques sont en cours de calcul...")

@bot.command(name="insight")
async def insight(ctx, member: discord.Member = None):
    member = member or ctx.author
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            AI_EXECUTOR,
            analyze_and_update, member.id, ctx.guild.id,
            "Analyse contextuelle automatique",
            str(member),
            (member.avatar.url if member.avatar else None)
        )
        await ctx.send(f"✅ Profil IA mis à jour pour {member.display_name}")
    except Exception as e:
        await ctx.send("⚠️ Impossible d’analyser pour le moment.")
        print("⚠️ Erreur commande !insight :", e)

# ======================
# USER COMMANDS (NOUVEAU)
# ======================

def _tox_emoji(val: float) -> str:
    if val is None:
        return "⚪"
    if val < 0.2:
        return "🟢"
    if val < 0.5:
        return "🟡"
    if val < 0.8:
        return "🟠"
    return "🔴"

def _sent_emoji(label: str) -> str:
    lab = (label or "neutral").lower()
    return {"positive": "😄", "negative": "☹️"}.get(lab, "😐")

def _channel_name(guild: discord.Guild, channel_id: int | None) -> str:
    if not channel_id:
        return "—"
    ch = guild.get_channel(channel_id)
    return f"#{ch.name}" if isinstance(ch, discord.TextChannel) else (ch.name if ch else str(channel_id))

def _format_topics(pairs: List[tuple]) -> str:
    if not pairs:
        return "—"
    items = [f"{k} ({v})" for k, v in pairs]
    return ", ".join(items[:5])

@bot.group(name="user", invoke_without_command=True)
async def user_group(ctx, member: discord.Member = None):
    """Snapshot rapide d'un utilisateur: !user @membre"""
    member = member or ctx.author
    data = get_user_snapshot(ctx.guild.id, member.id)

    emb = discord.Embed(
        title=f"Profil de {member.display_name}",
        description=f"Vue synthétique des activités et signaux IA",
        color=discord.Color.blurple(),
    )
    if data["user"]["avatar_url"]:
        emb.set_thumbnail(url=data["user"]["avatar_url"])

    # Ligne 1 – Volume / rang
    rank = data["messages"]["rank"]
    total = data["messages"]["rank_total"]
    rank_txt = f"#{rank} / {total}" if rank and total else "—"
    emb.add_field(
        name="Messages (30j)",
        value=f"**{data['messages']['last_30d']}**  •  7j: **{data['messages']['last_7d']}**  ({data['messages']['delta7']}%)\nAujourd’hui: **{data['messages']['today']}**\nRang: **{rank_txt}**",
        inline=False,
    )

    # Ligne 2 – Habitudes
    top_ch = data["messages"]["top_channels"]
    top_ch_txt = ", ".join(_channel_name(ctx.guild, cid) for cid, _ in top_ch) if top_ch else "—"
    peak = data["messages"]["peak_hour"]
    emb.add_field(
        name="Habitudes",
        value=f"Streak: **{data['messages']['streak_days']} jours**\nCanaux favoris: **{top_ch_txt}**\nHeure de pointe: **{peak}h**" if peak is not None else
              f"Streak: **{data['messages']['streak_days']} jours**\nCanaux favoris: **{top_ch_txt}**",
        inline=False,
    )

    # Ligne 3 – Social
    emb.add_field(
        name="Engagement",
        value=(
            f"Réactions données: **{data['engagement']['reactions_given']}** • reçues: **{data['engagement']['reactions_received']}**\n"
            f"Mentions faites: **{data['engagement']['mentions_made']}** • reçues: **{data['engagement']['mentions_received']}**\n"
            f"Score: **{round(data['engagement']['engagement_score'], 2)}**"
        ),
        inline=False,
    )

    # Ligne 4 – IA
    tox = data["ai"]["toxicity"]
    tox_badge = _tox_emoji(tox)
    sent = data["ai"]["sentiment"]
    sent_badge = _sent_emoji(sent)
    topics_txt = _format_topics(data["ai"]["topics_top"])
    style = data["ai"]["style"] or "—"
    emb.add_field(
        name="IA (contenu)",
        value=f"Toxicité: **{tox_badge} {round(tox, 2)}** • Sentiment: **{sent_badge} {sent}**\nStyle: **{style}**\nCentres d’intérêt: **{topics_txt}**",
        inline=False,
    )

    # Ligne 5 – Vocal
    emb.add_field(
        name="Vocal",
        value=f"Temps total: **{data['voice']['total_human']}** • Sessions: **{data['voice']['sessions_count']}**",
        inline=False,
    )

    roles = data["user"]["roles"] or []
    if roles:
        emb.add_field(name="Rôles", value=", ".join(roles), inline=False)

    emb.set_footer(text=f"ID: {member.id}")

    await ctx.send(embed=emb)

@user_group.command(name="activity")
async def user_activity_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    d = get_user_snapshot(ctx.guild.id, member.id)
    emb = discord.Embed(
        title=f"Activité de {member.display_name}",
        color=discord.Color.green(),
    )
    top_ch_txt = ", ".join(_channel_name(ctx.guild, cid) for cid, _ in d["messages"]["top_channels"]) or "—"
    peak = d["messages"]["peak_hour"]
    emb.add_field(name="Aujourd’hui", value=str(d["messages"]["today"]))
    emb.add_field(name="7 jours", value=f"{d['messages']['last_7d']}  ({d['messages']['delta7']}%)")
    emb.add_field(name="30 jours", value=str(d["messages"]["last_30d"]))
    emb.add_field(name="Streak (jours)", value=str(d["messages"]["streak_days"]))
    emb.add_field(name="Canaux favoris", value=top_ch_txt, inline=False)
    if peak is not None:
        emb.add_field(name="Heure de pointe", value=f"{peak}h", inline=True)
    await ctx.send(embed=emb)

@user_group.command(name="engagement")
async def user_engagement_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    e = get_user_snapshot(ctx.guild.id, member.id)["engagement"]
    emb = discord.Embed(
        title=f"Engagement de {member.display_name}",
        color=discord.Color.gold(),
    )
    emb.add_field(name="Réactions données", value=str(e["reactions_given"]))
    emb.add_field(name="Réactions reçues", value=str(e["reactions_received"]))
    emb.add_field(name="Mentions faites", value=str(e["mentions_made"]))
    emb.add_field(name="Mentions reçues", value=str(e["mentions_received"]))
    emb.add_field(name="Threads créés", value=str(e["threads_created"]))
    emb.add_field(name="Invitations envoyées", value=str(e["invitations_sent"]))
    emb.add_field(name="Jours actifs (mois)", value=str(e["active_days_in_month"]))
    emb.add_field(name="Streak social", value=str(e["streak_days"]))
    emb.add_field(name="Score d’engagement", value=str(round(e["engagement_score"], 2)), inline=False)
    await ctx.send(embed=emb)

@user_group.command(name="ai")
async def user_ai_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    ai = get_user_snapshot(ctx.guild.id, member.id)["ai"]
    tox_badge = _tox_emoji(ai["toxicity"])
    sent_badge = _sent_emoji(ai["sentiment"])
    emb = discord.Embed(
        title=f"Analyse IA — {member.display_name}",
        color=discord.Color.purple(),
    )
    emb.add_field(name="Toxicité", value=f"{tox_badge} {round(ai['toxicity'], 2)}")
    emb.add_field(name="Sentiment", value=f"{sent_badge} {ai['sentiment']}")
    emb.add_field(name="Style", value=ai["style"] or "—", inline=False)
    emb.add_field(name="Centres d’intérêt", value=_format_topics(ai["topics_top"]) or "—", inline=False)
    await ctx.send(embed=emb)

@user_group.command(name="voice")
async def user_voice_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    v = get_user_snapshot(ctx.guild.id, member.id)["voice"]
    emb = discord.Embed(
        title=f"Vocal — {member.display_name}",
        color=discord.Color.teal(),
    )
    emb.add_field(name="Temps total", value=v["total_human"])
    emb.add_field(name="Sessions", value=str(v["sessions_count"]))
    if v["most_used_voice_channel"]:
        emb.add_field(name="Salon préféré", value=v["most_used_voice_channel"])
    await ctx.send(embed=emb)

# =====================================================
#  NOUVELLE COMMANDE : !chart 
# =====================================================
from concurrent.futures import ThreadPoolExecutor
CHART_EXECUTOR = ThreadPoolExecutor(max_workers=2)

from charts import generate_chart
from bot_channel_manager import get_bot_channel

@bot.command(name="chart")
async def chart(ctx, dataset: str = "messages", *options):
    """
    Génère un graphique depuis la base en thread.
    Usage:
      !chart messages --type=line --days=30 --theme=plotly_dark
      !chart topusers --type=bar --theme=ggplot2
      !chart sentiment --type=donut
      !chart engagement --type=bar
      !chart ... --here   (force l’envoi ici)
    """
    # Valeurs par défaut
    viz = "line"
    days = None
    theme = "plotly_white"
    send_here = False


    for tok in options:
        t = tok.strip()
        if t == "--here":
            send_here = True
        elif t.startswith("--type="):
            viz = t.split("=", 1)[1]
        elif t.startswith("--days="):
            try:
                days = int(t.split("=", 1)[1])
            except Exception:
                days = None
        elif t.startswith("--theme="):
            theme = t.split("=", 1)[1]

    # Indicateur "en train d'écrire"
    async with ctx.typing():
        try:
            loop = asyncio.get_running_loop()
            # Exécute la génération d’image hors boucle asyncio
            path = await loop.run_in_executor(
                CHART_EXECUTOR,
                generate_chart,          # function
                dataset,                 # arg1
                ctx.guild.id,            # arg2
                viz,                     # arg3: viz_type
                days,                    # arg4: days
                theme,                   # arg5: template
            )

            if not path:
                await ctx.send("❌ Aucune donnée disponible pour ce graphique.")
                return

            bot_channel = await get_bot_channel(ctx.guild)

            if send_here or not bot_channel:
                # Envoie dans le canal courant
                await ctx.send(
                    f"📊 **{dataset}** — type: `{viz}` "
                    + (f"(sur {days} jours) " if days else "")
                    + f"thème: `{theme}`",
                    file=discord.File(path)
                )
            else:
                # Envoie dans le salon privé + accusé ici
                await bot_channel.send(
                    f"📊 **{dataset}** — type: `{viz}` "
                    + (f"(sur {days} jours) " if days else "")
                    + f"thème: `{theme}`",
                    file=discord.File(path)
                )
                await ctx.send(f"📤 Graphique envoyé dans {bot_channel.mention} (salon privé admin). Ajoute `--here` pour l’avoir ici.")
        except Exception as e:
            print("⚠️ Erreur commande !chart :", e)
            await ctx.send("❌ Erreur pendant la génération du graphique. Vérifie que `matplotlib` **ou** `plotly`+`kaleido` sont installés.")

    if not _mark_command_seen(ctx.message.id):
        return

    viz = "line"
    days = None
    theme = "plotly_white"

    for tok in options:
        t = tok.strip()
        if t.startswith("--type="):
            viz = t.split("=", 1)[1]
        elif t.startswith("--days="):
            try:
                days = int(t.split("=", 1)[1])
            except Exception:
                days = None
        elif t.startswith("--theme="):
            theme = t.split("=", 1)[1]

    try:
        async with ctx.typing():
            loop = asyncio.get_running_loop()
            path = await loop.run_in_executor(
                CHART_EXECUTOR,
                generate_chart,
                dataset,
                ctx.guild.id,
                viz,
                days,
                theme,
            )
        if not path:
            await ctx.send("❌ Aucune donnée disponible pour ce graphique.")
            return

        bot_channel = await get_bot_channel(ctx.guild)
        target = bot_channel or ctx
        await target.send(
            f"📊 **{dataset}** — type: `{viz}`  "
            + (f"(sur {days} jours) " if days else "")
            + f"thème: `{theme}`",
            file=discord.File(path)
        )
    except Exception as e:
        print("⚠️ Erreur commande !chart :", e)
        await ctx.send("❌ Erreur pendant la génération du graphique. Vérifie que `plotly`, `kaleido` **ou** `matplotlib` sont installés.")

if __name__ == "__main__":
    print("🚀 Lancement du bot InsightCord...")
    bot.run(BOT_TOKEN)
