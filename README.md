# Kodosumi Service

A multi-component service for managing Kodosumi API authentication and providing MIP-003 compliant agentic service endpoints with PostgreSQL database storage.

## Components

1. **PostgreSQL Database**: Stores API keys, flows, and job data with timestamps
2. **Authenticator Service**: Runs every 10 hours to fetch new API keys from Kodosumi server
3. **Flow Sync Service**: Synchronizes flows from Kodosumi and converts them to MIP-003 format
4. **API Server**: Provides MIP-003 compliant endpoints for agentic service interactions
5. **Admin Dashboard**: Web interface for managing flows and monitoring jobs

## Setup

1. Copy the example environment file and configure it:
   ```bash
   cp .env.example .env
   ```

2. Configure the `.env` file with your settings:
   ```
   # Database Configuration
   POSTGRES_HOST=postgres
   POSTGRES_PORT=5432
   POSTGRES_DB=kodosumi
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your_postgres_password

   # Kodosumi API Configuration
   KODOSUMI_SERVER_URL=http://localhost:3370
   KODOSUMI_USERNAME=admin
   KODOSUMI_PASSWORD=admin

   # Masumi Payment Service Configuration
   PAYMENT_SERVICE_URL=https://api.masumi.network
   PAYMENT_API_KEY=your_masumi_payment_api_key
   AGENT_IDENTIFIER=kodosumi-service
   NETWORK=Preprod
   ```

3. Run the startup script:
   ```bash
   ./startup.sh
   ```

## Manual Commands

- Start services: `docker-compose up -d`
- View logs: `docker-compose logs -f`
- Stop services: `docker-compose down`
- Remove all data: `docker-compose down -v`

## API Endpoints

The API server provides MIP-003 compliant endpoints for each flow. For a flow with UID `flow123`, the endpoints are:

- `POST /{flow_uid}/start_job` - Start a new job
- `GET /{flow_uid}/status?job_id={job_id}` - Check job status
- `POST /{flow_uid}/provide_input` - Provide additional input (optional)
- `GET /{flow_uid}/availability` - Check service availability
- `GET /{flow_uid}/input_schema` - Get input schema

Example:
```bash
# Get input schema for flow "youtube-analyzer"
curl http://localhost:8000/youtube-analyzer/input_schema

# Start a job
curl -X POST http://localhost:8000/youtube-analyzer/start_job \
  -H "Content-Type: application/json" \
  -d '{
    "identifier_from_purchaser": "my-job-123",
    "input_data": {
      "queries": "Analyze this YouTube channel..."
    }
  }'
```

## Services Access

- **API Server**: http://localhost:8000
- **Admin Dashboard**: http://localhost:5000
- **PgAdmin**: http://localhost:8080
- **API Documentation**: http://localhost:8000/docs

## Testing

The test script runs automatically during startup. To run manually:
```bash
cd scripts
pip install -r requirements.txt
python test_authentication.py
python test_flow_sync.py
```