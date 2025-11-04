#!/usr/bin/env python3
import os, time
from datetime import date, timedelta, datetime
from typing import List, Tuple, Optional, Dict


os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib
if matplotlib.get_backend().lower() != "agg":
    matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- SQLAlchemy / DB ---
from sqlalchemy import func, desc, and_
from dotenv import load_dotenv
from db import SessionLocal
from create_db import (
    Message,
    User,
    UserActivity,
    UserAIAnalysis,
    UserEngagement,
    UserMessageDaily,
)

# --- Plotly (optionnel, plus lent) ---
PLOTLY_OK = True
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    try:
        pio.kaleido.scope.default_format = "png"
        pio.kaleido.scope.default_width = 900
        pio.kaleido.scope.default_height = 500
        pio.kaleido.scope.default_scale = 2
        pio.kaleido.scope.mathjax = None
    except Exception:
        pass
except Exception:
    PLOTLY_OK = False

# ======================================================
# ⚙️ CONFIG
# ======================================================
load_dotenv("config.env")
os.makedirs("charts", exist_ok=True)

# Engine par défaut: mpl (rapide)
DEFAULT_ENGINE = os.getenv("CHART_ENGINE", "mpl").lower()  # mpl|plotly
CACHE_TTL_SEC = int(os.getenv("CHART_CACHE_TTL", "60"))

PLOTLY_TEMPLATES = {
    "plotly", "plotly_white", "plotly_dark", "ggplot2",
    "seaborn", "simple_white", "presentation"
}

def _safe_template(t: Optional[str]) -> str:
    if not t:
        return "plotly_white"
    t = t.strip()
    return t if t in PLOTLY_TEMPLATES else "plotly_white"

def _fresh(path: str) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path) < CACHE_TTL_SEC)

# ======================================================
#  FETCHERS
# ======================================================
def _daterange_fill(start: date, end: date, agg: Dict[date, int]) -> Tuple[List[date], List[int]]:
    days, counts = [], []
    cur = start
    while cur <= end:
        days.append(cur)
        counts.append(int(agg.get(cur, 0)))
        cur += timedelta(days=1)
    return days, counts

def fetch_messages_daily(guild_id: int, days: Optional[int] = None) -> Tuple[List[date], List[int]]:
    """Série quotidienne continue (jours sans msg = 0). Prend UserMessageDaily si dispo."""
    session = SessionLocal()
    try:
        since: Optional[date] = None
        if days and days > 0:
            since = (datetime.utcnow().date() - timedelta(days=days - 1))

        q = session.query(UserMessageDaily.day, func.sum(UserMessageDaily.count)) \
                   .filter(UserMessageDaily.guild_id == guild_id)
        if since:
            q = q.filter(UserMessageDaily.day >= since)
        rows = q.group_by(UserMessageDaily.day).order_by(UserMessageDaily.day.asc()).all()

        if not rows:
            # fallback direct sur messages
            q2 = session.query(func.date(Message.timestamp), func.count(Message.id)) \
                        .filter(Message.guild_id == guild_id)
            if since:
                q2 = q2.filter(func.date(Message.timestamp) >= since)
            rows = q2.group_by(func.date(Message.timestamp)) \
                     .order_by(func.date(Message.timestamp).asc()).all()

        if not rows:
            return [], []

        agg = {d: int(c) for d, c in rows}
        start = min(agg.keys())
        end = max(agg.keys())
        if since and start > since:
            start = since
        return _daterange_fill(start, end, agg)
    finally:
        session.close()

def fetch_top_users(guild_id: int, limit: int = 10) -> List[Tuple[str, int]]:
    session = SessionLocal()
    try:
        rows = (
            session.query(User.username, UserActivity.message_count)
            .join(User, and_(User.user_id == UserActivity.user_id,
                             User.guild_id == UserActivity.guild_id))
            .filter(UserActivity.guild_id == guild_id)
            .order_by(desc(UserActivity.message_count))
            .limit(limit)
            .all()
        )
        out = []
        for n, c in rows:
            name = (n or "Utilisateur")
            if len(name) > 24:
                name = name[:21] + "…"
            out.append((name, int(c or 0)))
        return out
    finally:
        session.close()

def fetch_engagement(guild_id: int, limit: int = 10) -> List[Tuple[str, float]]:
    session = SessionLocal()
    try:
        rows = (
            session.query(User.username, UserEngagement.engagement_score)
            .join(User, and_(User.user_id == UserEngagement.user_id,
                             User.guild_id == UserEngagement.guild_id))
            .filter(UserEngagement.guild_id == guild_id)
            .order_by(desc(UserEngagement.engagement_score))
            .limit(limit)
            .all()
        )
        out = []
        for n, s in rows:
            name = (n or "Utilisateur")
            if len(name) > 24:
                name = name[:21] + "…"
            out.append((name, float(s or 0.0)))
        return out
    finally:
        session.close()

