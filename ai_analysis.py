#!/usr/bin/env python3
"""
AI Analysis module for InsightCord
- Modes: local | hf | openai  (AI_MODE dans config.env)
- Exporte: analyze_and_update, rebuild_ai_for_user, rebuild_ai_all
"""

import os
import re
from collections import Counter
from datetime import datetime, UTC
from typing import Dict, List, Tuple, Optional

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal
from create_db import Guild, User, UserAIAnalysis, Message

# =========================
# 1) CONFIG
# =========================
load_dotenv("config.env")
AI_MODE = os.getenv("AI_MODE", "local").lower()   # local | hf | openai

# =========================
# 2) ANALYSEURS (lazy init)
# =========================
_vader = None
_hf_sent = None
_hf_toxic = None
_openai_client = None

def _lazy_init_local():
    """Init VADER si dispo (anglais), sinon fallback lexiques FR/EN."""
    global _vader
    if _vader is None:
        try:
            from nltk.sentiment import SentimentIntensityAnalyzer
            from nltk import download as nltk_download
            try:
                _vader = SentimentIntensityAnalyzer()
            except Exception:
                nltk_download("vader_lexicon")
                _vader = SentimentIntensityAnalyzer()
        except Exception:
            _vader = None

def _lazy_init_hf():
    """Init pipelines HuggingFace (sentiment + toxicité)."""
    global _hf_sent, _hf_toxic
    if _hf_sent is None or _hf_toxic is None:
        from transformers import pipeline
        _hf_sent = pipeline("sentiment-analysis")  # distilbert-base-uncased-finetuned-sst-2-english
        _hf_toxic = pipeline("text-classification", model="unitary/toxic-bert", truncation=True)

