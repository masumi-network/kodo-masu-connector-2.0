import os
from typing import Optional

class Config:
    """Configuration class for the API server."""
    
    # Database settings
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "kodosumi")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password")
    
    # Masumi Payment settings
    PAYMENT_SERVICE_URL: str = os.getenv("PAYMENT_SERVICE_URL", "https://api.masumi.network")
    PAYMENT_API_KEY: str = os.getenv("PAYMENT_API_KEY", "")
    AGENT_IDENTIFIER: str = os.getenv("AGENT_IDENTIFIER", "kodosumi-service")
    NETWORK: str = os.getenv("NETWORK", "Preprod")
    
    @property
    def database_url(self) -> str:
        """Get the database connection URL."""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

config = Config()