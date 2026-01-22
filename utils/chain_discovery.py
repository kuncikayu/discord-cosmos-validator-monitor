# utils/chain_discovery.py
# -*- coding: utf-8 -*-
"""
Auto-discovery utility untuk mendeteksi parameter chain dari RPC/API.
Mendukung flexible fallback ke manual config jika discovery gagal.
"""

import logging
import re
from typing import Optional, Dict, Any

import httpx


async def discover_chain_params(
    async_client: httpx.AsyncClient,
    rest_api_url: str,
    chain_name: str
) -> Dict[str, Optional[str]]:
    """
    Auto-discover chain parameters dari REST API.
    
    Returns:
        Dict dengan keys: valoper_prefix, valcons_prefix, base_denom, token_symbol
        Value None jika gagal discover parameter tersebut.
    """
    discovered = {
        'valoper_prefix': None,
        'valcons_prefix': None,
        'base_denom': None,
        'token_symbol': None
    }
    
    logging.info(f"[{chain_name}] Starting auto-discovery from {rest_api_url}")
    
    # 1. Fetch base_denom dari staking params
    discovered['base_denom'] = await fetch_base_denom(async_client, rest_api_url, chain_name)
    
    # 2. Derive token_symbol dari base_denom
    if discovered['base_denom']:
        discovered['token_symbol'] = derive_token_symbol(discovered['base_denom'])
    
    # 3. Extract prefixes dari validator addresses
    prefixes = await extract_prefixes_from_validators(async_client, rest_api_url, chain_name)
    if prefixes:
        discovered['valoper_prefix'] = prefixes.get('valoper_prefix')
        discovered['valcons_prefix'] = prefixes.get('valcons_prefix')
    
    # Log hasil discovery
    success_count = sum(1 for v in discovered.values() if v is not None)
    logging.info(f"[{chain_name}] Auto-discovery completed: {success_count}/4 parameters discovered")
    for key, value in discovered.items():
        if value:
            logging.info(f"[{chain_name}]   ✓ {key}: {value}")
        else:
            logging.warning(f"[{chain_name}]   ✗ {key}: Failed to discover")
    
    return discovered


async def fetch_base_denom(
    async_client: httpx.AsyncClient,
    rest_api_url: str,
    chain_name: str
) -> Optional[str]:
    """
    Fetch base denomination dari staking params.
    
    Endpoint: /cosmos/staking/v1beta1/params
    Returns: bond_denom (e.g., "uatom", "uempe")
    """
    try:
        url = f"{rest_api_url}/cosmos/staking/v1beta1/params"
        response = await async_client.get(url)
        response.raise_for_status()
        
        data = response.json()
        bond_denom = data.get('params', {}).get('bond_denom')
        
        if bond_denom:
            logging.debug(f"[{chain_name}] Discovered base_denom: {bond_denom}")
            return bond_denom
        else:
            logging.warning(f"[{chain_name}] bond_denom not found in staking params")
            return None
            
    except Exception as e:
        logging.error(f"[{chain_name}] Failed to fetch base_denom: {e}")
        return None


def derive_token_symbol(base_denom: str) -> str:
    """
    Derive token symbol dari base denomination.
    
    Examples:
        "uatom" -> "ATOM"
        "uempe" -> "EMPE"
        "ulume" -> "LUME"
        "ahp" -> "HP"
    
    Logic:
        - Remove common prefixes: u, a, n, m
        - Uppercase the result
    """
    # Remove common micro/atto/nano/milli prefixes
    prefixes = ['u', 'a', 'n', 'm']
    symbol = base_denom
    
    for prefix in prefixes:
        if symbol.startswith(prefix) and len(symbol) > 1:
            symbol = symbol[1:]
            break
    
    return symbol.upper()


async def extract_prefixes_from_validators(
    async_client: httpx.AsyncClient,
    rest_api_url: str,
    chain_name: str
) -> Optional[Dict[str, str]]:
    """
    Extract valoper_prefix dan valcons_prefix dari validator addresses.
    
    Endpoint: /cosmos/staking/v1beta1/validators?pagination.limit=1
    Returns: {valoper_prefix: str, valcons_prefix: str}
    """
    try:
        # Ambil 1 validator saja untuk extract prefix
        url = f"{rest_api_url}/cosmos/staking/v1beta1/validators?pagination.limit=1"
        response = await async_client.get(url)
        response.raise_for_status()
        
        data = response.json()
        validators = data.get('validators', [])
        
        if not validators:
            logging.warning(f"[{chain_name}] No validators found for prefix extraction")
            return None
        
        validator = validators[0]
        
        # Extract valoper_prefix dari operator_address
        operator_address = validator.get('operator_address')
        valoper_prefix = extract_bech32_prefix(operator_address)
        
        # Extract valcons_prefix dari consensus_pubkey
        # Kita perlu convert pubkey ke consensus address
        consensus_pubkey = validator.get('consensus_pubkey', {})
        valcons_prefix = extract_consensus_prefix(valoper_prefix)
        
        if valoper_prefix and valcons_prefix:
            logging.debug(f"[{chain_name}] Extracted prefixes - valoper: {valoper_prefix}, valcons: {valcons_prefix}")
            return {
                'valoper_prefix': valoper_prefix,
                'valcons_prefix': valcons_prefix
            }
        else:
            logging.warning(f"[{chain_name}] Failed to extract prefixes from validator data")
            return None
            
    except Exception as e:
        logging.error(f"[{chain_name}] Failed to extract prefixes: {e}")
        return None


def extract_bech32_prefix(address: str) -> Optional[str]:
    """
    Extract prefix dari bech32 address.
    
    Example:
        "cosmosvaloper1abc..." -> "cosmosvaloper"
        "empevaloper1xyz..." -> "empevaloper"
    """
    if not address:
        return None
    
    # Bech32 format: prefix + separator (1) + data
    # Prefix adalah semua karakter sebelum '1'
    match = re.match(r'^([a-z]+)1', address)
    if match:
        return match.group(1)
    
    return None


def extract_consensus_prefix(valoper_prefix: Optional[str]) -> Optional[str]:
    """
    Derive consensus prefix dari validator operator prefix.
    
    Pattern:
        "cosmosvaloper" -> "cosmosvalcons"
        "empevaloper" -> "empevalcons"
        "lumeravaloper" -> "lumeravalcons"
    
    Logic: Replace "valoper" with "valcons"
    """
    if not valoper_prefix:
        return None
    
    if 'valoper' in valoper_prefix:
        return valoper_prefix.replace('valoper', 'valcons')
    
    # Fallback: jika tidak ada pattern valoper, return None
    return None


def merge_discovered_with_config(
    discovered: Dict[str, Optional[str]],
    manual_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge hasil auto-discovery dengan manual config.
    Manual config memiliki prioritas lebih tinggi (override discovery).
    
    Args:
        discovered: Hasil dari discover_chain_params()
        manual_config: Config dari YAML
    
    Returns:
        Merged config dengan prioritas: manual > discovered > None
    """
    merged = manual_config.copy()
    
    # Untuk setiap parameter yang bisa di-discover
    for param in ['valoper_prefix', 'valcons_prefix', 'base_denom', 'token_symbol']:
        # Jika tidak ada di manual config dan ada di discovered, gunakan discovered
        if param not in merged or merged[param] is None:
            if discovered.get(param):
                merged[param] = discovered[param]
                logging.debug(f"Using discovered value for {param}: {discovered[param]}")
    
    return merged
