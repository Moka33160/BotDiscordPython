# Projet Bot Discord

Un bot Discord créé avec Python et discord.py.

## Installation

1. **Activer l'environnement virtuel** :
   ```bash
   # Sur Windows
   .venv\Scripts\activate
   
   # Sur Linux/Mac
   source .venv/bin/activate
   ```

2. **Installer les dépendances** :
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. **Créer votre bot Discord** :
   - Suivez le guide dans `DISCORD_SETUP.md`
   - Obtenez votre token sur [Discord Developer Portal](https://discord.com/developers/applications)

2. **Configurer les variables d'environnement** :
   ```bash
   # Copier le fichier d'exemple
   copy config.env.example .env
   
   # Éditer .env avec votre token Discord
   BOT_TOKEN=votre_token_discord_ici
   ```

## Utilisation

Lancer le bot :
```bash
python main.py
```

## Commandes Disponibles

- `!ping` - Teste la latence du bot
- `!hello` - Salutation personnalisée
- `!info` - Informations sur le bot et le serveur

## Structure du projet

```
Bot/
├── .venv/              # Environnement virtuel Python
├── .gitignore          # Fichiers à ignorer par Git
├── requirements.txt    # Dépendances Python
├── main.py            # Bot Discord principal
├── config.env.example # Exemple de configuration
├── DISCORD_SETUP.md   # Guide de configuration Discord
└── README.md          # Ce fichier
```

## Dépendances installées

- `discord.py` : Bibliothèque principale pour Discord
- `requests` : Pour les requêtes HTTP
- `python-dotenv` : Pour gérer les variables d'environnement
- `pytest` : Pour les tests
- `black` : Pour le formatage du code
- `flake8` : Pour la vérification du code

## Développement

### Formatage du code
```bash
black .
```

### Vérification du code
```bash
flake8 .
```

### Tests
```bash
pytest
```



## Notes

- L'environnement virtuel utilise Python 3.13.7
- Tous les packages sont installés dans `.venv/`
- N'oubliez pas d'activer l'environnement virtuel avant de travailler
- Consultez `DISCORD_SETUP.md` pour la configuration complète
# BotDiscordPython
