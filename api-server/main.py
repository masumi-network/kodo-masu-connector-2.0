from fastapi import FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import uvicorn
from datetime import datetime
from typing import Dict, Any
import asyncio
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from database import db_manager
from payment_service import payment_service
from config import config
from models import (
    StartJobRequest, StartJobResponse, StatusResponse, 
    ProvideInputRequest, ProvideInputResponse,
    AvailabilityResponse, InputSchemaResponse, ErrorResponse,
    Amount, FlowListResponse, FlowInfo
)
from middleware import (
    RobustnessMiddleware, HealthCheckMiddleware, 
    setup_rate_limiting, metrics_endpoint, limiter
)
from cache import flow_cache, response_cache, schema_cache, not_found_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting API server...")
    await db_manager.initialize()
    
    # Start background task for pool monitoring
    async def monitor_pool():
        while True:
            try:
                stats = await db_manager.get_pool_stats()
                logger.info(f"Database pool stats: {stats}")
                await asyncio.sleep(60)  # Log every minute
            except Exception as e:
                logger.error(f"Error monitoring pool: {e}")
                await asyncio.sleep(60)
    
    monitor_task = asyncio.create_task(monitor_pool())
    
    yield
    
    # Shutdown
    logger.info("Shutting down API server...")
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    await db_manager.close()
    await flow_cache.clear()

app = FastAPI(
    title="Kodosumi MIP-003 API",
    description="Agentic Service API Standard implementation for Kodosumi flows",
    version="1.0.0",
    lifespan=lifespan
)

# Setup rate limiting
rate_limits = setup_rate_limiting(app)

# Add middlewares
app.add_middleware(HealthCheckMiddleware)  # Process health checks first
app.add_middleware(RobustnessMiddleware, request_timeout=30.0)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add metrics endpoint
app.add_api_route("/metrics", metrics_endpoint, methods=["GET"])