def fetch_sentiment(guild_id: int) -> Tuple[List[str], List[int]]:
    session = SessionLocal()
    try:
        rows = (
            session.query(UserAIAnalysis.dominant_sentiment, func.count(UserAIAnalysis.id))
            .filter(UserAIAnalysis.guild_id == guild_id)
            .group_by(UserAIAnalysis.dominant_sentiment)
            .all()
        )
        if not rows:
            return [], []
        labels, values = [], []
        for lab, cnt in rows:
            labels.append(lab or "inconnu")
            values.append(int(cnt))
        return labels, values
    finally:
        session.close()

# ======================================================
#  RENDERERS
# ======================================================
def _mpl_save(fig, ax, path: str) -> str:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", dpi=150)  
    plt.close(fig)
    return path

def _plotly_save(fig, path: str) -> bool:
    if not PLOTLY_OK:
        return False
    try:
        fig.write_image(path, scale=2)
        return True
    except Exception as e:
        print("⚠️ Plotly/Kaleido export failed. Fallback Matplotlib. Err:", e)
        return False


def render_messages(guild_id: int, viz_type: str, template: Optional[str], days: Optional[int], engine: str) -> Optional[str]:
    x, y = fetch_messages_daily(guild_id, days=days)
    if not x:
        return None
    viz = (viz_type or "line").lower()
    t = _safe_template(template)
    path = f"charts/messages_{guild_id}_{viz}_{days or 'all'}_{t}_{engine}.png"
    if _fresh(path):
        return path

    if engine == "plotly" and PLOTLY_OK:
        fig = go.Figure()
        if viz in {"line", "lines"}:
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Messages"))
        elif viz in {"area", "filled"}:
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines", fill="tozeroy", name="Messages"))
        elif viz in {"bar", "column"}:
            fig.add_trace(go.Bar(x=x, y=y, name="Messages"))
        elif viz == "scatter":
            fig.add_trace(go.Scatter(x=x, y=y, mode="markers", name="Messages"))
        else:
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Messages"))
        fig.update_layout(template=t, title="Messages par jour",
                          xaxis_title="Date", yaxis_title="Nombre de messages",
                          margin=dict(l=40, r=20, t=60, b=40))
        if _plotly_save(fig, path):
            return path

   
    fig, ax = plt.subplots(figsize=(9, 4))
    if viz in {"line", "lines", "scatter"}:
        ax.plot(x, y, marker="o", linewidth=2)
    elif viz in {"area", "filled"}:
        ax.fill_between(x, y, step=None, alpha=0.3)
        ax.plot(x, y, linewidth=2)
    elif viz in {"bar", "column"}:
        ax.bar(x, y)
    else:
        ax.plot(x, y, marker="o", linewidth=2)
    ax.set_title("Messages par jour")
    ax.set_xlabel("Date")
    ax.set_ylabel("Nombre de messages")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    fig.autofmt_xdate(rotation=30)
    return _mpl_save(fig, ax, path)


def render_top_users(guild_id: int, viz_type: str, template: Optional[str], engine: str) -> Optional[str]:
    rows = fetch_top_users(guild_id)
    if not rows:
        return None
    names = [n for n, _ in rows]
    values = [v for _, v in rows]
    viz = (viz_type or "bar").lower()
    t = _safe_template(template)
    path = f"charts/topusers_{guild_id}_{viz}_{t}_{engine}.png"
    if _fresh(path):
        return path

    if engine == "plotly" and PLOTLY_OK:
        if viz in {"bar", "column"}:
            fig = go.Figure(go.Bar(x=names, y=values))
        else:
            fig = go.Figure(go.Bar(y=names, x=values, orientation="h"))
        fig.update_layout(template=t, title="Top 10 membres (messages)",
                          xaxis_title="Messages" if viz not in {"bar", "column"} else "Utilisateur",
                          yaxis_title="Utilisateur" if viz not in {"bar", "column"} else "Messages",
                          margin=dict(l=80, r=20, t=60, b=40))
        if _plotly_save(fig, path):
            return path

    fig, ax = plt.subplots(figsize=(9, 5))
    if viz in {"bar", "column"}:
        ax.bar(names, values)
        ax.set_xlabel("Utilisateur")
        ax.set_ylabel("Messages")
        plt.xticks(rotation=30, ha="right")
    else:
        ax.barh(names, values)
        ax.set_ylabel("Utilisateur")
        ax.set_xlabel("Messages")
        ax.invert_yaxis()
    ax.set_title("Top 10 des membres les plus actifs")
    ax.grid(axis="x", alpha=0.2)
    return _mpl_save(fig, ax, path)

