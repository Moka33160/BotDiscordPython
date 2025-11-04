#!/usr/bin/env python3
# bot_channel_manager.py
from __future__ import annotations
import os
from typing import Optional

import discord

# Nom du salon privé (modifiable via .env)
DEFAULT_PRIVATE_NAME = "insightcord"


def _private_channel_name(bot: discord.Client) -> str:
    env_name = os.getenv("BOT_PRIVATE_CHANNEL", "").strip()
    if env_name:
        return env_name.lower().replace(" ", "-")
    try:
        return str(bot.user.name).lower().replace(" ", "-")
    except Exception:
        return DEFAULT_PRIVATE_NAME


async def get_bot_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Retourne le salon privé du bot s’il existe, sinon None."""
    wanted = os.getenv("BOT_PRIVATE_CHANNEL", "").strip().lower()
    names_to_try = [wanted] if wanted else []
    names_to_try.append(DEFAULT_PRIVATE_NAME)
    for ch in guild.text_channels:
        if ch.name.lower() in names_to_try:
            return ch
    return None


async def ensure_private_channel(guild: discord.Guild, bot: discord.Client) -> discord.TextChannel:
    """
    Crée/maintient un salon privé visible par:
      • l’owner
      • les rôles Admin
      • le bot
    """
    name = _private_channel_name(bot)
    channel = discord.utils.get(guild.text_channels, name=name)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True
        ),
    }

    if guild.owner is not None:
        overwrites[guild.owner] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True
        )

    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=True
            )

    if channel is None:
        channel = await guild.create_text_channel(
            name=name, overwrites=overwrites,
            reason="Salon privé InsightCord (tableau de bord & rapports)"
        )
        try:
            msg = await channel.send(_channel_welcome_text(guild))
            await msg.pin()
        except Exception:
            pass
    else:
        try:
            await channel.edit(overwrites=overwrites, reason="Mise à jour permissions salon privé du bot")
        except Exception:
            pass

    return channel


def _channel_welcome_text(guild: discord.Guild) -> str:
    """Message épinglé dans le salon privé (ton amical + explications)."""
    return (
        f"👋 **Bienvenue sur votre QG InsightCord pour _{guild.name}_ !**\n\n"
        "Ici, je poste **graphiques**, **rapports** et **alertes intelligentes** — seulement visibles par l’équipe admin.\n\n"
        "### 🚀 Démarrage ultra-rapide\n"
        "1) Tape **`!rank`** pour voir ton rang (engagement + positivité)\n"
        "2) Essaye un graphique :\n"
        "```\n"
        "!chart messages --type=line --days=30 --theme=plotly_dark\n"
        "```\n"
        "3) Mets à jour un profil IA : `!insight @membre`\n\n"
        "### 📊 Graphiques disponibles\n"
        "• **Messages** par jour → `!chart messages --type=area --days=7`\n"
        "• **Top 10 actifs** → `!chart topusers --type=bar`\n"
        "• **Engagement** (score) → `!chart engagement --type=bar`\n"
        "• **Sentiment** global → `!chart sentiment --type=donut`\n"
        "_Types_: line, area, bar, column, scatter, pie, donut  ·  "
        "_Thèmes_: plotly_white, plotly_dark, ggplot2, seaborn, simple_white, presentation\n\n"
        "### 🛡️ Outils Admin\n"
        "• **Participation** : `!admin engagement` (actifs vs lurkers)\n"
        "• **Top toxique** : `!admin top-toxic` (surveillance possible)\n\n"
        "### 💡 Astuces performance\n"
        "• Ajoute `--days=30` pour accélérer le rendu\n"
        "• Évite de lancer 10 `!chart` d’un coup 😉\n\n"
        "Besoin d’aide ? Écris ici, je suis tout ouïe 🦉"
    )


def _build_admin_dm(guild: discord.Guild) -> discord.Embed:
    """DM d’accueil à l’owner : amical, clair, complet."""
    emb = discord.Embed(
        title="🦉 Bienvenue sur InsightCord !",
        description=(
            f"Merci d’avoir invité **InsightCord** sur **{guild.name}** 🙏\n\n"
            "Je t’aide à **comprendre l’activité**, **détecter la toxicité** et **animer ta communauté**. "
            "Voici un guide express pour prendre en main le bot."
        ),
        color=0x5865F2,
    )

    emb.add_field(
        name="✅ À vérifier (une seule fois)",
        value=(
            "• **Intents** activés: Server Members, Message Content, Presence, Guilds, Guild Messages, Guild Reactions, Voice States\n"
            "• **Permissions bot**: View/Send, Read History, Attach Files, Embed Links, Add Reactions, "
            "Manage Channels (pour le salon privé), Connect/View (vocaux)\n"
        ),
        inline=False,
    )

    cname = os.getenv("BOT_PRIVATE_CHANNEL", DEFAULT_PRIVATE_NAME)
    emb.add_field(
        name="🔒 Salon privé admin",
        value=(
            f"Un salon **#{cname}** a été créé/maintenu. "
            "Il reçoit automatiquement les **graphiques**, **rapports** et **alertes**. "
            "Regarde le message épinglé pour les exemples !"
        ),
        inline=False,
    )

    emb.add_field(
        name="🎛️ Commandes incontournables",
        value=(
            "• `!stats` → résumé rapide\n"
            "• `!insight [@membre]` → met à jour son profil IA\n"
            "• `!rank [@membre]` → rang basé sur engagement + positivité\n"
        ),
        inline=False,
    )

    emb.add_field(
        name="📊 Générer des graphiques (super simple)",
        value=(
            "Datasets: `messages`, `topusers`, `engagement`, `sentiment`\n"
            "Types: `line`, `area`, `bar`, `column`, `scatter`, `pie`, `donut`\n"
            "Thèmes: `plotly_white`, `plotly_dark`, `ggplot2`, `seaborn`, `simple_white`, `presentation`\n"
            "Exemples :\n"
            "```\n"
            "!chart messages --type=line --days=30 --theme=plotly_dark\n"
            "!chart topusers --type=bar\n"
            "!chart engagement --type=bar\n"
            "!chart sentiment --type=donut\n"
            "```"
        ),
        inline=False,
    )

    emb.add_field(
        name="🛡️ Admin tools",
        value=(
            "• `!admin engagement` → actifs vs lurkers (idéal pour des rôles récompenses)\n"
            "• `!admin top-toxic` → top 10 toxicité (avec surveillance ⚠️ optionnelle)\n"
        ),
        inline=False,
    )

    emb.add_field(
        name="⚙️ Astuces & bonnes pratiques",
        value=(
            "• Utilise `--days=N` pour des rendus rapides\n"
            "• L’IA applique un **cooldown** par utilisateur pour rester fluide\n"
            "• Tous les événements (messages, vocaux, réactions) nourrissent les stats"
        ),
        inline=False,
    )

    emb.set_footer(text="Besoin d’un coup de main ? Réponds à ce message et je t’accompagne 🙂")
    return emb


async def send_admin_setup_instructions(guild: discord.Guild, bot: discord.Client) -> None:
    """
    Envoie un DM chaleureux à l’owner (fallback: message dans le salon privé).
    """
    embed = _build_admin_dm(guild)

    owner = guild.owner
    sent = False
    if owner:
        try:
            dm = await owner.create_dm()
            await dm.send(embed=embed)
            sent = True
        except Exception:
            sent = False

    if not sent:
        try:
            ch = await ensure_private_channel(guild, bot)
            await ch.send(
                content=(f"👋 <@{owner.id}>" if owner else "👋 Admin"),
                embed=embed
            )
        except Exception:
            pass
