#!/usr/bin/with-contenv bashio

export ANTHROPIC_API_KEY="$(bashio::config 'anthropic_key')"
export TELEGRAM_TOKEN="$(bashio::config 'telegram_token')"
export GROQ_API_KEY="$(bashio::config 'groq_key')"
export HA_URL="$(bashio::config 'ha_url')"
export HA_TOKEN="$(bashio::config 'ha_token')"
export LOG_LEVEL="$(bashio::config 'log_level')"
export CONFIG_PATH="/data/options.json"

cd /app
exec python3 main.py
