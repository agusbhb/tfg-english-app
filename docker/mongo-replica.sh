#!/bin/bash
set -Eeuo pipefail
# Clave interna efímera, creada fuera del repositorio. Un solo miembro local.
head -c 756 /dev/urandom | base64 | tr -d '\n' > /tmp/trainer-replica.key
chown mongodb:mongodb /tmp/trainer-replica.key
chmod 400 /tmp/trainer-replica.key
exec /usr/local/bin/docker-entrypoint.sh mongod --replSet "$MONGO_REPLICA_SET" --bind_ip_all --keyFile /tmp/trainer-replica.key
