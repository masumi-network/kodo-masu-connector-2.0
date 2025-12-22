#!/bin/bash

echo "Running database migration to fix flow sync duplicates..."

# Check if running in Docker or local
if [ -f /.dockerenv ]; then
    # We're inside a Docker container
    DB_HOST="${POSTGRES_HOST:-postgres}"
else
    # We're running locally
    DB_HOST="${POSTGRES_HOST:-localhost}"
fi

# Run the migration
docker exec -i kodosumi-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < database/migrations/004_add_summary_unique_constraint.sql

if [ $? -eq 0 ]; then
    echo "Migration completed successfully!"
    echo "Database now uses flow summary as the unique key."
else
    echo "Migration failed. Please check the error messages above."
    exit 1
fi