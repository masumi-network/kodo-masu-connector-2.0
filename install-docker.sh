#!/bin/bash

echo "Installing Docker and Docker Compose for Ubuntu..."

# Update package index
sudo apt update

# Install required packages
sudo apt install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

# Add Docker's official GPG key
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Set up the repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update package index with Docker packages
sudo apt update

# Install Docker Engine, CLI, and Compose plugin
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add current user to docker group (requires logout/login to take effect)
sudo usermod -aG docker $USER

# Start and enable Docker service
sudo systemctl enable docker
sudo systemctl start docker

# Verify installation
echo "Verifying Docker installation..."
docker --version
docker compose version

echo ""
echo "Docker and Docker Compose have been installed successfully!"
echo "NOTE: You may need to log out and back in for group changes to take effect."
echo "      Alternatively, run: newgrp docker"