# Deployment Guide

## Prerequisites

- Ubuntu 20.04+ or similar Linux distribution
- Root or sudo access
- Git installed

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/masumi-network/kodo-masu-connector-2.0.git
cd kodo-masu-connector-2.0
```

### 2. Install Docker and Docker Compose

If Docker and Docker Compose are not installed, run the installation script:

```bash
./install-docker.sh
```

After installation, either:
- Log out and log back in, OR
- Run `newgrp docker` to apply group changes

### 3. Configure Environment Variables

Copy the example environment file and edit it with your settings:

```bash
cp .env.example .env
nano .env  # or use your preferred editor
```

Update the following variables:
- `KODOSUMI_SERVER_URL`: Your Kodosumi server URL
- `KODOSUMI_USERNAME`: Your Kodosumi username
- `KODOSUMI_PASSWORD`: Your Kodosumi password
- `PAYMENT_SERVICE_URL`: Payment service endpoint
- `PAYMENT_API_KEY`: Payment service API key
- `AGENT_IDENTIFIER`: Your agent identifier
- `POSTGRES_PASSWORD`: Choose a secure database password

### 4. Start the Services

Run the startup script:

```bash
./startup.sh
```

This will:
- Build all Docker containers
- Start all services
- Initialize the database
- Run initial authentication and flow sync

### 5. Verify Installation

Check that all services are running:

```bash
docker compose ps
```

All services should show as "Up" or "healthy".

### 6. Access the Services

- **API Server**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Admin Dashboard**: http://localhost:5000
- **pgAdmin**: http://localhost:8080

## Service Management

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api-server
```

### Stop Services

```bash
docker compose down
```

### Restart Services

```bash
docker compose restart
```

### Update Services

```bash
git pull
docker compose build
docker compose up -d
```

## Troubleshooting

### Docker Compose Not Found

If you get "docker: 'compose' is not a docker command", you need to install Docker Compose v2:

```bash
./install-docker.sh
```

### Permission Denied

If you get permission errors, make sure your user is in the docker group:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Port Already in Use

If ports are already in use, you can change them in the `.env` file or stop conflicting services.

### Database Connection Issues

Ensure the PostgreSQL container is healthy:

```bash
docker compose ps postgres
```

If issues persist, check the database logs:

```bash
docker compose logs postgres
```

## Security Considerations

1. **Change default passwords** in the `.env` file
2. **Use HTTPS** in production by setting up a reverse proxy (nginx, traefik)
3. **Restrict port access** using firewall rules
4. **Regular backups** of the PostgreSQL database
5. **Keep Docker and dependencies updated**

## Backup and Restore

### Backup Database

```bash
docker compose exec postgres pg_dump -U kodosumi_user kodosumi_db > backup.sql
```

### Restore Database

```bash
docker compose exec -T postgres psql -U kodosumi_user kodosumi_db < backup.sql
```