# Database Migrations

This directory contains SQL migration files that are applied automatically during startup.

## Migration Naming Convention

Migration files should be named with a numeric prefix to ensure they run in the correct order:
- `001_initial_schema.sql`
- `002_add_agent_identifier.sql`
- `003_add_jobs_columns.sql`

## How Migrations Work

1. When you run `startup.sh`, it automatically applies all migrations in this directory
2. Migrations are applied in alphabetical order (hence the numeric prefix)
3. Each migration should use `IF NOT EXISTS` clauses to be idempotent
4. The migration script continues even if individual migrations fail (to handle already-applied migrations)

## Adding New Migrations

1. Create a new `.sql` file with the next number in sequence
2. Use `IF NOT EXISTS` for all DDL operations
3. Test locally before deploying
4. Commit the migration file to version control

## Current Migrations

1. **001_add_agent_identifier.sql** - Adds `agent_identifier` column to flows table
2. **002_add_jobs_columns.sql** - Adds `kodosumi_start_attempts` and `waiting_for_start_in_kodosumi` columns to jobs table