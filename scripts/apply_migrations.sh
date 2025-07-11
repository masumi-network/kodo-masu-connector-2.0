#!/bin/bash

echo "Applying database migrations..."

# Wait for database to be ready
until docker exec kodosumi-postgres pg_isready -U kodosumi_user -d kodosumi_db -q; do
    echo "Waiting for database to be ready..."
    sleep 2
done

echo "Database is ready. Applying migrations..."

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MIGRATION_DIR="${SCRIPT_DIR}/../database/migrations"

# Check if migrations directory exists
if [ ! -d "$MIGRATION_DIR" ]; then
    echo "Warning: Migrations directory not found at $MIGRATION_DIR"
    exit 0
fi

# Apply all migrations in order (sorted by filename)
for migration in $(ls -1 "$MIGRATION_DIR"/*.sql 2>/dev/null | sort); do
    migration_name=$(basename "$migration")
    echo "Applying migration: $migration_name"
    
    cat "$migration" | docker exec -i kodosumi-postgres psql -U kodosumi_user -d kodosumi_db -q
    
    if [ $? -eq 0 ]; then
        echo "✓ $migration_name applied successfully"
    else
        echo "✗ Failed to apply $migration_name"
        # Continue with other migrations even if one fails
    fi
done

echo "All migrations completed!"