@app.get("/flows", response_model=FlowListResponse)
@limiter.limit(rate_limits.get("/flows", "30/minute"))
async def list_flows(request: Request):
    """
    List all available flows with their identifiers and descriptions.
    
    This endpoint helps users discover available flows and their identifiers
    for use in the MIP-003 endpoints.
    """
    try:
        flows_data = await db_manager.get_all_flows()
        
        flows = [
            FlowInfo(
                uid=flow["uid"],
                summary=flow["summary"],
                description=flow.get("description"),
                author=flow.get("author"),
                organization=flow.get("organization"),
                tags=flow.get("tags", []),
                url_identifier=flow.get("url_identifier")
            )
            for flow in flows_data
        ]
        
        return FlowListResponse(flows=flows)
        
    except Exception as e:
        logger.error(f"Unexpected error in list_flows: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/{flow_identifier}/start_job", response_model=StartJobResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/start_job", "10/minute"))
async def start_job(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    job_request: StartJobRequest = ...,
    request: Request = None
):
    """
    Start a new job for the specified flow.
    
    You can use either the flow UID or flow name as the identifier.
    Creates a payment request and stores job information in the database.
    """
    try:
        # Get flow information from database (by UID or name)
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Validate input data against MIP003 schema
        mip003_schema = flow.get('mip003_schema', [])
        if not mip003_schema:
            raise HTTPException(
                status_code=400, 
                detail="Flow does not have a valid MIP003 schema"
            )
        
        # Parse JSON string if needed
        if isinstance(mip003_schema, str):
            import json
            mip003_schema = json.loads(mip003_schema)
        
        # Validate input data against schema
        from validators import validate_input_data, ValidationError
        try:
            validate_input_data(job_request.input_data, mip003_schema)
        except ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid input data: {e.message}"
            )
        
        # Get agent identifier from flow
        flow_agent_identifier = flow.get('agent_identifier')
        
        # Create payment request
        try:
            payment_response = await payment_service.create_payment_request(
                identifier_from_purchaser=job_request.identifier_from_purchaser,
                input_data=job_request.input_data,
                agent_identifier=flow_agent_identifier
            )
        except Exception as e:
            logger.error(f"Payment request failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to create payment request")
        
        # Extract the actual payment data from the nested response structure
        payment_data = payment_response.get("data", {}) if isinstance(payment_response, dict) else {}
        
        # Parse timestamp values (keep as milliseconds)
        def parse_timestamp(timestamp_str: str) -> int:
            try:
                # Keep as milliseconds (original format)
                return int(timestamp_str)
            except:
                return 0
        
        # Extract time values from the correct nested fields
        submit_result_time = parse_timestamp(payment_data.get("submitResultTime", "0"))
        unlock_time = parse_timestamp(payment_data.get("unlockTime", "0"))
        external_dispute_unlock_time = parse_timestamp(payment_data.get("externalDisputeUnlockTime", "0"))
        pay_by_time = parse_timestamp(payment_data.get("payByTime", "0"))
        
        # Use the same agent identifier that was used to create the payment
        # This ensures consistency between payment creation and API response
        agent_identifier = flow_agent_identifier or config.AGENT_IDENTIFIER
        
        # Extract wallet public key
        smart_contract_wallet = payment_data.get("SmartContractWallet", {})
        seller_vkey = smart_contract_wallet.get("walletVkey", "")
        
        # Extract amounts from RequestedFunds
        requested_funds = payment_data.get("RequestedFunds", [])
        amounts_list = []
        
        for fund in requested_funds:
            amount = int(fund.get("amount", 0))
            
            # Use different units based on network
            if config.NETWORK.lower() == "preprod":
                # Preprod uses empty string for lovelace/ADA
                unit = ""
            else:
                # Mainnet uses USDM token identifier
                unit = "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad0014df105553444d"
            
            logger.info(f"Network: {config.NETWORK}, Unit: '{unit}'")
            amounts_list.append(Amount(amount=amount, unit=unit))
        
        # Store job in database (use the actual UID from the flow)
        # Store the complete payment response for future reference
        created_job_id = await db_manager.create_job(
            flow_uid=flow["uid"],  # Always use the actual UID
            input_data=job_request.input_data,
            payment_data=payment_response,  # Store the full response, not just data
            identifier_from_purchaser=job_request.identifier_from_purchaser
        )
        
        # Debug logging
        logger.info(f"Created job_id: {created_job_id}, type: {type(created_job_id)}")
        
        # Update job with blockchain identifier
        blockchain_identifier = payment_data.get("blockchainIdentifier", "")
        await db_manager.update_job_status(
            job_id=created_job_id,
            status="awaiting_payment",
            message=f"Payment requested. Blockchain ID: {blockchain_identifier}" if blockchain_identifier else "Payment requested"
        )
        
        # Build response - ensure job_id is a string
        job_id_string = str(created_job_id)
        
        # Log the amounts before creating response
        logger.info(f"Amounts list: {[{'amount': a.amount, 'unit': a.unit} for a in amounts_list]}")
        
        response = StartJobResponse(
            status="success",
            job_id=job_id_string,
            blockchainIdentifier=blockchain_identifier or "",
            submitResultTime=submit_result_time,
            unlockTime=unlock_time,
            externalDisputeUnlockTime=external_dispute_unlock_time,
            payByTime=pay_by_time,
            agentIdentifier=agent_identifier,
            sellerVKey=seller_vkey,
            identifierFromPurchaser=job_request.identifier_from_purchaser,
            amounts=amounts_list,
            input_hash=payment_data.get("inputHash", "")
        )
        
        logger.info(f"Job {created_job_id} started successfully for flow {flow_identifier} (UID: {flow['uid']})")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in start_job: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/{flow_identifier}/status", response_model=StatusResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/status", "60/minute"))
