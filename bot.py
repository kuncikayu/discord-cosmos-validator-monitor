# bot.py
# -*- coding: utf-8 -*-

# --- Standard Library Imports ---
import asyncio
import logging
import os

# --- Third-Party Imports ---
import discord
import httpx
import yaml
from discord.ext import commands
from dotenv import load_dotenv

# --- Local Imports ---
import db_manager

# --- Initial Setup ---
load_dotenv()

# Konfigurasi logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Fungsi untuk memuat konfigurasi ---
def load_config(config_file='config.yaml'):
    """Memuat konfigurasi chains dari file YAML dengan dukungan defaults dan auto-discovery."""
    try:
        with open(config_file, 'r') as f:
            raw_config = yaml.safe_load(f)
            
            # Cek apakah menggunakan format baru (dengan 'defaults' dan 'chains')
            if 'defaults' in raw_config and 'chains' in raw_config:
                defaults = raw_config.get('defaults', {})
                chains = raw_config.get('chains', {})
                
                # Merge defaults dengan setiap chain config
                merged_config = {}
                for chain_name, chain_config in chains.items():
                    # Mulai dengan defaults, lalu override dengan chain-specific values
                    merged_config[chain_name] = {**defaults, **chain_config}
                
                logging.info(f"Configuration loaded successfully from {config_file} (new format with defaults).")
                logging.info(f"Loaded {len(merged_config)} chains: {', '.join(merged_config.keys())}")
                return merged_config
            else:
                # Format lama (backward compatible)
                logging.info(f"Configuration loaded successfully from {config_file} (legacy format).")
                return raw_config
                
    except FileNotFoundError:
        logging.critical(f"FATAL: Configuration file '{config_file}' not found. The bot cannot start.")
        exit(1)
    except yaml.YAMLError as e:
        logging.critical(f"FATAL: Error parsing '{config_file}': {e}. The bot cannot start.")
        exit(1)

async def enrich_config_with_discovery(bot_instance):
    """
    Enrich loaded config dengan auto-discovery untuk parameter yang missing.
    Dipanggil setelah bot instance dibuat (karena butuh async_client).
    
    Args:
        bot_instance: Instance dari CosmosMonitorBot yang sudah punya async_client
    """
    from utils.chain_discovery import discover_chain_params, merge_discovered_with_config
    
    logging.info("Starting chain parameter auto-discovery...")
    
    for chain_name, chain_config in bot_instance.supported_chains.items():
        # Cek parameter yang perlu di-discover
        missing_params = []
        for param in ['valoper_prefix', 'valcons_prefix', 'base_denom', 'token_symbol']:
            if param not in chain_config or chain_config[param] is None:
                missing_params.append(param)
        
        if not missing_params:
            logging.info(f"[{chain_name}] All parameters present, skipping auto-discovery")
            continue
        
        logging.info(f"[{chain_name}] Missing parameters: {', '.join(missing_params)}")
        
        # Cek cache terlebih dahulu
        cached = db_manager.get_cached_chain_params(chain_name)
        if cached and cached.get('rest_api_url') == chain_config.get('rest_api_url'):
            logging.info(f"[{chain_name}] Using cached parameters from database")
            discovered = {
                'valoper_prefix': cached.get('valoper_prefix'),
                'valcons_prefix': cached.get('valcons_prefix'),
                'base_denom': cached.get('base_denom'),
                'token_symbol': cached.get('token_symbol')
            }
        else:
            # Lakukan auto-discovery
            logging.info(f"[{chain_name}] Performing auto-discovery...")
            discovered = await discover_chain_params(
                bot_instance.async_client,
                chain_config['rest_api_url'],
                chain_name
            )
            
            # Cache hasil discovery jika ada yang berhasil
            if any(discovered.values()):
                db_manager.cache_chain_params(chain_name, discovered, chain_config['rest_api_url'])
        
        # Merge discovered dengan config (manual config tetap prioritas)
        bot_instance.supported_chains[chain_name] = merge_discovered_with_config(
            discovered,
            chain_config
        )
    
    logging.info("Chain parameter auto-discovery completed")

