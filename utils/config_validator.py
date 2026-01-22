# utils/config_validator.py
# -*- coding: utf-8 -*-
"""
Config validator untuk memvalidasi konfigurasi chain dan mengirim notifikasi ke Discord
jika ada error atau missing parameters.
"""

import logging
import discord
from typing import Dict, List, Tuple


class ConfigValidationResult:
    """Class untuk menyimpan hasil validasi config."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.success: List[str] = []
        self.total_chains = 0
        self.valid_chains = 0
    
    def add_error(self, chain_name: str, message: str):
        """Tambahkan error untuk chain tertentu."""
        self.errors.append(f"**{chain_name}**: {message}")
    
    def add_warning(self, chain_name: str, message: str):
        """Tambahkan warning untuk chain tertentu."""
        self.warnings.append(f"**{chain_name}**: {message}")
    
    def add_success(self, chain_name: str):
        """Tandai chain sebagai valid."""
        self.success.append(chain_name)
        self.valid_chains += 1
    
    def is_valid(self) -> bool:
        """Cek apakah semua chain valid (tidak ada error)."""
        return len(self.errors) == 0
    
    def has_warnings(self) -> bool:
        """Cek apakah ada warnings."""
        return len(self.warnings) > 0


def validate_chain_config(chain_name: str, chain_config: dict) -> Tuple[bool, List[str], List[str]]:
    """
    Validasi konfigurasi untuk satu chain.
    
    Returns:
        Tuple[is_valid, errors, warnings]
    """
    errors = []
    warnings = []
    
    # Required parameters
    required_params = ['rest_api_url', 'decimals']
    
    for param in required_params:
        if param not in chain_config or chain_config[param] is None:
            errors.append(f"Missing required parameter: `{param}`")
    
    # Validate rest_api_url format
    if 'rest_api_url' in chain_config:
        url = chain_config['rest_api_url']
        if not url.startswith('http://') and not url.startswith('https://'):
            errors.append(f"Invalid `rest_api_url` format: must start with http:// or https://")
    
    # Validate decimals
    if 'decimals' in chain_config:
        decimals = chain_config['decimals']
        if not isinstance(decimals, int) or decimals < 0 or decimals > 18:
            errors.append(f"Invalid `decimals` value: must be integer between 0-18")
    
    # Check for parameters that will be auto-discovered
    auto_discover_params = ['valoper_prefix', 'valcons_prefix', 'base_denom', 'token_symbol']
    missing_auto_discover = []
    
    for param in auto_discover_params:
        if param not in chain_config or chain_config[param] is None:
            missing_auto_discover.append(param)
    
    if missing_auto_discover:
        warnings.append(f"Will auto-discover: {', '.join(f'`{p}`' for p in missing_auto_discover)}")
    
    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def validate_all_chains(supported_chains: Dict[str, dict]) -> ConfigValidationResult:
    """
    Validasi semua chain dalam konfigurasi.
    
    Args:
        supported_chains: Dict dari chain configs
    
    Returns:
        ConfigValidationResult dengan hasil validasi
    """
    result = ConfigValidationResult()
    result.total_chains = len(supported_chains)
    
    logging.info(f"Validating {result.total_chains} chains...")
    
    for chain_name, chain_config in supported_chains.items():
        is_valid, errors, warnings = validate_chain_config(chain_name, chain_config)
        
        if is_valid:
            result.add_success(chain_name)
            logging.info(f"[{chain_name}] Validation passed")
        else:
            for error in errors:
                result.add_error(chain_name, error)
                logging.error(f"[{chain_name}] {error}")
        
        for warning in warnings:
            result.add_warning(chain_name, warning)
            logging.warning(f"[{chain_name}] {warning}")
    
    return result


def validate_post_discovery(supported_chains: Dict[str, dict]) -> ConfigValidationResult:
    """
    Validasi SETELAH auto-discovery untuk memastikan semua parameter critical tersedia.
    
    Args:
        supported_chains: Dict dari chain configs (sudah di-enrich dengan auto-discovery)
    
    Returns:
        ConfigValidationResult dengan hasil validasi post-discovery
    """
    result = ConfigValidationResult()
    result.total_chains = len(supported_chains)
    
    logging.info("Validating post-discovery parameters...")
    
    # Critical parameters yang HARUS ada (baik manual atau auto-discovered)
    critical_params = ['valoper_prefix', 'valcons_prefix', 'base_denom', 'token_symbol']
    
    for chain_name, chain_config in supported_chains.items():
        chain_valid = True
        missing_critical = []
        
        for param in critical_params:
            if param not in chain_config or chain_config[param] is None:
                missing_critical.append(param)
                chain_valid = False
        
        if chain_valid:
            result.add_success(chain_name)
            logging.info(f"[{chain_name}] Post-discovery validation passed")
        else:
            error_msg = f"Missing critical parameters (auto-discovery failed): {', '.join(f'`{p}`' for p in missing_critical)}"
            result.add_error(chain_name, error_msg)
            logging.error(f"[{chain_name}] {error_msg}")
    
    return result


def create_validation_embed(result: ConfigValidationResult) -> discord.Embed:
    """
    Buat Discord embed untuk hasil validasi.
    
    Args:
        result: ConfigValidationResult
    
    Returns:
        discord.Embed
    """
    # Tentukan warna dan title berdasarkan hasil
    if result.is_valid():
        if result.has_warnings():
            color = discord.Color.orange()
            title = "⚠️ Configuration Validated with Warnings"
        else:
            color = discord.Color.green()
            title = "✅ Configuration Validated Successfully"
    else:
        color = discord.Color.red()
        title = "❌ Configuration Validation Failed"
    
    embed = discord.Embed(
        title=title,
        description=f"Validated **{result.total_chains}** chains at startup",
        color=color
    )
    
    # Summary
    summary_lines = [
        f"✅ Valid: **{result.valid_chains}**",
        f"❌ Errors: **{len(result.errors)}**",
        f"⚠️ Warnings: **{len(result.warnings)}**"
    ]
    embed.add_field(name="Summary", value="\n".join(summary_lines), inline=False)
    
    # Valid chains
    if result.success:
        chains_text = ", ".join(f"`{c}`" for c in result.success)
        if len(chains_text) > 1024:
            chains_text = chains_text[:1020] + "..."
        embed.add_field(name="✅ Valid Chains", value=chains_text, inline=False)
    
    # Errors
    if result.errors:
        errors_text = "\n".join(result.errors[:10])  # Limit to 10 errors
        if len(result.errors) > 10:
            errors_text += f"\n... and {len(result.errors) - 10} more errors"
        if len(errors_text) > 1024:
            errors_text = errors_text[:1020] + "..."
        embed.add_field(name="❌ Errors", value=errors_text, inline=False)
    
    # Warnings
    if result.warnings:
        warnings_text = "\n".join(result.warnings[:10])  # Limit to 10 warnings
        if len(result.warnings) > 10:
            warnings_text += f"\n... and {len(result.warnings) - 10} more warnings"
        if len(warnings_text) > 1024:
            warnings_text = warnings_text[:1020] + "..."
        embed.add_field(name="⚠️ Warnings", value=warnings_text, inline=False)
    
    embed.set_footer(text="Bot Startup Validation")
    
    return embed




async def send_validation_notification(bot, result: ConfigValidationResult, notification_channel_id: int = None):
    """
    Kirim notifikasi validasi ke Discord channel.
    
    Args:
        bot: Bot instance
        result: ConfigValidationResult
        notification_channel_id: Optional channel ID untuk notifikasi (default: first text channel)
    """
    try:
        logging.info("=" * 60)
        logging.info("Attempting to send validation notification to Discord...")
        logging.info(f"Bot guilds count: {len(bot.guilds)}")
        
        # Jika tidak ada channel ID, cari channel pertama yang bisa diakses
        if notification_channel_id:
            channel = bot.get_channel(notification_channel_id)
            logging.info(f"Using specified channel ID: {notification_channel_id}")
        else:
            # Cari channel pertama yang bisa diakses
            channel = None
            for guild in bot.guilds:
                logging.info(f"Checking guild: {guild.name} (ID: {guild.id})")
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        logging.info(f"Found accessible channel: {ch.name} (ID: {ch.id}) in guild {guild.name}")
                        break
                if channel:
                    break
        
        if not channel:
            logging.error("=" * 60)
            logging.error("CRITICAL: No accessible channel found for validation notification!")
            logging.error("Please ensure:")
            logging.error("1. Bot is invited to at least one Discord server")
            logging.error("2. Bot has 'Send Messages' permission in at least one channel")
            logging.error("=" * 60)
            return
        
        logging.info(f"Sending validation notification to channel: {channel.name} (ID: {channel.id})")
        
        embed = create_validation_embed(result)
        
        # Send with error handling
        message = await channel.send(embed=embed)
        
        logging.info("=" * 60)
        logging.info(f"✅ Validation notification sent successfully!")
        logging.info(f"   Channel: {channel.name}")
        logging.info(f"   Guild: {channel.guild.name}")
        logging.info(f"   Message ID: {message.id}")
        logging.info("=" * 60)
        
    except discord.Forbidden as e:
        logging.error(f"❌ Permission denied to send message: {e}")
        logging.error("   Bot may not have 'Send Messages' or 'Embed Links' permission")
    except discord.HTTPException as e:
        logging.error(f"❌ HTTP error while sending notification: {e}")
    except Exception as e:
        logging.error(f"❌ Unexpected error sending validation notification: {type(e).__name__}: {e}")
        import traceback
        logging.error(traceback.format_exc())