# --- Engagement ---
def render_engagement(guild_id: int, viz_type: str, template: Optional[str], engine: str) -> Optional[str]:
    rows = fetch_engagement(guild_id)
    if not rows:
        return None
    names = [n for n, _ in rows]
    scores = [v for _, v in rows]
    viz = (viz_type or "bar").lower()
    t = _safe_template(template)
    path = f"charts/engagement_{guild_id}_{viz}_{t}_{engine}.png"
    if _fresh(path):
        return path

    if engine == "plotly" and PLOTLY_OK:
        if viz in {"bar", "column"}:
            fig = go.Figure(go.Bar(x=names, y=scores))
        else:
            fig = go.Figure(go.Bar(y=names, x=scores, orientation="h"))
        fig.update_layout(template=t, title="Top 10 (score d’engagement)",
                          xaxis_title="Score" if viz not in {"bar", "column"} else "Utilisateur",
                          yaxis_title="Utilisateur" if viz not in {"bar", "column"} else "Score",
                          margin=dict(l=80, r=20, t=60, b=40))
        if _plotly_save(fig, path):
            return path

    fig, ax = plt.subplots(figsize=(9, 5))
    if viz in {"bar", "column"}:
        ax.bar(names, scores)
        ax.set_xlabel("Utilisateur")
        ax.set_ylabel("Score")
        plt.xticks(rotation=30, ha="right")
    else:
        ax.barh(names, scores)
        ax.set_ylabel("Utilisateur")
        ax.set_xlabel("Score")
        ax.invert_yaxis()
    ax.set_title("Score d’engagement des membres")
    ax.grid(axis="x", alpha=0.2)
    return _mpl_save(fig, ax, path)

# --- Sentiment ---
def render_sentiment(guild_id: int, viz_type: str, template: Optional[str], engine: str) -> Optional[str]:
    labels, values = fetch_sentiment(guild_id)
    if not labels:
        return None
    viz = (viz_type or "pie").lower()
    t = _safe_template(template)
    path = f"charts/sentiment_{guild_id}_{viz}_{t}_{engine}.png"
    if _fresh(path):
        return path

    if engine == "plotly" and PLOTLY_OK:
        if viz in {"pie", "donut", "doughnut"}:
            hole = 0.4 if viz in {"donut", "doughnut"} else 0.0
            fig = go.Figure(go.Pie(labels=labels, values=values, hole=hole))
        elif viz in {"bar", "column"}:
            fig = go.Figure(go.Bar(x=labels, y=values))
        else:
            fig = go.Figure(go.Pie(labels=labels, values=values))
        fig.update_layout(template=t, title="Répartition des sentiments",
                          margin=dict(l=40, r=20, t=60, b=40))
        if _plotly_save(fig, path):
            return path

    fig, ax = plt.subplots(figsize=(6, 6))
    if viz in {"pie", "donut", "doughnut"}:
        wedges, texts, autotexts = ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=120)
        ax.set_title("Répartition des sentiments")
    elif viz in {"bar", "column"}:
        ax.bar(labels, values)
        ax.set_xlabel("Sentiment")
        ax.set_ylabel("Utilisateurs")
        ax.set_title("Répartition des sentiments")
        plt.xticks(rotation=15)
    else:
        wedges, texts, autotexts = ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=120)
        ax.set_title("Répartition des sentiments")
    return _mpl_save(fig, ax, path)

# ======================================================
#  Dispatcher
# ======================================================
def generate_chart(dataset: str,
                   guild_id: int,
                   viz_type: str = "line",
                   days: Optional[int] = None,
                   template: Optional[str] = "plotly_white",
                   engine: Optional[str] = None) -> Optional[str]:
    eng = (engine or DEFAULT_ENGINE).lower()
    if eng not in {"mpl", "plotly"}:
        eng = "mpl"

    ds = (dataset or "").lower()
    if ds in {"messages", "msgs"}:
        return render_messages(guild_id, viz_type, template, days, eng)
    if ds in {"topusers", "top"}:
        return render_top_users(guild_id, viz_type, template, eng)
    if ds in {"engagement", "eng"}:
        return render_engagement(guild_id, viz_type, template, eng)
    if ds in {"sentiment", "sent"}:
        return render_sentiment(guild_id, viz_type, template, eng)
    return None