# --- Class Bot Utama ---
class CosmosMonitorBot(commands.Bot):
    """
    Class turunan dari commands.Bot untuk merangkum fungsionalitas bot,
    termasuk konfigurasi dan klien HTTP.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_chains = load_config()
        self.async_client = httpx.AsyncClient(timeout=20.0)
        logging.info("CosmosMonitorBot initialized.")

    async def setup_hook(self):
        """
        Hook ini dijalankan setelah login bot dan sebelum terhubung ke WebSocket.
        Digunakan untuk memuat ekstensi (cogs), auto-discovery, dan validasi config.
        """
        logging.info("Running setup_hook...")
        
        # 1. Validasi konfigurasi terlebih dahulu
        from utils.config_validator import validate_all_chains, send_validation_notification
        
        logging.info("=" * 60)
        logging.info("STEP 1: Validating configuration...")
        logging.info("=" * 60)
        
        validation_result = validate_all_chains(self.supported_chains)
        
        # Kirim notifikasi validasi ke Discord (akan dikirim ke channel pertama yang accessible)
        # Notifikasi akan dikirim setelah bot ready
        self._validation_result = validation_result
        
        if not validation_result.is_valid():
            logging.error("Configuration validation failed! Check Discord for details.")
            # Bot tetap jalan, tapi akan kirim notif error ke Discord
        elif validation_result.has_warnings():
            logging.warning("Configuration validated with warnings. Check Discord for details.")
        else:
            logging.info("Configuration validated successfully!")
        
        # 2. Auto-discovery chain parameters (jika ada yang missing)
        logging.info("=" * 60)
        logging.info("STEP 2: Auto-discovering chain parameters...")
        logging.info("=" * 60)
        
        await enrich_config_with_discovery(self)
        
        # 2b. Validasi SETELAH auto-discovery (untuk catch parameter yang gagal di-discover)
        logging.info("=" * 60)
        logging.info("STEP 2b: Post-discovery validation...")
        logging.info("=" * 60)
        
        from utils.config_validator import validate_post_discovery
        
        post_discovery_result = validate_post_discovery(self.supported_chains)
        
        # Simpan hasil untuk notifikasi (akan di-merge dengan pre-discovery result)
        if hasattr(self, '_validation_result'):
            # Merge errors dari post-discovery ke pre-discovery result
            for error in post_discovery_result.errors:
                self._validation_result.errors.append(error)
            # Update valid chains count
            self._validation_result.valid_chains = post_discovery_result.valid_chains
        else:
            self._validation_result = post_discovery_result
        
        if not post_discovery_result.is_valid():
            logging.error("Post-discovery validation failed! Some parameters could not be auto-discovered.")
        else:
            logging.info("Post-discovery validation passed!")
        
        # 3. Memuat semua file .py dari direktori 'cogs'
        logging.info("=" * 60)
        logging.info("STEP 3: Loading cogs...")
        logging.info("=" * 60)
        
        cogs_loaded = 0
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logging.info(f"Successfully loaded cog: {filename}")
                    cogs_loaded += 1
                except Exception as e:
                    logging.error(f"Failed to load cog {filename}: {type(e).__name__} - {e}")
        
        logging.info(f"Completed loading {cogs_loaded} cogs.")

        # 4. Sinkronisasi slash commands secara global setelah semua cogs dimuat
        logging.info("=" * 60)
        logging.info("STEP 4: Syncing slash commands...")
        logging.info("=" * 60)
        
        try:
            synced = await self.tree.sync()
            logging.info(f"Successfully synced {len(synced)} application commands globally.")
        except Exception as e:
            logging.error(f"Failed to sync application commands: {e}")

    async def on_ready(self):
        """Event yang dijalankan saat bot siap beroperasi."""
        logging.info(f'Logged in as {self.user.name} ({self.user.id})')
        logging.info('Bot is ready and online!')
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Validator Performance"))
        
        # Kirim notifikasi validasi config ke Discord (jika ada)
        if hasattr(self, '_validation_result'):
            logging.info("Validation result found, preparing to send notification...")
            from utils.config_validator import send_validation_notification
            
            # Get notification channel ID from environment variable (optional)
            notification_channel_id = os.getenv('NOTIFICATION_CHANNEL_ID')
            if notification_channel_id:
                try:
                    notification_channel_id = int(notification_channel_id)
                    logging.info(f"Using notification channel ID from .env: {notification_channel_id}")
                except ValueError:
                    logging.warning(f"Invalid NOTIFICATION_CHANNEL_ID in .env: {notification_channel_id}")
                    notification_channel_id = None
            
            await send_validation_notification(self, self._validation_result, notification_channel_id)
        else:
            logging.warning("No validation result found to send notification")

    async def on_close(self):
        """Event untuk membersihkan resource saat bot ditutup."""
        logging.info("Closing bot... Closing HTTP client session.")
        await self.async_client.aclose()


# --- Main Execution Block ---
if __name__ == '__main__':
    DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not DISCORD_BOT_TOKEN:
        logging.critical("DISCORD_BOT_TOKEN environment variable not set. Please create a .env file or export it.")
        exit(1)

    # Inisialisasi database
    try:
        db_manager.init_db()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.critical(f"Failed to initialize database: {e}")
        exit(1)
        
    # Menyiapkan intents
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    
    # Membuat instance dan menjalankan bot
    bot = CosmosMonitorBot(command_prefix='!', intents=intents)
    
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login Failed. Please ensure your Discord Bot Token is correct.")
    except Exception as e:
        logging.critical(f"An unexpected error occurred while running the bot: {e}")