async def check_job_status(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    job_id: str = Query(..., description="The ID of the job to check"),
    request: Request = None
):
    """Check the status of a specific job with caching."""
    try:
        # Check regular cache first for successful responses
        cache_key = f"{flow_identifier}:{job_id}"
        cached_response = await response_cache.get("status", {"key": cache_key})
        if cached_response:
            return cached_response
        
        # Check not-found cache for 404 responses
        not_found_response = await not_found_cache.get("not_found", {"key": cache_key})
        if not_found_response:
            logger.debug(f"Returning cached 404 for job {job_id}")
            raise HTTPException(status_code=404, detail=not_found_response.get('message', 'Not found'))
        
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            # Cache the flow not found error
            await not_found_cache.set("not_found", {"key": cache_key}, {
                'message': f"Flow '{flow_identifier}' not found"
            })
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Get job from database
        job = await db_manager.get_job_by_id(job_id)
        if not job:
            # Cache the job not found error
            await not_found_cache.set("not_found", {"key": cache_key}, {
                'message': "Job not found"
            })
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Verify job belongs to the specified flow (use actual UID)
        if job["flow_uid"] != flow["uid"]:
            # Cache this specific error too
            await not_found_cache.set("not_found", {"key": cache_key}, {
                'message': "Job not found for this flow"
            })
            raise HTTPException(status_code=404, detail="Job not found for this flow")
        
        # Build status response
        # Check if this is a job that failed to start in Kodosumi
        # Jobs with status "error" and waiting_for_start_in_kodosumi=true failed to start
        if (job["status"] == "error" and 
            job.get("waiting_for_start_in_kodosumi", False) and 
            job.get("kodosumi_start_attempts", 0) >= 5):
            # This job failed to start in Kodosumi after max attempts
            response = StatusResponse(
                job_id=job_id,
                status="failed",  # Return "failed" per MIP003 spec
                message="Failed to start agent job",
                result=None,
                reasoning=None
            )
        else:
            # Map database status to API status
            db_status = job["status"]
            # Convert "error" status to "failed" for API response
            if db_status == "error":
                api_status = "failed"
            else:
                api_status = db_status
            
            # Debug logging
            logger.info(f"Status mapping: db_status={db_status}, api_status={api_status}")
            
            # Only include result if job is completed
            result = None
            if db_status == "completed":
                result = job.get("result")
                if result and isinstance(result, dict):
                    # Check if this is a kodosumi result with final_result structure
                    if "final_result" in result and isinstance(result["final_result"], dict):
                        # Extract the body content from nested structure
                        final_result = result["final_result"]
                        if "Markdown" in final_result and isinstance(final_result["Markdown"], dict):
                            # Return just the body content
                            result = final_result["Markdown"].get("body", "")
                        else:
                            # If not in expected format, return the whole final_result as JSON
                            import json
                            result = json.dumps(final_result)
                    else:
                        # For other dict results, convert to JSON string
                        import json
                        result = json.dumps(result)
            
            try:
                response = StatusResponse(
                    job_id=job_id,
                    status=api_status,  # This should work with string value
                    message=job.get("message"),
                    result=result,  # Will be None unless job is completed
                    reasoning=job.get("reasoning") if db_status == "completed" else None
                )
            except Exception as e:
                logger.error(f"Error creating StatusResponse: {e}")
                logger.error(f"api_status type: {type(api_status)}, value: {api_status}")
                logger.error(f"db_status: {db_status}")
                raise
        
        # Add input_data if status is awaiting_input
        if job["status"] == "awaiting_input":
            # TODO: Add logic to determine required input fields
            response.input_data = []
        
        # Cache response if job is completed or failed (won't change anymore)
        if response.status in ["completed", "failed"]:
            await response_cache.set("status", {"key": cache_key}, response)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in check_job_status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/{flow_identifier}/provide_input", response_model=ProvideInputResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/provide_input", "20/minute"))
async def provide_input(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    input_request: ProvideInputRequest = ...,
    request: Request = None
):
    """Provide additional input for a job awaiting input."""
    try:
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Get job from database
        job = await db_manager.get_job_by_id(input_request.job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Verify job belongs to the specified flow (use actual UID)
        if job["flow_uid"] != flow["uid"]:
            raise HTTPException(status_code=404, detail="Job not found for this flow")
        
        # Verify job is in awaiting_input status
        if job["status"] != "awaiting_input":
            raise HTTPException(
                status_code=400, 
                detail=f"Job is not awaiting input (current status: {job['status']})"
            )
        
        # TODO: Process the additional input and update job
        # For now, just update status to running
        await db_manager.update_job_status(
            job_id=input_request.job_id,
            status="running",
            message="Additional input provided, processing job"
        )
        
        return ProvideInputResponse(status="success")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in provide_input: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/{flow_identifier}/availability", response_model=AvailabilityResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/availability", "1000/minute"))  # Increased limit
