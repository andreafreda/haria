#!/usr/bin/with-contenv bashio

# /config/haria_options.json (File Editor) takes priority over Supervisor options
if [ -f "/config/haria_options.json" ]; then
    bashio::log.info "HARIA: loading config from /config/haria_options.json"
    export CONFIG_PATH="/config/haria_options.json"
else
    bashio::log.info "HARIA: loading config from Supervisor options"
    export CONFIG_PATH="/data/options.json"
fi

export LOG_LEVEL="${LOG_LEVEL:-info}"

cd /app
exec python3 main.py
