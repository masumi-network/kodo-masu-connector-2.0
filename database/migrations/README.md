# Database Migrations

This directory contains SQL migration files that are applied automatically during startup for **existing** databases.
New installations now pull the complete schema from `database/init.sql`, so you can provision a fresh environment with a single `docker compose up --build -d` and the system will bootstrap itself (API keys, flow sync, etc.).

## Migration Naming Convention

Migration files should be named with a numeric prefix to ensure they run in the correct order:
- `001_initial_schema.sql`
- `002_add_agent_identifier.sql`
- `003_add_jobs_columns.sql`

## How Migrations Work

1. When you run `startup.sh`, it automatically applies all migrations in this directory (mostly relevant when upgrading a previously deployed database)
2. Migrations are applied in alphabetical order (hence the numeric prefix)
3. Each migration should use `IF NOT EXISTS` clauses to be idempotent
4. The migration script continues even if individual migrations fail (to handle already-applied migrations)

## Adding New Migrations

1. Create a new `.sql` file with the next number in sequence
2. Use `IF NOT EXISTS` for all DDL operations
3. Test locally before deploying
4. Commit the migration file to version control

## Current Migrations

1. **001_add_agent_identifier.sql** - Adds `agent_identifier` column to flows table (already included for new installs)
2. **002_add_jobs_columns.sql** - Adds `kodosumi_start_attempts` and `waiting_for_start_in_kodosumi` columns to jobs table (already included for new installs)
3. **003_add_cron_executions.sql** - Creates `cron_executions` table used by the admin dashboard (already included for new installs)
4. **004_add_summary_unique_constraint.sql** - Enforces unique flow summaries and deduplicates legacy data
5. **005_add_flow_agent_columns.sql** - Adds agent metadata columns to `flows`
6. **006_add_agent_identifier_used.sql** - Adds `agent_identifier_used` column to `jobs`
7. **007_add_payment_required.sql** - Aligns existing databases with the new `payment_required` flag on `jobs`

These remain available so upgrades retain historical data, but greenfield deployments do not need to run them manually.
