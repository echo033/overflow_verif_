import ipaddress
import os
import asyncio
import secrets
import logging
import sqlite3
import datetime
from dotenv import load_dotenv
import json
from typing import Dict, Tuple, Optional, List
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

import aiohttp
from aiohttp import request, web
import discord
from discord.ext import commands
from bot_setup import setup_bot
import socket
import time
try:
    import geoip2.database
    GEOIP_AVAILABLE = True
except Exception:
    GEOIP_AVAILABLE = False

logging.basicConfig(level=logging.INFO)



load_dotenv()
logging.info(f"✅ Fichier .env chargé. IPHub key présente: {bool(os.getenv('IPHUB_API_KEY'))}")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BASE_URL = os.getenv("BASE_URL", "https://wearying-unharmonious-reta.ngrok-free.dev")
VPN_API_KEY = os.getenv("VPN_API_KEY", "")  
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "verifications.db")
MIN_ACCOUNT_AGE_DAYS = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", "180"))  
MAX_ACCOUNTS_PER_IP = int(os.getenv("MAX_ACCOUNTS_PER_IP", "1"))  

if not DISCORD_TOKEN:
    logging.warning("DISCORD_TOKEN non défini. Le bot ne pourra pas se connecter tant que la variable d'environnement n'est pas définie.")


def init_db():
    with open('schema.sql', 'r', encoding='utf-8') as f:
        schema = f.read()
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(schema)
    logging.info(f"Base de données initialisée : {DB_PATH}")

init_db()