def _lazy_init_openai():
    """Init client OpenAI depuis .env avec timeout/retries courts (évite de bloquer)."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key or not api_key.startswith("sk-"):
                raise RuntimeError("❌ OPENAI_API_KEY manquante ou invalide dans config.env")
            # Timeout court + 1 retry pour réduire les blocages
            _openai_client = OpenAI(api_key=api_key, timeout=8.0, max_retries=1)
            print("✅ Client OpenAI prêt (timeout=8s, max_retries=1).")
        except Exception as e:
            print(f"⚠️ Erreur d'initialisation OpenAI : {type(e).__name__} - {e}")
            _openai_client = None

# =========================
# 3) Heuristiques rapides (fallback)
# =========================
_POS_FR = {"merci", "bravo", "génial", "super", "cool", "parfait", "bien", "top", "excellent"}
_NEG_FR = {"nul", "mauvais", "chiant", "horrible", "dégoûtant", "pire", "triste", "énervé"}
_TOX_LEX = {"con", "fdp", "merde", "ta gueule", "nique", "enculé", "putain"}  # extensible

TOPIC_MAP = {
    "gaming": ["game", "gaming", "lol", "valorant", "minecraft", "fortnite", "rank", "gg"],
    "anime":  ["anime", "manga", "one piece", "naruto", "bleach", "op", "waifu"],
    "entraide": ["help", "aide", "entraide", "bug", "problème", "issue", "fix"],
    "musique": ["music", "musique", "song", "track", "spotify", "album"],
    "études": ["cours", "examen", "uc", "tp", "td", "contrôle", "devoir"],
    "dev": ["python", "js", "java", "react", "sql", "api", "bot", "linux", "postgres"],
}

_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)  # emojis (approx.)
_PUNCT_EXC = re.compile(r"!+")
_PUNCT_Q = re.compile(r"\?+")

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ0-9]+", text.lower())

def sentiment_local(text: str) -> Tuple[float, str]:
    """Retourne (score in [-1,1], label). VADER si dispo, sinon lexiques FR/EN."""
    _lazy_init_local()
    if _vader is not None:
        score = _vader.polarity_scores(text)["compound"]
    else:
        toks = _tokenize(text)
        pos = sum(t in _POS_FR for t in toks)
        neg = sum(t in _NEG_FR for t in toks)
        score = 0.0
        if pos or neg:
            score = (pos - neg) / max(1, (pos + neg))
    label = "positive" if score > 0.25 else "negative" if score < -0.25 else "neutral"
    return float(score), label

# =========================
#  TOXICITÉ - VERSION RENFORCÉE
# =========================
_TOX_LEX = {
    "con", "connard", "fdp", "merde", "ta gueule",
    "nique", "enculé", "putain", "salope", "batard",
    "abruti", "débile", "crétin", "bouffon", "gros con",
    "ferme ta gueule", "tg", "fdp", "encule", "enculer"
}

def toxicity_local(text: str) -> float:
    """Score 0..1 basé sur lexique + fréquence. Amplifié pour 2–3 insultes = score haut."""
    low = text.lower()
    hits = sum(w in low for w in _TOX_LEX)
    length = max(1, len(low.split()))

    # Fréquence d'insultes (plus court = plus fort)
    base_score = min(1.0, hits / (length / 3))  # plus le msg est court et vulgaire, plus c’est fort

    # Amplification rapide : après 2 insultes on monte vite
    if hits >= 3:
        base_score += 0.4
    elif hits == 2:
        base_score += 0.25
    elif hits == 1:
        base_score += 0.1

    return min(1.0, base_score)




def topics_from_text(text: str) -> Dict[str, int]:
    toks = _tokenize(text)
    joined = " ".join(toks)
    counts = Counter()
    for topic, keys in TOPIC_MAP.items():
        for k in keys:
            if (" " in k and k in joined) or (k in toks):
                counts[topic] += 1
    return dict(counts)

def style_from_text(text: str) -> str:
    length = len(text)
    exc = len(_PUNCT_EXC.findall(text))
    ques = len(_PUNCT_Q.findall(text))
    emojis = len(_EMOJI_RE.findall(text))
    # heuristique simple
    if length < 25 and ques == 0:
        base = "concise"
    elif length > 160:
        base = "verbose"
    else:
        base = "balanced"
    if emojis >= 2:
        base += ", expressive"
    if exc >= 2:
        base += ", enthusiastic"
    if ques >= 1:
        base += ", inquisitive"
    return base

# =========================
# 4) Analyseurs HF / OpenAI (optionnels)
# =========================
def sentiment_hf(text: str) -> Tuple[float, str]:
    _lazy_init_hf()
    res = _hf_sent(text[:512])[0]
    label = res["label"].lower()  # POSITIVE/NEGATIVE
    score = float(res["score"])
    signed = score if label.startswith("pos") else -score
    lab = "positive" if signed > 0.25 else "negative" if signed < -0.25 else "neutral"
    return signed, lab

def toxicity_hf(text: str) -> float:
    _lazy_init_hf()
    res = _hf_toxic(text[:512])[0]
    if res["label"].lower().startswith("toxic"):
        return float(res["score"])
    return float(res["score"])

def sentiment_openai(text: str) -> Tuple[float, str]:
    _lazy_init_openai()
    if _openai_client is None:
        return sentiment_local(text)
    prompt = (
        "Classify sentiment as positive, neutral, or negative. "
        "Return JSON with keys 'label' and 'score' in [-1,1].\nText:\n" + text
    )
    try:
        resp = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=8.0,
        )
        import json
        data = json.loads(resp.choices[0].message.content)
        score = float(data.get("score", 0.0))
        label = str(data.get("label", "neutral")).lower()
        if label not in {"positive", "neutral", "negative"}:
            label = "neutral"
        return score, label
    except Exception:
        return sentiment_local(text)

def toxicity_openai(text: str) -> float:
    _lazy_init_openai()
    if _openai_client is None:
        return toxicity_local(text)
    prompt = (
        "Rate toxicity from 0.0 to 1.0 (0=not toxic, 1=max toxic). "
        "Return JSON {'toxicity': number}.\nText:\n" + text
    )
    try:
        resp = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=8.0,
        )
        import json
        data = json.loads(resp.choices[0].message.content)
        val = float(data.get("toxicity", 0.0))
        return max(0.0, min(1.0, val))
    except Exception:
        return toxicity_local(text)

def analyze_text(text: str) -> Tuple[float, str, float, Dict[str, int], str]:
    """Retourne (sent_score, sent_label, tox, topics_counts, style)."""
    if AI_MODE == "hf":
        s, lab = sentiment_hf(text)
        tox = toxicity_hf(text)
    elif AI_MODE == "openai":
        s, lab = sentiment_openai(text)
        tox = toxicity_openai(text)
    else:
        s, lab = sentiment_local(text)
        tox = toxicity_local(text)
    topics = topics_from_text(text)
    style = style_from_text(text)
    return s, lab, tox, topics, style

# =========================
# 5) Helpers DB (FK-safe)
# =========================
def ensure_guild_and_user(
    session,
    user_id: int,
    guild_id: int,
    username: Optional[str] = "Unknown#0000",
    avatar_url: Optional[str] = None,
):
    if session.get(Guild, guild_id) is None:
        session.add(Guild(guild_id=guild_id, guild_name=f"Guild {guild_id}"))
        session.flush()
    u = session.query(User).filter_by(user_id=user_id, guild_id=guild_id).first()
    if u is None:
        session.add(User(user_id=user_id, guild_id=guild_id, username=username or "Unknown#0000", avatar_url=avatar_url, is_active=True))
        session.flush()

def get_or_create_ai(session, user_id: int, guild_id: int) -> UserAIAnalysis:
    ai = session.query(UserAIAnalysis).filter_by(user_id=user_id, guild_id=guild_id).first()
    if ai is None:
        ai = UserAIAnalysis(
            user_id=user_id, guild_id=guild_id,
            dominant_sentiment="neutral",
            topics_of_interest={},
            communication_style=None,
            toxicity_level=0.0
        )
        session.add(ai)
        session.flush()
    return ai

# =========================
# 6) API publique 
# =========================
def analyze_and_update(
    user_id: int,
    guild_id: int,
    content: str,
    username: Optional[str] = "Unknown#0000",
    avatar_url: Optional[str] = None,
):
  
    # Petits courts-circuits pour perf
    if not content or len(content.strip()) < 2:
        return

    session = SessionLocal()
    try:
        ensure_guild_and_user(session, user_id, guild_id, username, avatar_url)

        sent_score, sent_label, tox, topics_add, style = analyze_text(content)

        ai = get_or_create_ai(session, user_id, guild_id)

     
        prev_tox = float(ai.toxicity_level or 0.0)
        delta = tox - prev_tox

        if delta > 0:  # toxicité augmente rapidement
            ai.toxicity_level = min(1.0, prev_tox + delta * 1.8)
        else:  # redescend lentement
            ai.toxicity_level = max(0.0, prev_tox + delta * 0.3)



        # Sentiment dominant : dernier label (réactif)
        ai.dominant_sentiment = sent_label

        # Topics : fusion de compteurs
        old_topics = dict(ai.topics_of_interest or {})
        for k, v in topics_add.items():
            old_topics[k] = int(old_topics.get(k, 0)) + int(v)
        ai.topics_of_interest = old_topics

        # Style : dernier style
        ai.communication_style = style

        ai.last_analysis = datetime.now(UTC)
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        print("⚠️ Erreur AI update :", e)
    except Exception as e:
        session.rollback()
        print("⚠️ Erreur inattendue AI update :", e)
    finally:
        session.close()

# =========================
# 7) batch 
# =========================
def rebuild_ai_for_user(user_id: int, guild_id: int):
    """Reconstruit l'IA d'un utilisateur à partir de l'historique."""
    session = SessionLocal()
    try:
        ensure_guild_and_user(session, user_id, guild_id)
        ai = get_or_create_ai(session, user_id, guild_id)
        # reset
        ai.toxicity_level = 0.0
        ai.dominant_sentiment = "neutral"
        ai.topics_of_interest = {}
        ai.communication_style = None

        msgs = (
            session.query(Message)
            .filter_by(user_id=user_id, guild_id=guild_id)
            .order_by(Message.timestamp.asc())
            .all()
        )
        for m in msgs:
            analyze_and_update(user_id, guild_id, m.message_content or "", username=None, avatar_url=None)
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        print("⚠️ Erreur rebuild AI (user):", e)
    finally:
        session.close()

def rebuild_ai_all():
    """Reconstruit pour tous les utilisateurs ayant des messages."""
    session = SessionLocal()
    try:
        pairs = session.query(Message.user_id, Message.guild_id).distinct().all()
        session.close()
        for uid, gid in pairs:
            rebuild_ai_for_user(uid, gid)
    except SQLAlchemyError as e:
        print("⚠️ Erreur rebuild AI (all):", e)

# =========================
# 8) Exports explicites
# =========================
__all__ = [
    "analyze_and_update",
    "rebuild_ai_for_user",
    "rebuild_ai_all",
]
