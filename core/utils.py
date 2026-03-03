import hmac
import hashlib
import os
import random
import time

def calculate_ema(data, period):
    if not data: return None
    if len(data) < period: return sum(data) / len(data)
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for val in data[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def stake_derive_roll(server_seed, client_seed, nonce):
    """
    Implements Stake.com's provably fair dice roll derivation.
    """
    message = f"{client_seed}:{nonce}:0".encode()
    k = server_seed.encode()
    h = hmac.new(k, message, hashlib.sha256).hexdigest()
    
    # 8 chars (32 bits) hex to float
    for i in range(0, len(h), 8):
        val = int(h[i:i+8], 16)
        if val < 4294967295: # 2^32 - 1
            return (val % 1000001) / 10000.0
    return 0.0

def dragon_tower_derive_game(server_seed, client_seed, nonce, difficulty):
    """
    Implements Dragon Tower map derivation based on Stake Pf rules.
    """
    # Simplified mock for now, but following the logic structure
    rows = 9
    tiles_per_row = {
        'easy': 4,
        'medium': 3,
        'hard': 2,
        'expert': 3,
        'master': 4
    }.get(difficulty, 4)
    
    eggs_per_row = {
        'expert': 2,
        'master': 3
    }.get(difficulty, 1)
    
    tower = []
    for row_idx in range(rows):
        message = f"{client_seed}:{nonce}:{row_idx}".encode()
        k = server_seed.encode()
        h = hmac.new(k, message, hashlib.sha256).hexdigest()
        
        # Determine egg positions via Fisher-Yates or simple modulo for mock
        row_data = []
        for i in range(tiles_per_row):
            row_data.append({'is_egg': False})
            
        # Mock egg placement
        egg_pos = int(h[:8], 16) % tiles_per_row
        row_data[egg_pos]['is_egg'] = True
        
        if eggs_per_row > 1:
            egg_pos2 = int(h[8:16], 16) % tiles_per_row
            row_data[egg_pos2]['is_egg'] = True
            
        tower.append(row_data)
        
    return tower

def generate_new_seeds():
    return {
        'server_seed': hashlib.sha256(os.urandom(32)).hexdigest(),
        'client_seed': f"gork-{random.randint(100000,999999)}-{int(time.time())}",
        'nonce': 0
    }