with sqlite3.connect(DB_PATH) as _conn:
    _conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_tokens (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        guild_id INTEGER,
        created_at TEXT NOT NULL
    )
    """)


def save_pending_token_db(token: str, user_id: int, guild_id: Optional[int]):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_tokens (token, user_id, guild_id, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, guild_id, datetime.datetime.utcnow().isoformat() + "Z")
        )


def pop_pending_token_db(token: str) -> Optional[Tuple[int, Optional[int]]]:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT user_id, guild_id FROM pending_tokens WHERE token = ?", (token,))
        row = cur.fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM pending_tokens WHERE token = ?", (token,))
        return row[0], row[1]


def load_pending_tokens_from_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT token, user_id, guild_id FROM pending_tokens").fetchall()
    for t, u, g in rows:
        pending_tokens[t] = (u, g)


def add_ip_to_config(list_type: str, ip: str, reason: str, added_by: int):
    cfg = {}
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {"whitelist": [], "blacklist": []}
    key = 'whitelist' if list_type == 'whitelist' else 'blacklist'
    entry = {
        "ip": ip,
        "reason": reason,
        "added_by": added_by,
        "added_at": datetime.datetime.utcnow().isoformat() + "Z"
    }
    cfg.setdefault(key, []).append(entry)
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)



pending_tokens: Dict[str, Tuple[int, Optional[int]]] = {}


load_pending_tokens_from_db()



class VerifyViewForUser(discord.ui.View):
    def __init__(self, url: str, target_user_id: int, *, timeout: Optional[float] = 360):
        super().__init__(timeout=timeout)
        self.url = url
        self.target_user_id = target_user_id
        
        self.add_item(discord.ui.Button(label="Ouvrir le lien de vérification", style=discord.ButtonStyle.link, url=self.url))

    @discord.ui.button(label="✅ Vérifier", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
        if interaction_button.user.id != self.target_user_id:
            await interaction_button.response.send_message("Ce bouton n'est pas pour vous.", ephemeral=True)
            return
        private_embed = discord.Embed(
            title="✅ Vérification en cours",
            description=("Cliquez sur 'Ouvrir le lien de vérification' puis suivez les étapes "
                         "dans votre navigateur pour terminer."),
            color=0x3498DB,
        )
        private_embed.add_field(name="Lien de vérification", value=f"[Ouvrir le lien]({self.url})", inline=False)
        await interaction_button.response.send_message(embed=private_embed, ephemeral=True)



class UniversalVerifyView(discord.ui.View):
    def __init__(self, *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="✅ Vérifier", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
        user = interaction_button.user
        guild_id = interaction_button.guild_id or (interaction_button.user.guild.id if hasattr(interaction_button.user, 'guild') else None)
        token = secrets.token_urlsafe(16)
        pending_tokens[token] = (user.id, interaction_button.guild_id)
        save_pending_token_db(token, user.id, interaction_button.guild_id)
        verify_link = f"{BASE_URL}/verify?token={token}"

        private_embed = discord.Embed(
            title="🔵 Vérification - lien privé",
            description=("Voici votre lien de vérification unique. Ouvrez-le dans votre navigateur "
                         "pour que le serveur puisse contrôler votre IP."),
            color=0x3498DB,
        )
        private_embed.add_field(name="Lien de vérification (privé)", value=f"[Ouvrir le lien]({verify_link})", inline=False)
        private_embed.set_footer(text="Ce lien est personnel et expirera si vous ne l'utilisez pas.")
        try:
            await interaction_button.response.send_message(embed=private_embed, ephemeral=True)
        except Exception:
            try:
                await interaction_button.response.send_message("Impossible d'envoyer le lien de vérification de manière privée. Contactez un modérateur.", ephemeral=True)
            except Exception:
                pass




def render_html_page(
    title: str,
    heading: str,
    message: str,
    details: Optional[str] = None,
    guild_name: str = "Serveur Discord",
    guild_logo: str = "https://i.imgur.com/8Km9tLL.png",
    accent_color: str = "#2ECC71"
) -> str:
    """Page statique moderne et responsive avec thème dynamique."""
    safe_details = f"<p class='details'>{details}</p>" if details else ""

    html = f"""
    <!doctype html>
    <html lang="fr">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{title}</title>
        <style>
            :root {{
                color-scheme: light dark;
                --accent: {accent_color};
                --bg-dark: #0f1724;
                --bg-light: #f3f4f6;
                --text-dark: #e6eef8;
                --text-light: #1e293b;
            }}
            @media (prefers-color-scheme: light) {{
                body {{ background: var(--bg-light); color: var(--text-light); }}
                .card {{ background: white; color: var(--text-light); }}
                .btn {{ background: var(--accent); color: white; }}
            }}
            @media (prefers-color-scheme: dark) {{
                body {{ background: var(--bg-dark); color: var(--text-dark); }}
                .card {{ background: rgba(255,255,255,0.05); color: var(--text-dark); }}
                .btn {{ background: var(--accent); color: #07203a; }}
            }}
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;
                display:flex; align-items:center; justify-content:center;
                margin:0; padding:0; min-height:100vh;
            }}
            .card {{
                border-radius:18px;
                padding:32px;
                width:95%; max-width:720px;
                text-align:center;
                box-shadow:0 6px 30px rgba(0,0,0,0.3);
                animation: fadeIn 1s ease-in-out;
            }}
            @keyframes fadeIn {{
                from {{ opacity:0; transform:translateY(20px); }}
                to {{ opacity:1; transform:none; }}
            }}
            img.logo {{
                width:90px; height:90px; border-radius:50%;
                margin-bottom:12px;
                box-shadow:0 0 12px var(--accent);
            }}
            h1 {{ font-size:24px; color: var(--accent); margin:8px 0; }}
            p {{ line-height:1.5; margin:8px 0; }}
            .details {{ font-size:13px; color:#9fb7d3; }}
            .btn {{
                display:inline-block;
                margin-top:16px;
                padding:12px 18px;
                border-radius:10px;
                text-decoration:none;
                font-weight:600;
                transition: all 0.2s ease;
            }}
            .btn:hover {{
                transform:scale(1.05);
                filter:brightness(1.1);
            }}
            footer {{ margin-top:16px; font-size:13px; color:#9fb7d3; }}
        </style>
    </head>
    <body>
        <div class="card">
            <img src="{guild_logo}" class="logo" alt="Logo serveur">
            <h1>{heading}</h1>
            <p>{message}</p>
            {safe_details}
            <a class="btn" href="/">Retour</a>
            <footer>{guild_name} — {datetime.datetime.utcnow().isoformat()}Z</footer>
        </div>
    </body>
    </html>
    """
    return html

async def get_user_avatar_url(user_id: int) -> str:
    """Retourne l'URL de l'avatar Discord (ou une image par défaut)."""
    try:
        user = await bot.fetch_user(user_id)
        
        if user.avatar:
            return user.avatar.url
        else:
            return user.default_avatar.url
    except Exception as e:
        logging.warning(f"Impossible de récupérer l'avatar pour {user_id}: {e}")
        return "https://cdn.discordapp.com/embed/avatars/0.png"

async def get_user_profile(user_id: int) -> Tuple[str, str]:
    """Retourne (avatar_url, username)"""
    try:
        user = await bot.fetch_user(user_id)
        avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
        username = user.global_name or user.name  
        return avatar_url, username
    except Exception as e:
        logging.warning(f"Impossible de récupérer le profil Discord pour {user_id}: {e}")
        return "https://cdn.discordapp.com/embed/avatars/0.png", "Utilisateur inconnu"




async def update_rich_presence():
    """Met à jour la Rich Presence avec le nombre total de membres dans le serveur."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            # 🔹 Récupère l’ID du serveur depuis .env
            guild_id = int(os.getenv("DEV_GUILD_ID", "0")) or int(os.getenv("MAIN_GUILD_ID", "0")) or None
            if not guild_id:
                logging.warning("Aucun GUILD_ID défini pour le suivi des membres.")
                await asyncio.sleep(300)
                continue

            # 🔹 Récupère le serveur
            guild = bot.get_guild(guild_id)
            if not guild:
                logging.warning(f"Guild {guild_id} introuvable (pas encore chargée ?)")
                await asyncio.sleep(60)
                continue

            # 🔹 Nombre total de membres
            total_members = sum(1 for m in guild.members if not m.bot)


            # 🔹 Change la Rich Presence
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{total_members} membres in the server"
            )
            await bot.change_presence(activity=activity)

            logging.info(f"Rich Presence mise à jour : {total_members} membres actuellement dans le serveur.")

        except Exception as e:
            logging.warning(f"Erreur lors de la mise à jour Rich Presence: {e}")

        # Actualiser toutes les 5 minutes
        await asyncio.sleep(300)



def render_html_with_delay(
    title: str,
    heading: str,
    message: str,
    details: Optional[str] = None,
    delay_ms: int = 6000,
    guild_name: str = "Serveur Discord",
    guild_logo: str = "https://i.imgur.com/8Km9tLL.png",
    accent_color: str = "#2ECC71"
) -> str:
    """Page dynamique avec spinner + transition douce du résultat."""
    safe_details = f"<p class='details'>{details}</p>" if details else ""

    html = f"""
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{title}</title>
      <style>
        :root {{
          color-scheme: light dark;
          --accent: {accent_color};
          --bg-dark: #0f1724;
          --bg-light: #f3f4f6;
          --text-dark: #e6eef8;
          --text-light: #1e293b;
        }}
        body {{
          font-family:'Inter', system-ui, sans-serif;
          display:flex; align-items:center; justify-content:center;
          margin:0; padding:0; min-height:100vh;
          background:var(--bg-dark); color:var(--text-dark);
        }}
        @media (prefers-color-scheme: light) {{
          body {{ background:var(--bg-light); color:var(--text-light); }}
        }}
        .card {{
          background: rgba(255,255,255,0.05);
          border-radius:18px; padding:32px;
          max-width:720px; width:95%;
          text-align:center;
          box-shadow:0 6px 30px rgba(0,0,0,0.3);
        }}
        img.logo {{
          width:90px; height:90px; border-radius:50%;
          margin-bottom:10px;
          box-shadow:0 0 12px var(--accent);
        }}
        h1 {{ font-size:22px; color:var(--accent); margin:10px 0; }}
        p {{ margin:8px 0; line-height:1.6; }}
        .details {{ font-size:13px; color:#9fb7d3; }}
        .btn {{ display:inline-block; margin-top:16px; background:var(--accent); color:#07203a; padding:12px 18px; border-radius:10px; text-decoration:none; font-weight:600; }}
        .spinner {{ width:64px; height:64px; margin:20px auto; border-radius:50%; border:6px solid rgba(255,255,255,0.12); border-top-color:var(--accent); animation:spin 1s linear infinite; }}
        @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
        .fadeIn {{ animation: fadeIn 0.8s ease-in-out; }}
        @keyframes fadeIn {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
        footer {{ margin-top:16px; font-size:13px; color:#9fb7d3; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div id="analysis" class="fadeIn">
          <img src="{guild_logo}" class="logo" alt="Logo serveur">
          <h1>Analyse en cours...</h1>
          <div class="spinner"></div>
          <p class="muted">Le système vérifie votre identité, cela prend quelques secondes...</p>
        </div>

        <div id="result" style="display:none" class="fadeIn">
          <img src="{guild_logo}" class="logo" alt="Logo serveur">
          <h1>{heading}</h1>
          <p>{message}</p>
          {safe_details}
          <a class="btn" href="/">Retour</a>
          <footer>{guild_name} — {datetime.datetime.utcnow().isoformat()}Z</footer>
        </div>
      </div>

      <script>
        setTimeout(() => {{
          document.getElementById('analysis').style.display = 'none';
          document.getElementById('result').style.display = 'block';
        }}, {delay_ms});
      </script>
    </body>
    </html>
    """
    return html



intents = discord.Intents.default()
intents.members = True  
intents.message_content = True  
intents.guilds = True
intents.dm_messages = True

class VerificationBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",  
            intents=intents,
            application_commands=[],  
        )
        
    async def setup_hook(self):
        dev_guild = os.getenv('DEV_GUILD_ID')
        try:
            if dev_guild:
                guild_obj = discord.Object(id=int(dev_guild))
                synced = await self.tree.sync(guild=guild_obj)
                logging.info(f"✅ {len(synced)} commandes slash synchronisées pour le guild {dev_guild}")
            else:
                synced = await self.tree.sync()
                logging.info(f"✅ {len(synced)} commandes slash synchronisées globalement")
            
            try:
                self._commands_synced = True
            except Exception:
                pass
        except Exception as e:
            logging.exception("Erreur lors de la synchronisation des commandes slash")

bot = VerificationBot()


pending_tokens: Dict[str, Tuple[int, Optional[int]]] = {}


VERIF_CHANNEL_ID = int(os.getenv('VERIF_CHANNEL_ID', '1098926833665331241'))
LOGS_CHANNEL_ID = 1435232123475853413


@bot.tree.command(name="verifier", description="Lance la vérification de votre compte")
async def verifier(interaction: discord.Interaction):
    
    embed = discord.Embed(
        title="🔒 Vérification requise",
        description=("Pour accéder au serveur, cliquez sur **Vérifier** ci-dessous. "
                     "pour finaliser la vérification dans votre navigateur."),
        color=0x2ECC71,
    )
    embed.set_footer(text="Ce message est public — le lien de vérification est envoyé en privé lorsque vous cliquez.")

    class UniversalVerifyView(discord.ui.View):
        def __init__(self, *, timeout: Optional[float] = None):
            super().__init__(timeout=timeout)

        @discord.ui.button(label="✅ Vérifier", style=discord.ButtonStyle.primary)
        async def verify_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
            
            user = interaction_button.user
            guild_id = interaction_button.guild_id or interaction_button.user.guild.id if hasattr(interaction_button.user, 'guild') else None
            token = secrets.token_urlsafe(16)
            pending_tokens[token] = (user.id, interaction_button.guild_id)
            save_pending_token_db(token, user.id, interaction_button.guild_id)
            verify_link = f"{BASE_URL}/verify?token={token}"

           
            private_embed = discord.Embed(
                title="🔵 Vérification - lien privé",
                description=("Voici votre lien de vérification unique. Ouvrez-le dans votre navigateur "
                             "pour que le serveur puisse contrôler votre IP."),
                color=0x3498DB,
            )
            private_embed.add_field(name="Lien de vérification (privé)", value=f"[Ouvrir le lien]({verify_link})", inline=False)
            private_embed.set_footer(text="Ce lien est personnel et expirera si vous ne l'utilisez pas.")

            try:
                await interaction_button.response.send_message(embed=private_embed, ephemeral=True)
            except Exception:

                try:
                    await interaction_button.response.send_message(
                        "Impossible d'envoyer le lien de vérification de manière privée. Contactez un modérateur.",
                        ephemeral=True,
                    )
                except Exception:
                    
                    pass


    view = UniversalVerifyView()

   
    channel = bot.get_channel(VERIF_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(VERIF_CHANNEL_ID)
        except Exception:
            await interaction.response.send_message("Erreur: canal de vérification introuvable.", ephemeral=True)
            return

    try:
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"Message de vérification public posté dans {channel.mention}.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("Impossible d'envoyer le message de vérification dans le canal configuré.", ephemeral=True)


@bot.tree.command(name="token", description="Génère un lien de vérification privé pour vous")
async def token_cmd(interaction: discord.Interaction):
    """Génère un token unique et renvoie le lien de vérification de façon éphémère."""
    user = interaction.user
    guild_id = interaction.guild_id

    
    token = secrets.token_urlsafe(16)
    pending_tokens[token] = (user.id, guild_id)
    save_pending_token_db(token, user.id, guild_id)
    verify_link = f"{BASE_URL}/verify?token={token}"

    private_embed = discord.Embed(
        title="🔵 Votre lien de vérification",
        description=("Voici votre lien de vérification unique. Ouvrez-le dans votre navigateur "
                     "pour que le serveur puisse contrôler votre IP."),
        color=0x3498DB,
    )
    private_embed.add_field(name="Lien de vérification (privé)", value=f"[Ouvrir le lien]({verify_link})", inline=False)
    private_embed.set_footer(text="Ce lien est personnel et expirera si vous ne l'utilisez pas.")

    
    dm_sent = False
    try:
        await user.send(embed=private_embed)
        dm_sent = True
    except Exception:
        logging.exception(f"Impossible d'envoyer le DM à l'utilisateur {user.id}")

    
    try:
        if guild_id is not None:
            if dm_sent:
                await interaction.response.send_message("✅ Le lien de vérification vous a été envoyé en message privé.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "⚠️ Je n'ai pas pu vous envoyer de message privé (D.M. fermés). Ouvrez vos messages privés avec les membres du serveur et réessayez, ou utilisez `/verifier` pour générer un lien public.",
                    ephemeral=True,
                )
        else:
            
            try:
                if dm_sent:
                    await interaction.response.send_message("✅ Lien envoyé en message privé.", ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "⚠️ Impossible d'envoyer le lien en MP. Assurez-vous que vos DMs sont ouverts et réessayez.",
                        ephemeral=True,
                    )
            except Exception:
                pass
    except Exception:
        
        logging.exception("Erreur lors de la réponse à l'interaction /token")

@bot.tree.command(name="check", description="Vérifie les comptes associés à une IP")
@discord.app_commands.describe(ip="L'adresse IP à vérifier")
async def check_ip(interaction: discord.Interaction, ip: str):
    """Affiche les comptes associés à une IP."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Cette commande est réservée aux administrateurs.", ephemeral=True)
        return
        
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT list_type, added_by, reason FROM ip_lists WHERE ip_address = ?",
            (ip,)
        ).fetchone()
        
        accounts = conn.execute("""
            SELECT user_id, guild_id, created_at, verification_status
            FROM verifications WHERE ip_address = ?
            ORDER BY created_at DESC LIMIT 10
        """, (ip,)).fetchall()
    
    embed = discord.Embed(title=f"🔍 Vérification de l'IP {ip}", color=0x00ff00)
    
    if status:
        status_text = "✅ Whitelist" if status['list_type'] == 'whitelist' else "⛔ Blacklist"
        embed.add_field(
            name="Statut", 
            value=f"{status_text}\nRaison: {status['reason']}\nPar: <@{status['added_by']}>",
            inline=False
        )
    
    if accounts:
        accounts_text = "\n".join(
            f"<@{acc['user_id']}> - {acc['verification_status']} "
            f"({acc['created_at']})"
            for acc in accounts
        )
        embed.add_field(name=f"Comptes associés ({len(accounts)})", value=accounts_text, inline=False)
    else:
        embed.add_field(name="Comptes associés", value="Aucun compte trouvé", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="blacklist", description="Ajoute une IP à la blacklist")
@discord.app_commands.describe(
    ip="L'adresse IP à blacklister",
    reason="Raison du blacklist (optionnel)"
)
async def blacklist(interaction: discord.Interaction, ip: str, reason: str = "Non spécifiée"):
    """Ajoute une IP à la blacklist."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Cette commande est réservée aux administrateurs.", ephemeral=True)
        return
        
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ip_lists (ip_address, list_type, added_by, reason) VALUES (?, 'blacklist', ?, ?)",
            (ip, interaction.user.id, reason)
        )
    
    try:
        add_ip_to_config('blacklist', ip, reason, interaction.user.id)
    except Exception:
        logging.exception("Impossible d'écrire dans config.json pour la blacklist")
    await interaction.response.send_message(
        f"⛔ IP `{ip}` ajoutée à la blacklist.\nRaison: {reason}", 
        ephemeral=True
    )

@bot.tree.command(name="whitelist", description="Ajoute une IP à la whitelist")
@discord.app_commands.describe(
    ip="L'adresse IP à whitelister",
    reason="Raison du whitelist (optionnel)"
)
async def whitelist(interaction: discord.Interaction, ip: str, reason: str = "Non spécifiée"):
    """Ajoute une IP à la whitelist."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Cette commande est réservée aux administrateurs.", ephemeral=True)
        return
        
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ip_lists (ip_address, list_type, added_by, reason) VALUES (?, 'whitelist', ?, ?)",
            (ip, interaction.user.id, reason)
        )
    try:
        add_ip_to_config('whitelist', ip, reason, interaction.user.id)
    except Exception:
        logging.exception("Impossible d'écrire dans config.json pour la whitelist")
    await interaction.response.send_message(
        f"✅ IP `{ip}` ajoutée à la whitelist.\nRaison: {reason}", 
        ephemeral=True
    )


TOR_EXIT_URL = "https://check.torproject.org/exit-addresses"
_tor_cache = {"ips": set(), "updated": 0}
TOR_CACHE_TTL = 60 * 60  # 1 heure

async def _refresh_tor_list():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TOR_EXIT_URL, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    ips = set()
                    for line in text.splitlines():
                        if line.startswith('ExitAddress'):
                            parts = line.split()
                            if len(parts) >= 2:
                                ips.add(parts[1].strip())
                    _tor_cache['ips'] = ips
                    _tor_cache['updated'] = time.time()
    except Exception:
        logging.exception("Impossible de rafraîchir la liste Tor (ignorer)")


SUSPECT_KEYWORDS = (
    'digitalocean', 'linode', 'ovh', 'hetzner', 'amazon', 'amazonaws', 'aws',
    'google', 'microsoft', 'azure', 'cloud', 'vultr', 'scaleway', 'ibm', 'oracle',
    'host', 'server', 'vps', 'datacenter', 'proxy', 'vpn', 'anonymizer',
    'proxyserver', 'fastly', 'cloudflare', 'akamai', 'edgecast',
    'hurricane', 'leaseweb', 'softlayer', 'contabo', 'upcloud', 'tencent',
    'aliyun', 'gcore', 'netcup', 'nforce', 'keystone', 'packet', 'scaleway',
    'exoscale', 'virmach', 'buyvm', 'turnkey', 'nordvpn', 'expressvpn',
    'surfshark', 'cyberghost', 'privateinternetaccess', 'ipvanish',
    'proton', 'protonvpn'
)


async def check_ip_vpn(ip: str) -> Tuple[bool, dict]:
    """Détecte les VPN / proxies en combinant IPHub + heuristiques locales."""
    details = {"checks": []}

    # 1️⃣ Vérif liste Tor
    try:
        if time.time() - _tor_cache.get('updated', 0) > TOR_CACHE_TTL:
            await _refresh_tor_list()
        if ip in _tor_cache.get('ips', set()):
            details['checks'].append('tor_exit')
            return True, details
    except Exception:
        logging.exception('Erreur lors du check Tor')

    # 2️⃣ Vérif reverse DNS (hébergeur connu)
    try:
        loop = asyncio.get_running_loop()
        def _rdns_lookup(a):
            try:
                return socket.gethostbyaddr(a)[0]
            except Exception:
                return None

        rdns = await loop.run_in_executor(None, _rdns_lookup, ip)
        if rdns:
            details['rdns'] = rdns
            lower = rdns.lower()
            for kw in SUSPECT_KEYWORDS:
                if kw in lower:
                    details['checks'].append(f'rdns_match:{kw}')
                    return True, details
    except Exception:
        logging.exception('Erreur lors du reverse DNS')
        
        

    # 3️⃣ Vérif ASN via GeoLite2
    try:
        geo_db_path = 'data/GeoLite2-ASN.mmdb'
        if GEOIP_AVAILABLE and os.path.exists(geo_db_path):
            def _geoip_lookup(a):
                try:
                    with geoip2.database.Reader(geo_db_path) as reader:
                        rec = reader.asn(a)
                        return rec.autonomous_system_number, rec.autonomous_system_organization
                except Exception:
                    return None, None

            asn, asn_org = await loop.run_in_executor(None, _geoip_lookup, ip)
            if asn or asn_org:
                details['asn'] = asn
                details['asn_org'] = (asn_org or '').lower() if asn_org else ''
                if any(kw in (asn_org or '').lower() for kw in SUSPECT_KEYWORDS):
                    details['checks'].append(f'asn_org_match:{asn_org}')
                    return True, details
    except Exception:
        logging.exception('Erreur lors du GeoIP ASN check')

    # 4️⃣ Vérif via IPHub API (si clé dispo)
    api_key = os.getenv("IPHUB_API_KEY")
    if api_key:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://v2.api.iphub.info/ip/{ip}", headers={"X-Key": api_key}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        details['iphub'] = data
                        if data.get("block", 0) == 1:
                            details['checks'].append(f"iphub_block:{data}")
                            return True, details
                        elif data.get("block", 0) == 2:
                            details['checks'].append(f"iphub_warn:{data}")
                            return True, details
        except Exception as e:
            logging.warning(f"Erreur IPHub: {e}")



def is_admin():
    """Vérifie si l'utilisateur est admin du serveur."""
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

async def setup_commands():
    """Configure et synchronise les commandes slash."""
    
    if getattr(bot, '_commands_synced', False):
        logging.info("Les commandes ont déjà été synchronisées ; saut de la resynchronisation.")
        return

    try:
        commands_sync = await bot.tree.sync()
        logging.info(f"✅ {len(commands_sync)} commandes slash synchronisées")
        bot._commands_synced = True
    except Exception as e:
        logging.error(f"❌ Erreur lors de la synchronisation des commandes: {e}")

@bot.event
async def on_ready():
    logging.info(f"Bot connecté en tant que {bot.user} (id: {bot.user.id})")
    logging.info("Les commandes slash seront disponibles dans quelques minutes.")

    dev_guild = os.getenv("DEV_GUILD_ID")

    if dev_guild:
        guild = bot.get_guild(int(dev_guild))
        if guild:
            logging.info(f"✅ Suivi de la guilde '{guild.name}' ({guild.id}) pour la Rich Presence.")
        else:
            logging.warning(f"⚠️ Guild {dev_guild} non trouvée (peut-être pas encore chargée).")
    else:
        logging.warning("⚠️ Aucune variable DEV_GUILD_ID trouvée dans le .env.")

    # Lance les tâches périodiques si pas déjà actives
    if not hasattr(bot, 'periodic_poster_task'):
        bot.periodic_poster_task = asyncio.create_task(periodic_post_verification())
        logging.info("Tâche périodique de publication d'embed configurée (toutes les 20 minutes).")

    if not hasattr(bot, 'rich_presence_task'):
        bot.rich_presence_task = asyncio.create_task(update_rich_presence())
        logging.info("Tâche périodique de mise à jour du Rich Presence configurée.")


    
    if not hasattr(bot, 'periodic_poster_task'):
        bot.periodic_poster_task = asyncio.create_task(periodic_post_verification())
        logging.info("Tâche périodique de publication d'embed configurée (toutes les 20 minutes).")

    
    if not hasattr(bot, 'rich_presence_task'):
        bot.rich_presence_task = asyncio.create_task(update_rich_presence())
        logging.info("Tâche périodique de mise à jour du Rich Presence configurée.")

@bot.command()
@is_admin()
async def whitelist(ctx, ip: str, *, reason: str = "Non spécifiée"):
    """Ajoute une IP à la whitelist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ip_lists (ip_address, list_type, added_by, reason) VALUES (?, 'whitelist', ?, ?)",
            (ip, ctx.author.id, reason)
        )
    try:
        add_ip_to_config('whitelist', ip, reason, ctx.author.id)
    except Exception:
        logging.exception("Impossible d'écrire dans config.json pour la whitelist")
    await ctx.send(f"✅ IP {ip} ajoutée à la whitelist.")

@bot.command()
@is_admin()
async def blacklist(ctx, ip: str, *, reason: str = "Non spécifiée"):
    """Ajoute une IP à la blacklist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ip_lists (ip_address, list_type, added_by, reason) VALUES (?, 'blacklist', ?, ?)",
            (ip, ctx.author.id, reason)
        )
    try:
        add_ip_to_config('blacklist', ip, reason, ctx.author.id)
    except Exception:
        logging.exception("Impossible d'écrire dans config.json pour la blacklist")
    await ctx.send(f"⛔ IP {ip} ajoutée à la blacklist.")

