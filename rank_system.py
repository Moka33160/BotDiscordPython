#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Tuple

import discord
from discord.ext import commands
from sqlalchemy import func, and_
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from create_db import User, UserEngagement, UserAIAnalysis


# ────────────────────────────────────────────────────────────────────────────────
# Config rangs
# ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tier:
    name: str
    min_score: float
    color: int
    emoji: str

TIERS: List[Tier] = [
    Tier("Mythic",   90.0, 0x9b59b6, "🌌"),
    Tier("Diamond",  80.0, 0x00d2ff, "💎"),
    Tier("Platinum", 70.0, 0x7fdbff, "🔷"),
    Tier("Gold",     60.0, 0xf1c40f, "🥇"),
    Tier("Silver",   45.0, 0xbdc3c7, "🥈"),
    Tier("Bronze",    0.0, 0xcd7f32, "🥉"),
]

def pick_tier(score: float) -> Tier:
    for t in TIERS:
        if score >= t.min_score:
            return t
    return TIERS[-1]


# ────────────────────────────────────────────────────────────────────────────────
# Calcul du score de rang
# ────────────────────────────────────────────────────────────────────────────────
def _minmax_norm(values: List[float], x: float) -> float:
    """Normalisation min-max → [0, 100]. Si tous identiques, renvoie 50."""
    if not values:
        return 0.0
    vmin = min(values)
    vmax = max(values)
    if vmax <= vmin:
        return 50.0
    return max(0.0, min(100.0, (x - vmin) * 100.0 / (vmax - vmin)))

def _score_combined(eng_norm_0_100: float, tox_0_1: float) -> float:
    """Score = 70% engagement (normalisé) + 30% positivité (1 - toxicité)."""
    positivity = max(0.0, min(1.0, 1.0 - float(tox_0_1))) * 100.0
    return 0.7 * eng_norm_0_100 + 0.3 * positivity

def _progress_bar(score_0_100: float, width: int = 20) -> str:
    filled = int(round((score_0_100 / 100.0) * width))
    return "█" * filled + "░" * (width - filled)


# ────────────────────────────────────────────────────────────────────────────────
# Lecture DB + ranking
# ────────────────────────────────────────────────────────────────────────────────
def _fetch_all_profiles(guild_id: int) -> List[Tuple[int, float, float]]:
    """
    Renvoie [(user_id, engagement_score, toxicity_level)] pour tout le serveur.
    Valeurs manquantes → 0.0.
    """
    s = SessionLocal()
    try:
        rows = (
            s.query(
                User.user_id,
                func.coalesce(UserEngagement.engagement_score, 0.0),
                func.coalesce(UserAIAnalysis.toxicity_level, 0.0),
            )
            .filter(User.guild_id == guild_id)
            .outerjoin(
                UserEngagement,
                and_(UserEngagement.user_id == User.user_id,
                     UserEngagement.guild_id == User.guild_id),
            )
            .outerjoin(
                UserAIAnalysis,
                and_(UserAIAnalysis.user_id == User.user_id,
                     UserAIAnalysis.guild_id == User.guild_id),
            )
            .all()
        )
        return [(int(uid), float(eng or 0.0), float(tox or 0.0)) for (uid, eng, tox) in rows]
    finally:
        s.close()

def _username_of(guild_id: int, user_id: int) -> str:
    s = SessionLocal()
    try:
        name = s.query(User.username)\
            .filter(User.guild_id == guild_id, User.user_id == user_id)\
            .scalar()
        return name or f"User {user_id}"
    finally:
        s.close()


# ────────────────────────────────────────────────────────────────────────────────
# Commande !rank
# ────────────────────────────────────────────────────────────────────────────────
async def cmd_rank(ctx: commands.Context, member: Optional[discord.Member]) -> None:
    """!rank [@membre] — calcule le rang sur 100 + tier."""
    target = member or ctx.author
    guild_id = ctx.guild.id
    user_id = target.id

    try:
        profiles = _fetch_all_profiles(guild_id)
        if not profiles:
            await ctx.send(" Aucune donnée trouvée pour ce serveur.")
            return

        # Normalisation engagement sur la population
        eng_values = [p[1] for p in profiles]
        eng_map = {p[0]: p[1] for p in profiles}
        tox_map = {p[0]: p[2] for p in profiles}

        eng_user = float(eng_map.get(user_id, 0.0))
        tox_user = float(tox_map.get(user_id, 0.0))

        eng_norm = _minmax_norm(eng_values, eng_user)
        score_user = _score_combined(eng_norm, tox_user)
        tier = pick_tier(score_user)

        # Classement global sur le score combiné (optionnel mais sympa)
        combined_all = []
        for uid, eng, tox in profiles:
            eng_n = _minmax_norm(eng_values, eng)
            combined_all.append((uid, _score_combined(eng_n, tox)))

        combined_all.sort(key=lambda x: x[1], reverse=True)
        position = 1 + next((i for i, (uid, sc) in enumerate(combined_all) if uid == user_id), len(combined_all)-1)
        total = len(combined_all)

        # Champs explicatifs
        positivity_pct = max(0.0, min(100.0, (1.0 - tox_user) * 100.0))
        next_tier = None
        for t in TIERS:
            if score_user < t.min_score:
                next_tier = t
        # next tier logique = le plus petit tier dont min_score > score_user
        if next_tier is None:
            # déjà au top : Mythic
            next_hint = "Tu es au rang maximal. 🔥"
        else:
            delta = max(0.0, next_tier.min_score - score_user)
            next_hint = f"{next_tier.emoji} Prochain rang **{next_tier.name}** à **+{delta:.1f}** points."

        # Embed
        emb = discord.Embed(
            title=f"{tier.emoji} Rang de {target.display_name}",
            color=tier.color,
            description=_progress_bar(score_user),
        )
        emb.add_field(name="Score", value=f"**{score_user:.1f}** / 100", inline=True)
        emb.add_field(name="Tier", value=f"**{tier.name}**", inline=True)
        emb.add_field(name="Classement", value=f"**#{position}** sur **{total}**", inline=True)

        eng_line = f"{eng_user:.2f} (normalisé: {eng_norm:.1f}/100)"
        tox_line = f"{tox_user:.2f} → positivité {positivity_pct:.0f}%"
        emb.add_field(name="Engagement", value=eng_line, inline=True)
        emb.add_field(name="Toxicité", value=tox_line, inline=True)
        emb.add_field(name="Prochain rang", value=next_hint, inline=False)

        # Thumbnail & footer
        if target.avatar:
            emb.set_thumbnail(url=target.avatar.url)
        emb.set_footer(text="Score = 70% engagement + 30% positivité (1 - toxicité)")

        await ctx.send(embed=emb)

    except SQLAlchemyError as e:
        print("rank error:", e)
        await ctx.send(" Erreur lors du calcul du rang. Réessaie plus tard.")


# ────────────────────────────────────────────────────────────────────────────────
# Setup (à appeler depuis main.py)
# ────────────────────────────────────────────────────────────────────────────────
def setup_rank_commands(bot: commands.Bot):
    @bot.command(name="rank")
    async def _rank(ctx: commands.Context, member: Optional[discord.Member] = None):
        await cmd_rank(ctx, member)
