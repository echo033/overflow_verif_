import discord
from discord.ext import commands

def setup_bot():
    # Configuration des intents
    intents = discord.Intents.default()
    intents.members = True       # Pour on_member_join
    intents.message_content = True
    intents.guilds = True       # Pour accéder aux infos des serveurs
    intents.dm_messages = True  # Pour les messages privés
    
    # Création du bot avec tous les intents nécessaires
    bot = commands.Bot(
        command_prefix="!",  # Gardé pour compatibilité
        intents=intents,
        help_command=None    # Désactive la commande help par défaut
    )
    
    async def setup_commands(bot):
        """Configure et synchronise les commandes slash."""
        try:
            commands_sync = await bot.tree.sync()
            print(f"✅ {len(commands_sync)} commandes slash synchronisées")
        except Exception as e:
            print(f"❌ Erreur lors de la synchronisation des commandes: {e}")
    
    @bot.event
    async def on_ready():
        await setup_commands(bot)
        print(f"Bot connecté en tant que {bot.user}")
        print("Les commandes slash seront disponibles dans quelques minutes.")
    
    return bot