@bot.command()
async def verifier(ctx):
    """Version texte: poster le message de vérification dans le canal configuré."""
    token = secrets.token_urlsafe(16)
    pending_tokens[token] = (ctx.author.id, ctx.guild.id)
    save_pending_token_db(token, ctx.author.id, ctx.guild.id)
    verify_link = f"{BASE_URL}/verify?token={token}"

    embed = discord.Embed(
        title="🔒 Vérification requise",
        description=("Cliquez sur le bouton ci-dessous pour vérifier votre IP et obtenir le rôle **Vérifié**."),
        color=0x2ECC71
    )
    embed.add_field(name="Utilisateur", value=f"{ctx.author.mention}", inline=True)
    embed.set_footer(text="Ce lien est unique. Si vous avez un problème, contactez un modérateur.")

    
    view = VerifyViewForUser(verify_link, ctx.author.id)

    
    channel = bot.get_channel(VERIF_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(VERIF_CHANNEL_ID)
        except Exception:
            await ctx.reply("Erreur: canal de vérification introuvable.")
            return

    try:
        await channel.send(content=f"{ctx.author.mention}", embed=embed, view=view)
        await ctx.reply(f"Le message de vérification a été posté dans {channel.mention}.", delete_after=8)
    except Exception:
        await ctx.reply("Impossible d'envoyer le message de vérification dans le canal configuré.")

@bot.command()
@is_admin()
async def check_ip(ctx, ip: str):
    """Affiche les comptes associés à une IP."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT list_type, added_by, reason FROM ip_lists WHERE ip_address = ?",
            (ip,)
        ).fetchone()
        
        accounts = conn.execute("""
            SELECT user_id, guild_id, created_at, verification_status
            FROM verifications WHERE ip_address = ?
            ORDER BY created_at DESC LIMIT 10
        """, (ip,)).fetchall()
    
    embed = discord.Embed(title=f"Vérification de l'IP {ip}", color=0x00ff00)
    
    if status:
        status_text = "✅ Whitelist" if status['list_type'] == 'whitelist' else "⛔ Blacklist"
        embed.add_field(
            name="Statut", 
            value=f"{status_text}\nRaison: {status['reason']}\nPar: <@{status['added_by']}>",
            inline=False
        )
    
    if accounts:
        accounts_text = "\n".join(
            f"<@{acc['user_id']}> - {acc['verification_status']} "
            f"({acc['created_at']})"
            for acc in accounts
        )
        embed.add_field(name=f"Comptes associés ({len(accounts)})", value=accounts_text, inline=False)
    else:
        embed.add_field(name="Comptes associés", value="Aucun compte trouvé", inline=False)
    
    await ctx.send(embed=embed)

@bot.event
async def on_member_join(member: discord.Member):
    """Ne rien poster automatiquement lors du join (évite les doublons/bugs d'affichage).
    Les utilisateurs peuvent générer leur token avec la commande /token si nécessaire.
    """
    logging.info(f"Membre rejoint: {member} - aucun message de vérification automatique envoyé.")



app = web.Application()


async def check_alt_accounts(ip: str, user_id: int, guild_id: int) -> Tuple[bool, str, List[dict]]:
    """Vérifie si l'IP est associée à d'autres comptes."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        
        alts = conn.execute("""
            SELECT user_id, guild_id, created_at, verification_status
            FROM verifications 
            WHERE ip_address = ? AND user_id != ? 
            ORDER BY created_at DESC
        """, (ip, user_id)).fetchall()
        
        if len(alts) >= MAX_ACCOUNTS_PER_IP:
            alt_info = [dict(row) for row in alts]
            
            guild = bot.get_guild(guild_id)
            if guild:
                embed = discord.Embed(
                    title="🚨 Double Compte Détecté!",
                    description=f"Un utilisateur a tenté de vérifier avec une IP déjà utilisée.",
                    color=0xFF0000
                )
                embed.add_field(
                    name="Détails",
                    value=f"IP: {ip}\nUtilisateur: <@{user_id}>\nComptes existants: " + 
                          ", ".join(f"<@{alt['user_id']}>" for alt in alt_info[:5])
                )
                
                
                log_channel = discord.utils.get(guild.text_channels, name="logs")
                if log_channel:
                    try:
                        await log_channel.send(embed=embed)
                    except:
                        pass  
                
                
                try:
                    member = guild.get_member(user_id)
                    if member:
                        await member.kick(reason="Double compte détecté")
                        if log_channel:
                            await log_channel.send(f"👢 <@{user_id}> a été kick (double compte).")
                except:
                    pass  
            
            return True, f"Trop de comptes détectés sur cette IP ({len(alts)})", alt_info
        return False, "", []

async def handle_verify(request: web.Request) -> web.Response:
    """Endpoint pour /verify?token=...
    Vérifie l'IP (VPN + alts) et les critères du compte Discord.
    """
    token = request.query.get('token')
    if not token:
        html = render_html_with_delay("Token manquant", "Token manquant", "Le lien de vérification est invalide.",
                                      guild_logo=user_avatar,
                                        guild_name=user_name
)
        return web.Response(text=html, content_type='text/html', status=400)

    
    entry = pending_tokens.pop(token, None)
    if not entry:
        db_entry = pop_pending_token_db(token)
        if db_entry:
            entry = db_entry
    if not entry:
        html = render_html_with_delay("Token invalide", "Token invalide ou expiré", "Le lien de vérification est invalide ou a expiré.",
                guild_logo=user_avatar,
                guild_name=user_name
)
        return web.Response(text=html, content_type='text/html', status=404)

    user_id, guild_id = entry
    
    
    user_avatar, user_name = await get_user_profile(user_id)


    
    ip = request.headers.get("X-Forwarded-For", request.remote)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    logging.info(f"Vérification du token {token} pour l'utilisateur {user_id} depuis IP {ip}")

    
    with sqlite3.connect(DB_PATH) as conn:
        ip_status = conn.execute(
            "SELECT list_type FROM ip_lists WHERE ip_address = ?", 
            (ip,)
        ).fetchone()
        if ip_status and ip_status[0] == 'blacklist':
            html = render_html_with_delay(
                "✅ Vérification réussie",
                "Vérification réussie !",
                "Vous avez maintenant accès au serveur.",
                guild_logo=user_avatar,
                guild_name=user_name
)
            return web.Response(text=html, content_type='text/html', status=403)


    
    if not (ip_status and ip_status[0] == 'whitelist'):
        try:
            is_vpn, raw = await check_ip_vpn(ip)
            if is_vpn:
                logging.info(f"IP {ip} marquée comme VPN/proxy. details={raw}")
                
                try:
                    
                    logs_channel = bot.get_channel(LOGS_CHANNEL_ID)
                    if logs_channel is None:
                        try:
                            logs_channel = await bot.fetch_channel(LOGS_CHANNEL_ID)
                        except Exception:
                            logs_channel = None

                    if logs_channel:
                        guild_obj = bot.get_guild(guild_id)
                        embed = discord.Embed(
                            title="🚨 Blocage: VPN/Proxy détecté",
                            description=f"Une vérification a été bloquée par la détection VPN/Proxy.",
                            color=0xFF0000,
                            timestamp=datetime.datetime.utcnow()
                        )
                        embed.add_field(name="Utilisateur", value="<@{}> ({})".format(user_id, user_id), inline=False)
                        embed.add_field(name="Guild", value="{} ({})".format(guild_obj.name if guild_obj else guild_id, guild_id), inline=False)
                        embed.add_field(name="IP", value=str(ip), inline=True)
                        embed.add_field(name="Checks", value=str(raw), inline=False)
                        embed.add_field(name="Token", value=str(token), inline=True)
                        try:
                            await logs_channel.send(embed=embed)
                        except Exception:
                            logging.exception("Impossible d'envoyer le log détaillé dans le salon #logs verif")
                except Exception:
                    logging.exception("Erreur lors de l'envoi du log détaillé (continuer)")

                html = render_html_with_delay(
                    "Accès refusé",
                    "VPN/proxy détecté",
                    "Votre adresse IP semble être un VPN ou un proxy. Si c'est une erreur, contactez un administrateur.",
                guild_logo=user_avatar,
                guild_name=user_name
                )
                return web.Response(text=html, content_type='text/html', status=403)
        except Exception:
            logging.exception("Erreur lors de la vérification VPN locale (continuer la vérification)")

    
    is_alt, alt_message, alt_accounts = await check_alt_accounts(ip, user_id, guild_id)
    if is_alt:
        logging.warning(f"Double compte détecté pour {user_id}: {alt_message}")
        html = render_html_with_delay("Vérification échouée", "Double compte détecté", f"{alt_message}. Un modérateur vérifiera votre cas.",
                                     details=str(alt_accounts),
                                     guild_logo=user_avatar,
                                     guild_name=user_name
                                     )
        
        await log_verification_refus(
        "Double compte détecté",
        user_id, guild_id, ip,
        extra=json.dumps(alt_accounts, indent=2),
        token=token
    )
        return web.Response(text=html, content_type='text/html', status=403)
    



    guild = bot.get_guild(guild_id)
    if not guild:
        logging.warning(f"Guild {guild_id} non trouvée dans le cache du bot.")
        
        html = render_html_with_delay("Erreur serveur", "Guild non trouvée", "La vérification a échoué (guild non trouvée). Réessayez plus tard.",
                                     guild_logo=user_avatar,
                                     guild_name=user_name
                                     )
        return web.Response(text=html, content_type='text/html')

    member = guild.get_member(user_id)
    if not member:
        logging.warning(f"Membre {user_id} non trouvé dans la guild {guild_id} (peut-être quitté).")
        html = render_html_with_delay("Membre introuvable", "Membre introuvable", "Impossible de trouver votre compte sur le serveur. Avez-vous quitté ?",
                                     guild_logo=user_avatar,
                                     guild_name=user_name
                                     )
        return web.Response(text=html, content_type='text/html')


    # On récupère la vraie date de création du compte (pas juste l'entrée sur le serveur)
    user = await bot.fetch_user(user_id)
    created_at = user.created_at.replace(tzinfo=datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    account_age = (now_utc - created_at).days

    if account_age < MIN_ACCOUNT_AGE_DAYS:
        await log_verification_refus(
            f"Compte trop récent ({account_age} jours)",
            user_id, guild_id, ip,
            extra=f"Âge minimum requis : {MIN_ACCOUNT_AGE_DAYS} jours",
            token=token
        )

        html = render_html_with_delay(
            "Compte trop récent",
            "Compte trop récent",
            f"Votre compte a {account_age} jours. Minimum requis : {MIN_ACCOUNT_AGE_DAYS} jours.",
            guild_logo=user_avatar,
            guild_name=user_name
        )
        logging.info(f"Âge du compte pour {user}: {account_age} jours (minimum requis: {MIN_ACCOUNT_AGE_DAYS})")
        return web.Response(text=html, content_type='text/html', status=403)




    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO verifications (
                user_id, guild_id, ip_address, account_created_at,
                is_vpn, verification_status
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id, guild_id, ip, member.created_at,
            False, 'verified'
        ))

    
    role_name = "Vérifié"
    role = discord.utils.get(guild.roles, name=role_name)
    if role:
        try:
            await member.add_roles(role, reason="Vérification réussie (IP + âge compte OK)")
            logging.info(f"Rôle '{role_name}' ajouté à {member}.")
        except Exception:
            logging.exception("Impossible d'ajouter le rôle au membre.")
            html = render_html_with_delay(
                "Vérification partielle",
                "Rôle non attribué",
                "Vérification réussie mais le rôle n'a pas pu être attribué (permissions). Contactez un admin.",
                guild_logo=user_avatar,
                guild_name=user_name
            )
            return web.Response(text=html, content_type='text/html')
    else:
        logging.warning(
            f"Rôle '{role_name}' introuvable dans la guild {guild.name}. Tentative de création..."
        )
        
        try:
            new_role = await guild.create_role(name=role_name, reason="Création rôle Vérifié pour vérification")
            logging.info(f"Rôle '{role_name}' créé dans la guild {guild.name}.")
            try:
                await member.add_roles(new_role, reason="Vérification réussie (rôle créé)")
                logging.info(f"Rôle '{role_name}' ajouté à {member} après création.")
            except Exception:
                logging.exception("Impossible d'ajouter le rôle nouvellement créé au membre.")
                html = render_html_with_delay(
                    "Vérification partielle",
                    "Rôle non attribué",
                    "Vérification réussie mais le rôle n'a pas pu être attribué (permissions). Contactez un admin.",
                    guild_logo=user_avatar,
                    guild_name=user_name
                )
                return web.Response(text=html, content_type='text/html')
        except Exception:
            logging.exception("Impossible de créer le rôle 'Vérifié' (permissions manquantes?).")
            html = render_html_with_delay(
                "Vérification partielle",
                "Rôle introuvable",
                "Vérification réussie mais le rôle introuvable et la création a échoué (permissions). Contactez un admin.",
                guild_logo=user_avatar,
                guild_name=user_name
            )
            return web.Response(text=html, content_type='text/html')

    html = render_html_with_delay("Vérification réussie", "✅ Vérification réussie!", "Vous avez maintenant accès au serveur.",
                                    guild_logo=user_avatar,
                                    guild_name=user_name
                                    )
    return web.Response(text=html, content_type='text/html')


app.router.add_get('/verify', handle_verify)



async def log_verification_refus(reason: str, user_id: int, guild_id: int, ip: str, extra: str = "", token: str = ""):
        """Envoie un log détaillé dans le salon #logs en cas de refus de vérification."""
        try:
            logs_channel = bot.get_channel(LOGS_CHANNEL_ID)
            if logs_channel is None:
                try:
                    logs_channel = await bot.fetch_channel(LOGS_CHANNEL_ID)
                except Exception:
                    logs_channel = None

            if logs_channel:
                guild_obj = bot.get_guild(guild_id)
                embed = discord.Embed(
                    title="🚫 Vérification refusée",
                    description=f"**Raison :** {reason}",
                    color=0xFF0000,
                    timestamp=datetime.datetime.utcnow()
                )
                embed.add_field(name="Utilisateur", value=f"<@{user_id}> ({user_id})", inline=False)
                embed.add_field(name="IP", value=ip or "Inconnue", inline=True)
                embed.add_field(name="Guild", value=f"{guild_obj.name if guild_obj else guild_id}", inline=False)
                if token:
                    embed.add_field(name="Token", value=token, inline=False)
                if extra:
                    embed.add_field(name="Détails", value=extra[:1000], inline=False)
                await logs_channel.send(embed=embed)
        except Exception:
            logging.exception("Erreur lors de l'envoi du log de refus de vérification")



async def periodic_post_verification():
    """Tâche d'arrière-plan: poste l'embed universel de vérification toutes les 20 minutes."""
    await bot.wait_until_ready()
    logging.info("Periodic poster: démarrage de la boucle de publication d'embed.")
    interval = 20 * 60  
    while not bot.is_closed():
        try:
            embed = discord.Embed(
                title="🔒 Vérification requise",
                description=("Pour accéder au serveur, cliquez sur **Vérifier** ci-dessous. "
                             "pour finaliser la vérification dans votre navigateur."),
                color=0x2ECC71,
            )
            embed.set_footer(text="Ce message est public — le lien de vérification est envoyé en privé lorsque vous cliquez.")

            view = UniversalVerifyView()

            channel = bot.get_channel(VERIF_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(VERIF_CHANNEL_ID)
                except Exception:
                    logging.exception(f"Erreur: canal de vérification {VERIF_CHANNEL_ID} introuvable pour la tâche périodique.")
                    await asyncio.sleep(interval)
                    continue

            try:
                await channel.send(embed=embed, view=view)
                logging.info(f"Message de vérification périodique posté dans {VERIF_CHANNEL_ID}.")
            except Exception:
                logging.exception("Impossible d'envoyer le message de vérification périodique.")

        except Exception:
            logging.exception("Erreur inattendue dans periodic_post_verification")

        await asyncio.sleep(interval)


async def start_web_server():
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
	await site.start()
	logging.info(f"Serveur web démarré sur le port {WEB_PORT} (BASE_URL={BASE_URL})")


async def main():
    
    await start_web_server()
    
    
    if not DISCORD_TOKEN:
        logging.error("DISCORD_TOKEN non défini. Définissez la variable d'environnement DISCORD_TOKEN avant de lancer le bot.")
        logging.error("En PowerShell: $env:DISCORD_TOKEN = 'votre_token' ; python bot.py")
        return

    try:
        await bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logging.error("Impossible de se connecter à Discord. Le token fourni est invalide. Regénérez le token dans le Developer Portal et mettez à jour DISCORD_TOKEN.")
        return


if __name__ == '__main__':

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Arrêt demandé par l'utilisateur.")
    finally:
        try:
            
            if hasattr(bot, 'http_session') and bot.http_session:
                asyncio.get_event_loop().run_until_complete(bot.http_session.close())
        except Exception:
            pass
