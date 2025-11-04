Voici un **README.md** complet en français, prêt à copier-coller, sans licence et avec toutes les commandes référencées.

---

# InsightCord

InsightCord est un bot Discord d’analytique communautaire. Il mesure l’activité texte et vocale, l’engagement, et ajoute une couche d’analyse IA (sentiment, toxicité, thèmes). Il génère des graphiques, classe les membres et fournit des outils d’administration.

## Fonctionnalités

* Suivi messages, voix, engagement (PostgreSQL + SQLAlchemy)
* Analyse IA locale/HF/OpenAI : sentiment, toxicité, thèmes, style
* Graphiques (Plotly → PNG via Kaleido, fallback Matplotlib)
* Création d’un salon privé admin et DM de configuration à l’arrivée du bot
* Exécutions en arrière-plan pour IA et graphiques (ThreadPoolExecutor)

## Prérequis

* Python 3.10+
* PostgreSQL 13+
* Application Discord + bot token (intents activés)
* Recommandé : Kaleido (export Plotly → PNG)

## Installation

```bash
git clone https://github.com/<vous>/BotDiscordPython.git
cd BotDiscordPython
python -m venv .venv
# Windows
. .venv/Scripts/activate
pip install -r requirements.txt
```

### Configuration (`config.env`)

> Ne jamais committer ce fichier.

```
BOT_TOKEN=xxxxxxxx
BOT_PREFIX=!
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/insightcord
AI_MODE=openai            # local | hf | openai
OPENAI_API_KEY=xxxxxxxx   # requis si AI_MODE=openai
DEBUG=True
LOG_LEVEL=INFO
```

### Lancement

```bash
python main.py
```

## Intents et permissions Discord

* Intents : Server Members, Message Content, Presence (optionnel)
* Permissions : Manage Channels (création salon privé), View/Send Messages, Add Reactions, Attach Files

## Commandes

### Aide

* `!help`
  Résumé de toutes les commandes, usage et exemples.
* `/help`
  Variante slash si activée (voir code si app commands synchronisés).

### Profils et classements

* `!insight [@membre]`
  Met à jour le profil IA (sentiment, toxicité, thèmes) pour vous ou le membre mentionné.
* `!rank [@membre]`
  Donne un rang basé sur l’engagement et la toxicité.
* `!profile <@membre | nom_exact>`
  Carte profil : messages du jour, dernière activité, canal favori, score/streak d’engagement, sentiment/toxicité/thèmes/style, synthèse vocale.

### Statistiques rapides

* `!stats`
  Statistiques de base (compteur de messages). Préférez les graphiques pour la visualisation.

### Graphiques

`!chart <dataset> [--type=...] [--days=N] [--theme=...]`

Datasets :

* `messages` : évolution quotidienne des messages (série continue par jour)
* `topusers` : top 10 par nombre de messages
* `engagement` : top par engagement_score
* `sentiment` : répartition du sentiment dominant

Types :

* `messages` : `line`, `area`, `bar`, `scatter`
* `topusers` : `bar` (vertical) ou horizontal par défaut si non `bar`
* `engagement` : `bar` (vertical) ou horizontal par défaut si non `bar`
* `sentiment` : `pie`, `donut`

Thèmes Plotly : `plotly`, `plotly_white`, `plotly_dark`, `ggplot2`, `seaborn`, `simple_white`, `presentation`

Exemples :

```
!chart messages --type=area --days=30 --theme=plotly_dark
!chart topusers --type=bar --theme=ggplot2
!chart engagement --type=bar
!chart sentiment --type=donut
```

### Administration

* `!admin engagement`
  Mesure la participation globale : utilisateurs actifs vs lurkers, activité quotidienne, distribution des posts.
* `!admin top-toxic`
  Top 10 des utilisateurs les plus toxiques avec niveau de toxicité, sentiment dominant et score d’engagement.
  Option de “surveillance” disponible dans le code (alertes si seuil dépassé).

## Architecture

* Interface Discord : `main.py`
* Modèles BD : `create_db.py` (PostgreSQL, SQLAlchemy)
* Session BD : `db.py`
* Pipeline messages : `cptMessageUtilisateur.py` (upsert guild/user, insert message)
* Suivi vocal : `cptVoiceUtilisateur.py`
* Activité utilisateur : `user_activity.py` (messages, moyennes, last time, canal favori, journaliers, réactions)
* Engagement : `user_engagement.py` (mentions, threads, invitations, active_days, streak_days, engagement_score)
* Analyse IA : `ai_analysis.py` (local/HF/OpenAI)
* Graphiques : `charts.py` (Plotly + fallback Matplotlib)
* Salon privé & DM : `bot_channel_manager.py`

## Modèle de données (principal)

* `guilds`, `users`
* `messages`
* `user_activity` : message_count, average_message_length, last_message_time, most_used_channel, reaction_count, received_reactions
* `user_message_daily` : agrégation journalière normalisée
* `user_voice` : temps vocal, sessions
* `user_engagement` : mentions, threads, invitations, active_days, streak_days, engagement_score
* `user_ai_analysis` : dominant_sentiment, topics_of_interest (JSONB), communication_style, toxicity_level

## Performance

* IA et graphiques en threads (ThreadPoolExecutor)
* UPSERT et transactions groupées
* Série journalière normalisée pour les graphiques quotidiens performants
* Fallback Matplotlib Agg si Kaleido indisponible

## Sécurité

* Ne jamais pousser de secrets (utiliser `config.env` ignoré par Git).
* Si des secrets ont fuité, les régénérer et réécrire l’historique avant de pousser.

## Dépannage rapide

* Graphiques lents : préférer `--days=N` pour limiter la fenêtre, vérifier Kaleido. En absence de Kaleido, fallback Matplotlib (plus fiable, parfois plus rapide).
* Doublons d’envoi : la commande `!chart` ne poste qu’une seule fois (salon privé si présent, sinon canal courant). Si vous voyez un doublon, vérifier qu’aucun autre bot/relay ne repost le fichier.

## Feuille de route

* Alertes automatiques (pics de toxicité, baisses d’activité)
* Tableau de bord web (FastAPI + React)
* Comparatifs temporels et nouveaux presets de graphiques

## Licence

Aucune licence. Tous droits réservés.
