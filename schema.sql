-- Schema pour la base de données de détection de doubles comptes
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT NOT NULL,           -- Discord user ID
    guild_id BIGINT NOT NULL,          -- Discord server ID
    ip_address TEXT NOT NULL,          -- IP de vérification
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_created_at TIMESTAMP,      -- Date création compte Discord
    is_vpn BOOLEAN,                    -- Si détecté comme VPN
    shared_servers INTEGER DEFAULT 0,   -- Nombre de serveurs en commun avec d'autres comptes
    verification_status TEXT           -- 'pending', 'verified', 'blocked_vpn', 'blocked_alt', etc.
);

CREATE INDEX IF NOT EXISTS idx_ip_address ON verifications(ip_address);
CREATE INDEX IF NOT EXISTS idx_user_guild ON verifications(user_id, guild_id);

-- Table pour les IPs en whitelist/blacklist
CREATE TABLE IF NOT EXISTS ip_lists (
    ip_address TEXT PRIMARY KEY,
    list_type TEXT NOT NULL,          -- 'whitelist' ou 'blacklist'
    added_by BIGINT,                  -- Discord ID de l'admin
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT
);