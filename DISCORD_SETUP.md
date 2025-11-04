# Guide de Configuration du Bot Discord

## 1. Créer une Application Discord

1. Allez sur [Discord Developer Portal](https://discord.com/developers/applications)
2. Cliquez sur "New Application"
3. Donnez un nom à votre bot (ex: "Mon Bot")
4. Cliquez sur "Create"

## 2. Créer le Bot

1. Dans votre application, allez dans l'onglet "Bot"
2. Cliquez sur "Add Bot"
3. Personnalisez votre bot (nom, avatar, etc.)
4. **IMPORTANT**: Copiez le token du bot (cliquez sur "Copy")

## 3. Configurer les Permissions

1. Allez dans l'onglet "OAuth2" > "URL Generator"
2. Sélectionnez les scopes:
   - `bot` (pour les commandes de base)
   - `applications.commands` (pour les slash commands)
3. Sélectionnez les permissions nécessaires:
   - `Send Messages`
   - `Read Message History`
   - `Use Slash Commands`
   - `Embed Links`
4. Copiez l'URL générée et utilisez-la pour inviter le bot sur votre serveur

## 4. Configuration Locale

1. Copiez `config.env.example` vers `.env`
2. Remplacez `your_discord_bot_token_here` par votre vrai token
3. Modifiez le préfixe si nécessaire (par défaut: `!`)

## 5. Lancer le Bot

```bash
# Activer l'environnement virtuel
.venv\Scripts\activate

# Lancer le bot
python main.py
```

## Commandes Disponibles

- `!ping` - Teste la latence du bot
- `!hello` - Salutation
- `!info` - Informations sur le bot et le serveur

## Sécurité

⚠️ **IMPORTANT**: 
- Ne partagez JAMAIS votre token Discord
- Ajoutez `.env` à votre `.gitignore`
- Utilisez `config.env.example` comme modèle

## Dépannage

- **Bot ne se connecte pas**: Vérifiez le token dans `.env`
- **Bot ne répond pas**: Vérifiez les permissions du bot sur le serveur
- **Erreur de permissions**: Réinvitez le bot avec les bonnes permissions