async def check_availability(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    request: Request = None
):
    """Check if the server and specific flow are available - ALWAYS returns available."""
    # Ultra-lightweight implementation - no database calls, no caching
    # Just return available immediately for maximum performance
    return AvailabilityResponse(
        status="available",
        type="masumi-agent",
        message=f"Flow '{flow_identifier}' is ready to accept jobs"
    )

@app.get("/{flow_identifier}/input_schema", response_model=InputSchemaResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/input_schema", "30/minute"))
async def get_input_schema(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    request: Request = None
):
    """Get the input schema for the specified flow with caching."""
    try:
        # Check cache first (use schema_cache with longer TTL)
        cached_response = await schema_cache.get("input_schema", {"flow_identifier": flow_identifier})
        if cached_response:
            # Create a deep copy to avoid modifying cached data
            import copy
            response_copy = copy.deepcopy(cached_response)
            # Ensure validations is always an array
            if 'input_data' in response_copy:
                for field in response_copy['input_data']:
                    if 'validations' in field and field['validations'] is None:
                        field['validations'] = []
                    elif 'validations' not in field:
                        field['validations'] = []
            # Use Response with manual JSON to ensure arrays stay as arrays
            import json
            json_str = json.dumps(response_copy)
            return Response(content=json_str, media_type="application/json")
        
        # Get flow from database (by UID or name)
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Get MIP003 schema
        mip003_schema = flow.get('mip003_schema', [])
        if not mip003_schema:
            raise HTTPException(
                status_code=500, 
                detail="Flow does not have a valid MIP003 schema"
            )
        
        # Parse JSON string if needed
        if isinstance(mip003_schema, str):
            import json
            mip003_schema = json.loads(mip003_schema)
        
        # Clean up the schema to ensure validations is always an array
        cleaned_schema = []
        for field in mip003_schema:
            cleaned_field = dict(field)  # Create a copy
            # Convert None validations to empty array
            if 'validations' in cleaned_field and cleaned_field['validations'] is None:
                cleaned_field['validations'] = []
            # Ensure validations field exists as empty array if not present
            elif 'validations' not in cleaned_field:
                cleaned_field['validations'] = []
            cleaned_schema.append(cleaned_field)
        
        response_data = {"input_data": cleaned_schema}
        
        # Cache the response for 10 minutes (schemas rarely change)
        await schema_cache.set("input_schema", {"flow_identifier": flow_identifier}, response_data)
        
        # Use Response with manual JSON to ensure arrays stay as arrays
        import json
        json_str = json.dumps(response_data)
        # Debug: log what we're returning
        logger.info(f"DEBUG: Returning JSON: {json_str[:200]}")
        return Response(content=json_str, media_type="application/json")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_input_schema: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/availability")
async def global_availability():
    """Global availability check - no rate limiting, ultra-fast."""
    return {"status": "available", "type": "masumi-agent"}

@app.get("/health")
@limiter.limit("100/minute")
async def health_check(request: Request):
    """Health check endpoint - optimized for high concurrency."""
    # This is handled by HealthCheckMiddleware for better performance
    # But we keep this as a fallback
    return {
        "status": "healthy", 
        "timestamp": datetime.utcnow().isoformat(),
        "circuit_breaker": payment_service.get_circuit_breaker_status()
    }

@app.get("/health/detailed")
@limiter.limit("10/minute")
async def health_check_detailed(request: Request):
    """Detailed health check with component status."""
    try:
        # Check database
        db_stats = await db_manager.get_pool_stats()
        
        # Check cache
        cache_stats = flow_cache.get_stats()
        
        # Check circuit breaker
        circuit_status = payment_service.get_circuit_breaker_status()
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "components": {
                "database": {
                    "status": "healthy" if db_stats.get("idle", 0) > 0 else "degraded",
                    "pool_stats": db_stats
                },
                "cache": {
                    "status": "healthy",
                    "stats": cache_stats
                },
                "payment_service": {
                    "status": "healthy" if circuit_status["state"] != "open" else "degraded",
                    "circuit_breaker": circuit_status
                }
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e)
        }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)