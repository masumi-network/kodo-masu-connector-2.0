from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import uvicorn
from datetime import datetime
from typing import Dict, Any

from database import db_manager
from payment_service import payment_service
from config import config
from models import (
    StartJobRequest, StartJobResponse, StatusResponse, 
    ProvideInputRequest, ProvideInputResponse,
    AvailabilityResponse, InputSchemaResponse, ErrorResponse,
    Amount, FlowListResponse, FlowInfo
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting API server...")
    await db_manager.initialize()
    yield
    # Shutdown
    logger.info("Shutting down API server...")
    await db_manager.close()

app = FastAPI(
    title="Kodosumi MIP-003 API",
    description="Agentic Service API Standard implementation for Kodosumi flows",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/flows", response_model=FlowListResponse)
async def list_flows():
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
async def start_job(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    request: StartJobRequest = ...
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
        
        # TODO: Add input validation against schema
        
        # Create payment request
        try:
            payment_response = await payment_service.create_payment_request(
                identifier_from_purchaser=request.identifier_from_purchaser,
                input_data=request.input_data
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
        from config import config
        agent_identifier = config.AGENT_IDENTIFIER
        
        # Extract wallet public key
        smart_contract_wallet = payment_data.get("SmartContractWallet", {})
        seller_vkey = smart_contract_wallet.get("walletVkey", "")
        
        # Extract amounts from RequestedFunds
        requested_funds = payment_data.get("RequestedFunds", [])
        amounts_list = []
        for fund in requested_funds:
            # Convert from lovelace to ada if unit contains lovelace/ADA info
            unit = fund.get("unit", "")
            amount = int(fund.get("amount", 0))
            
            # For display purposes, convert large lovelace amounts
            if "lovelace" in unit.lower() or len(str(amount)) > 6:
                amounts_list.append(Amount(amount=amount, unit="lovelace"))
            else:
                amounts_list.append(Amount(amount=amount, unit=unit))
        
        # Store job in database (use the actual UID from the flow)
        # Store the complete payment response for future reference
        created_job_id = await db_manager.create_job(
            flow_uid=flow["uid"],  # Always use the actual UID
            input_data=request.input_data,
            payment_data=payment_response,  # Store the full response, not just data
            identifier_from_purchaser=request.identifier_from_purchaser
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
            identifierFromPurchaser=request.identifier_from_purchaser,
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
async def check_job_status(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    job_id: str = Query(..., description="The ID of the job to check")
):
    """Check the status of a specific job."""
    try:
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Get job from database
        job = await db_manager.get_job_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Verify job belongs to the specified flow (use actual UID)
        if job["flow_uid"] != flow["uid"]:
            raise HTTPException(status_code=404, detail="Job not found for this flow")
        
        # Build status response
        response = StatusResponse(
            job_id=job_id,
            status=job["status"],
            message=job.get("message"),
            result=job.get("result"),
            reasoning=job.get("reasoning")
        )
        
        # Add input_data if status is awaiting_input
        if job["status"] == "awaiting_input":
            # TODO: Add logic to determine required input fields
            response.input_data = []
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in check_job_status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/{flow_identifier}/provide_input", response_model=ProvideInputResponse)
async def provide_input(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    request: ProvideInputRequest = ...
):
    """Provide additional input for a job awaiting input."""
    try:
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # Get job from database
        job = await db_manager.get_job_by_id(request.job_id)
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
            job_id=request.job_id,
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
async def check_availability(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)")
):
    """Check if the server and specific flow are available."""
    try:
        # Check if flow exists (by UID or name)
        flow = await db_manager.get_flow_by_uid_or_name(flow_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{flow_identifier}' not found")
        
        # TODO: Add additional availability checks (e.g., resource usage, queue length)
        
        return AvailabilityResponse(
            status="available",
            type="masumi-agent",
            message=f"Flow '{flow['summary']}' is ready to accept jobs"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in check_availability: {e}")
        return AvailabilityResponse(
            status="unavailable",
            type="masumi-agent",
            message="Service temporarily unavailable"
        )

@app.get("/{flow_identifier}/input_schema", response_model=InputSchemaResponse)
async def get_input_schema(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)")
):
    """Get the input schema for the specified flow."""
    try:
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
        
        return InputSchemaResponse(input_data=mip003_schema)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_input_schema: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)