#!/bin/bash

echo "Manually triggering flow sync..."

# Run flow sync in the container
docker exec -it kodosumi-flow-sync python /app/flow_sync.py

if [ $? -eq 0 ]; then
    echo "Flow sync completed successfully!"
else
    echo "Flow sync failed. Check the logs above."
    exit 1
fi