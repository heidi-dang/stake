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

def stake_derive_roll(server_seed: str, client_seed: str, nonce: int) -> float:
    """
    Exact replication of Stake.com Dice HMAC-SHA256 algorithm.
    """
    message = f"{client_seed}:{nonce}"
    h = hmac.new(server_seed.encode(), message.encode(), hashlib.sha256).hexdigest()
    
    # Extract 4 nibble groups of 8 hex chars each, take first that gives < 10000
    for i in range(4):
        segment = h[i*8:(i*8)+8]
        val = int(segment, 16)
        result = val % 10001
        if result <= 10000:
            return result / 100.0
    return 50.0

def dragon_tower_derive_game(server_seed, client_seed, nonce, difficulty):
    """
    Implements Dragon Tower map derivation based on Stake Pf rules.
    Exactly replicates the tower layout.
    """
    diff_map = {
        'easy': {'eggs': 1, 'size': 4},
        'medium': {'eggs': 1, 'size': 3},
        'hard': {'eggs': 1, 'size': 2},
        'expert': {'eggs': 2, 'size': 3},
        'master': {'eggs': 3, 'size': 4}
    }
    cfg = diff_map.get(difficulty.lower(), diff_map['easy'])
    eggs_per_row = cfg['eggs']
    tiles_per_row = cfg['size']
    
    tower = []
    
    def get_float(index):
        round_num = index // 8
        byte_offset = (index % 8) * 4
        message = f"{client_seed}:{nonce}:{round_num}"
        h = hmac.new(server_seed.encode(), message.encode(), hashlib.sha256).digest()
        
        # 4 bytes to float
        bytes_part = h[byte_offset:byte_offset+4]
        val = 0
        for i, b in enumerate(bytes_part):
            val += b / (256**(i+1))
        return val

    float_cursor = 0
    for row_idx in range(9):
        # Initialize row
        row_data = [{'is_egg': False} for _ in range(tiles_per_row)]
        
        # Determine egg positions using Fisher-Yates approach per Stake rules
        available_indices = list(range(tiles_per_row))
        for _ in range(eggs_per_row):
            f_val = get_float(float_cursor)
            float_cursor += 1
            pos_idx = int(f_val * len(available_indices))
            egg_pos = available_indices.pop(pos_idx)
            row_data[egg_pos]['is_egg'] = True
            
        tower.append(row_data)
        
    return tower
        
    return tower

def generate_new_seeds():
    return {
        'server_seed': hashlib.sha256(os.urandom(32)).hexdigest(),
        'client_seed': f"gork-{random.randint(100000,999999)}-{int(time.time())}",
        'nonce': 0
    }
