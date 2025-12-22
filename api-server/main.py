from fastapi import FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import uvicorn
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
import uuid
from enum import Enum
from urllib.parse import unquote
import os
import asyncio
import httpx
import json
import re
from html import unescape
import hashlib
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from masumi import create_masumi_input_hash

from database import db_manager
from payment_service import payment_service
from config import config
from models import (
    StartJobRequest, StartJobResponse, StatusResponse, 
    ProvideInputRequest, ProvideInputResponse,
    AvailabilityResponse, InputSchemaResponse, ErrorResponse,
    InputField,
    Amount, FlowListResponse, FlowInfo
)
from middleware import (
    RobustnessMiddleware,
    HealthCheckMiddleware,
    setup_rate_limiting, metrics_endpoint, limiter
)
from cache import flow_cache, response_cache, schema_cache, not_found_cache
from shared.mip003_converter import convert_kodosumi_to_mip003
from shared.status_messages import (
    PAYMENT_PENDING_MESSAGE,
    WORKING_STATUS_MESSAGE,
    AWAITING_INPUT_MESSAGE,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWAITING_PAYMENT_MESSAGE = "The Agent is now waiting for its payment and will begin working soon."
RUNNING_MESSAGE = "The agent is now working on your task. Please check back soon."

def _load_max_start_attempts() -> int:
    value = os.getenv('KODOSUMI_START_MAX_ATTEMPTS', '10')
    try:
        attempts = int(value)
    except ValueError:
        logger.warning("Invalid KODOSUMI_START_MAX_ATTEMPTS value '%s', defaulting to 10", value)
        attempts = 10
    return max(1, attempts)

MAX_START_ATTEMPTS = _load_max_start_attempts()

HTML_TAG_RE = re.compile(r"<[^>]+>")


class FlowVariant(str, Enum):
    DEFAULT = "default"
    PREMIUM = "premium"
    FREE = "free"


def parse_flow_identifier(identifier: str) -> Tuple[str, FlowVariant]:
    """Split flow identifier into base identifier and variant."""
    if not identifier:
        return identifier, FlowVariant.DEFAULT

    # Decode URL-encoded sequences (handle double-encoding by decoding twice if needed).
    decoded = unquote(identifier)
    if "%" in decoded:
        decoded = unquote(decoded)

    normalized = decoded.strip()
    if normalized.lower().endswith("+premium"):
        base = normalized[:-8]
        return base if base else normalized, FlowVariant.PREMIUM
    if normalized.lower().endswith("+free"):
        base = normalized[:-5]
        return base if base else normalized, FlowVariant.FREE
    return normalized, FlowVariant.DEFAULT


def resolve_agent_identifier(flow: Dict[str, Any], variant: FlowVariant) -> Tuple[Optional[str], bool]:
    """Return the agent identifier for the requested variant and whether payment is required."""
    default_agent = flow.get('agent_identifier_default') or flow.get('agent_identifier')

    if variant == FlowVariant.DEFAULT:
        return (default_agent.strip() if isinstance(default_agent, str) else None, True)

    if variant == FlowVariant.PREMIUM:
        premium_agent = flow.get('premium_agent_identifier')
        return (premium_agent.strip() if isinstance(premium_agent, str) else None, True)

    # Free variant
    free_enabled = bool(flow.get('free_mode_enabled'))
    free_agent = flow.get('free_agent_identifier')
    if not free_enabled:
        return None, False
    if isinstance(free_agent, str) and free_agent.strip():
        return free_agent.strip(), False
    # Fallback to default agent when free mode is enabled but no dedicated identifier is set
    return (default_agent.strip() if isinstance(default_agent, str) else None, False)


def _clean_schema_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = HTML_TAG_RE.sub("", value)
    cleaned = cleaned.replace("\r", "").strip()
    if not cleaned:
        return None
    return unescape(cleaned)


def _extract_pending_input_context(pending_request: Optional[Dict[str, Any]]) -> Optional[str]:
    if not pending_request:
        return None

    schema_raw = pending_request.get("schema_raw")
    if isinstance(schema_raw, str):
        try:
            schema_raw = json.loads(schema_raw)
        except (ValueError, json.JSONDecodeError):
            schema_raw = None

    segments: List[str] = []
    if isinstance(schema_raw, list):
        for element in schema_raw:
            if not isinstance(element, dict):
                continue
            text_value = element.get("text")
            cleaned_text = _clean_schema_text(text_value)
            if cleaned_text:
                segments.append(cleaned_text)
                continue
            if element.get("type") in {"textarea", "input"}:
                parts = []
                label = element.get("label") or element.get("name")
                if isinstance(label, str) and label.strip():
                    parts.append(label.strip())
                default_value = element.get("value") or element.get("placeholder")
                cleaned_default = _clean_schema_text(default_value)
                if cleaned_default:
                    parts.append(cleaned_default)
                if parts:
                    segments.append(": ".join(parts))
        if segments:
            return "\n\n".join(segments)

    lock_identifier = pending_request.get("lock_identifier")
    if isinstance(lock_identifier, str) and lock_identifier:
        return f"{AWAITING_INPUT_MESSAGE} (lock {lock_identifier})"

    return None


def _extract_final_output_text(payload: Any) -> Optional[str]:
    """Return a human-readable string from nested Kodosumi output structures."""
    if payload in (None, ""):
        return None

    data = payload

    if isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return text

    if isinstance(data, list):
        for item in data:
            extracted = _extract_final_output_text(item)
            if extracted:
                return extracted
        return None

    if not isinstance(data, dict):
        return None

    markdown = data.get("Markdown")
    if isinstance(markdown, dict):
        body = markdown.get("body")
        if isinstance(body, str) and body.strip():
            return body.strip()

    for key in ("body", "text", "message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("final_result", "final", "result", "output"):
        nested = data.get(key)
        extracted = _extract_final_output_text(nested)
        if extracted:
            return extracted

    return None


def _build_user_friendly_message(job: Dict[str, Any], pending_request: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Convert internal job metadata into a user-facing message."""
    status = job.get("status")
    base_message = job.get("message")

    if status == "awaiting_payment":
        return PAYMENT_PENDING_MESSAGE

    if status in {"running", "working"}:
        return WORKING_STATUS_MESSAGE

    if status == "awaiting_input":
        detailed_prompt = _extract_pending_input_context(pending_request)
        return detailed_prompt or AWAITING_INPUT_MESSAGE

    if status == "error":
        final_text = _extract_final_output_text(job.get("result"))
        if final_text and base_message:
            return f"{final_text}\n\nError: {base_message}"
        return final_text or base_message

    return base_message

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
        base_identifier, variant = parse_flow_identifier(flow_identifier)

        # Get flow information from database (by UID or name)
        flow = await db_manager.get_flow_by_uid_or_name(base_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{base_identifier}' not found")

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

        # Determine agent identifier and payment requirement based on variant
        agent_identifier, payment_required = resolve_agent_identifier(flow, variant)

        if not agent_identifier:
            variant_name = variant.value
            raise HTTPException(
                status_code=404,
                detail=f"Flow '{base_identifier}' does not have an agent configured for variant '{variant_name}'"
            )

        amounts_list = []
        payment_response: Dict[str, Any] = {}
        payment_data: Dict[str, Any] = {}
        submit_result_time = unlock_time = external_dispute_unlock_time = pay_by_time = 0
        seller_vkey = ""
        blockchain_identifier = ""
        input_hash_value: Optional[str] = None

        if payment_required:
            # Create payment request via Masumi
            try:
                payment_response = await payment_service.create_payment_request(
                    identifier_from_purchaser=job_request.identifier_from_purchaser,
                    input_data=job_request.input_data,
                    agent_identifier=agent_identifier
                )
            except Exception as e:
                logger.error(f"Payment request failed: {e}")
                raise HTTPException(status_code=500, detail="Failed to create payment request")

            payment_data = payment_response.get("data", {}) if isinstance(payment_response, dict) else {}

            def parse_timestamp(timestamp_str: str) -> int:
                try:
                    return int(timestamp_str)
                except Exception:
                    return 0

            submit_result_time = parse_timestamp(payment_data.get("submitResultTime", "0"))
            unlock_time = parse_timestamp(payment_data.get("unlockTime", "0"))
            external_dispute_unlock_time = parse_timestamp(payment_data.get("externalDisputeUnlockTime", "0"))
            pay_by_time = parse_timestamp(payment_data.get("payByTime", "0"))
            blockchain_identifier = payment_data.get("blockchainIdentifier", "")

            smart_contract_wallet = payment_data.get("SmartContractWallet", {})
            seller_vkey = smart_contract_wallet.get("walletVkey", "")

            requested_funds = payment_data.get("RequestedFunds", [])
            for fund in requested_funds:
                amount = int(fund.get("amount", 0))

                if config.NETWORK.lower() == "preprod":
                    unit = ""
                else:
                    unit = "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad0014df105553444d"

                logger.info(f"Network: {config.NETWORK}, Unit: '{unit}'")
                amounts_list.append(Amount(amount=amount, unit=unit))

            input_hash_value = payment_data.get("inputHash")
        else:
            logger.info(f"Creating free-mode job for flow {base_identifier}")

        # Store job in database
        created_job_id = await db_manager.create_job(
            flow_uid=flow["uid"],
            input_data=job_request.input_data,
            payment_data=payment_response,
            identifier_from_purchaser=job_request.identifier_from_purchaser,
            status="awaiting_payment" if payment_required else "running",
            input_hash=input_hash_value,
            agent_identifier_used=agent_identifier,
            payment_required=payment_required,
            waiting_for_start=not payment_required  # free-mode jobs can start immediately
        )

        # Update job status with message
        if payment_required:
            await db_manager.update_job_status(
                job_id=created_job_id,
                status="awaiting_payment",
                message=AWAITING_PAYMENT_MESSAGE,
                waiting_for_start=False
            )
        else:
            await db_manager.update_job_status(
                job_id=created_job_id,
                status="running",
                message=RUNNING_MESSAGE,
                waiting_for_start=True
            )

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
            identifierFromPurchaser=job_request.identifier_from_purchaser,
            amounts=amounts_list,
            input_hash=input_hash_value or ""
        )

        logger.info(
            f"Job {created_job_id} started successfully for flow {base_identifier}"
            f" (UID: {flow['uid']}), variant={variant.value}, payment_required={payment_required}"
        )
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
        base_identifier, _ = parse_flow_identifier(flow_identifier)
        # Check regular cache first for successful responses
        cache_key = f"{base_identifier}:{job_id}"
        cached_response = await response_cache.get("status", {"key": cache_key})
        if cached_response:
            return JSONResponse(content=cached_response)

        # Check not-found cache for 404 responses
        not_found_response = await not_found_cache.get("not_found", {"key": cache_key})
        if not_found_response:
            logger.debug(f"Returning cached 404 for job {job_id}")
            raise HTTPException(status_code=404, detail=not_found_response.get('message', 'Not found'))
        
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(base_identifier)
        if not flow:
            # Cache the flow not found error
            await not_found_cache.set("not_found", {"key": cache_key}, {
                'message': f"Flow '{base_identifier}' not found"
            })
            raise HTTPException(status_code=404, detail=f"Flow '{base_identifier}' not found")
        
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
        
        def build_status_id(job_record: Dict[str, Any]) -> str:
            """Create a deterministic status identifier for each status change."""
            job_id_value = job_record.get("job_id")
            try:
                job_uuid = uuid.UUID(str(job_id_value))
            except (ValueError, TypeError):
                job_uuid = None

            updated_at = job_record.get("updated_at")
            if isinstance(updated_at, datetime):
                updated_marker = updated_at.isoformat()
            else:
                updated_marker = str(updated_at) if updated_at is not None else ""

            status_marker = job_record.get("status", "")
            namespace = job_uuid or uuid.NAMESPACE_URL
            seed = f"{job_id_value}:{status_marker}:{updated_marker}"
            return str(uuid.uuid5(namespace, seed))

        def build_input_schema_from_result(job_record: Dict[str, Any]) -> Optional[InputSchemaResponse]:
            """Extract lock-driven input fields when job awaits human input."""
            if job_record.get("status") != "awaiting_input":
                return None

            result_payload = job_record.get("result") or {}
            if not isinstance(result_payload, dict):
                return None

            lock_section = result_payload.get("kodosumi_locks")
            if not isinstance(lock_section, dict):
                return None

            locks = lock_section.get("locks")
            if not isinstance(locks, list):
                return None

            type_map = {
                "text": "string",
                "textarea": "textarea",
                "boolean": "boolean",
                "checkbox": "boolean",
                "number": "number",
            }

            collected_fields: List[InputField] = []
            for lock in locks:
                lock_id = lock.get("lock_id")
                if not lock_id:
                    logger.warning("Lock entry missing lock_id; skipping")
                    continue

                schema = lock.get("schema")
                if not isinstance(schema, list):
                    continue

                for element in schema:
                    if not isinstance(element, dict):
                        continue
                    element_type = element.get("type")
                    if not element_type:
                        continue

                    normalized_type = element_type.lower()
                    if normalized_type in {"submit", "cancel", "markdown"}:
                        continue

                    field_id = element.get("name") or element.get("id")
                    if not field_id:
                        continue

                    field_type = type_map.get(normalized_type, normalized_type)
                    data: Dict[str, Any] = {}
                    if element.get("label"):
                        data["label"] = element["label"]
                    if element.get("placeholder"):
                        data["placeholder"] = element["placeholder"]
                    if element.get("value") is not None:
                        data["value"] = element["value"]
                    if element.get("description"):
                        data["description"] = element["description"]

                    data["lock_id"] = lock_id

                    validations: List[Dict[str, Any]] = []
                    raw_validations = element.get("validations")
                    if isinstance(raw_validations, list):
                        validations = raw_validations

                    collected_fields.append(
                        InputField(
                            id=str(field_id),
                            type=field_type,
                            name=element.get("label") or element.get("name"),
                            data=data or None,
                            validations=validations
                        )
                    )

            if not collected_fields:
                return None
            return InputSchemaResponse(input_data=collected_fields)
        
        job_id_str = str(job["job_id"])
        raw_status_identifier = job.get("current_status_id")
        status_identifier = str(raw_status_identifier) if raw_status_identifier else build_status_id(job)
        input_fields: Optional[List[InputField]] = None
        input_schema_response: Optional[InputSchemaResponse] = None
        pending_request: Optional[Dict[str, Any]] = None

        if job["status"] == "awaiting_input":
            pending_request = await db_manager.get_pending_input_request(job_id_str)
            if pending_request:
                schema = pending_request.get("schema_mip003")
                if not schema and pending_request.get("schema_raw"):
                    try:
                        schema = convert_kodosumi_to_mip003({"elements": pending_request["schema_raw"]})
                    except Exception as exc:
                        logger.error(f"Failed to convert lock schema for job {job_id_str}: {exc}")
                        schema = None
                if schema:
                    try:
                        input_fields = [InputField(**field) for field in schema]
                        input_schema_response = InputSchemaResponse(input_data=input_fields)
                        status_identifier = pending_request.get("status_id", status_identifier)
                    except Exception as exc:
                        logger.error(f"Unable to build input schema for job {job_id_str}: {exc}")

        if input_schema_response is None:
            fallback_schema = build_input_schema_from_result(job)
            if fallback_schema:
                input_schema_response = fallback_schema

        user_friendly_message = _build_user_friendly_message(job, pending_request)

        # Build status response
        # Check if this is a job that failed to start in Kodosumi
        # Jobs with status "error" and waiting_for_start_in_kodosumi=true failed to start
        if (
            job["status"] == "error"
            and job.get("waiting_for_start_in_kodosumi", False)
            and job.get("kodosumi_start_attempts", 0) >= MAX_START_ATTEMPTS
        ):
            failure_message = job.get("message") or "Failed to start agent job"
            # This job failed to start in Kodosumi after max attempts
            response = StatusResponse(
                status_id=status_identifier,
                job_id=job_id,
                status="failed",  # Return "failed" per MIP003 spec
                message=failure_message,
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

            display_message = user_friendly_message if user_friendly_message is not None else job.get("message")
            if api_status == "awaiting_payment":
                display_message = AWAITING_PAYMENT_MESSAGE
            elif api_status == "running":
                display_message = RUNNING_MESSAGE
            
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
            elif job.get("message") and api_status == "failed":
                # Surface failure details in the result field so clients can display them
                result = job.get("message")
            
            try:
                response = StatusResponse(
                    status_id=status_identifier,
                    job_id=job_id,
                    status=api_status,  # This should work with string value
                    message=display_message,
                    result=result,  # Will be None unless job is completed
                    reasoning=job.get("reasoning") if db_status == "completed" else None
                )
            except Exception as e:
                logger.error(f"Error creating StatusResponse: {e}")
                logger.error(f"api_status type: {type(api_status)}, value: {api_status}")
                logger.error(f"db_status: {db_status}")
                raise
        
        if input_schema_response:
            response.input_schema = input_schema_response

        response_payload = response.model_dump(by_alias=True, exclude_none=True)
        
        # Cache response if job is completed or failed (won't change anymore)
        if response.status in ["completed", "failed"]:
            await response_cache.set("status", {"key": cache_key}, response_payload)
        
        return JSONResponse(content=response_payload)
        
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
        base_identifier, variant = parse_flow_identifier(flow_identifier)
        # Get flow information to verify it exists and get actual UID
        flow = await db_manager.get_flow_by_uid_or_name(base_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{base_identifier}' not found")

        agent_identifier, _ = resolve_agent_identifier(flow, variant)
        if not agent_identifier:
            raise HTTPException(status_code=404, detail="Variant not available for this flow")

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

        if job.get("awaiting_input_status_id") != input_request.status_id:
            raise HTTPException(
                status_code=400,
                detail="Status identifier does not match the current pending input request"
            )

        pending_request = await db_manager.get_input_request_by_status_id(input_request.status_id)
        if not pending_request or pending_request.get("status") != "pending":
            raise HTTPException(
                status_code=404,
                detail="Pending input request not found or already resolved"
            )

        api_key, session_cookie = await db_manager.get_latest_kodosumi_credentials()
        if not api_key:
            raise HTTPException(status_code=503, detail="No Kodosumi API key available to submit input")

        kodosumi_fid = pending_request.get("kodosumi_fid")
        lock_identifier = pending_request.get("lock_identifier")
        if not kodosumi_fid or not lock_identifier:
            raise HTTPException(status_code=500, detail="Lock metadata missing for this job")

        lock_url = f"{config.KODOSUMI_SERVER_URL.rstrip('/')}/lock/{kodosumi_fid}/{lock_identifier}"

        try:
            cookies = {'kodosumi_jwt': session_cookie} if session_cookie else None

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    lock_url,
                    headers={
                        "KODOSUMI_API_KEY": api_key,
                        "Content-Type": "application/json",
                    },
                    json=input_request.input_data,
                    cookies=cookies,
                    timeout=30.0,
                )
        except httpx.HTTPError as exc:
            logger.error(f"Failed to submit lock data for job {job['job_id']}: {exc}")
            raise HTTPException(status_code=502, detail="Failed to reach Kodosumi lock endpoint")

        if response.status_code != 200:
            error_msg = response.text
            await db_manager.update_input_request_status(
                input_request.status_id,
                new_status="error",
                error_message=error_msg
            )
            raise HTTPException(status_code=response.status_code, detail="Kodosumi rejected the additional input")

        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {"raw": response.text}

        await db_manager.update_input_request_status(
            input_request.status_id,
            new_status="completed",
            response_data=response_payload
        )

        await db_manager.update_job_status(
            job_id=input_request.job_id,
            status="running",
            message=RUNNING_MESSAGE,
            clear_awaiting_input=True
        )

        input_hash_value = ""
        identifier_from_purchaser = job.get("identifier_from_purchaser")
        if identifier_from_purchaser:
            try:
                input_hash_value = create_masumi_input_hash(
                    input_request.input_data,
                    str(identifier_from_purchaser)
                )
            except Exception as exc:
                logger.error(
                    f"Failed to calculate input hash for job {job['job_id']}: {exc}"
                )
        else:
            logger.warning(
                f"No purchaser identifier found for job {job['job_id']}; "
                "input hash response will be empty"
            )

        serialized_input = json.dumps(input_request.input_data, sort_keys=True, separators=(",", ":"))
        secure_input_hash = f"sha256:{hashlib.sha256(serialized_input.encode('utf-8')).hexdigest()}"
        signature_seed = f"{secure_input_hash}:{agent_identifier}:{input_request.job_id}"
        signature = hashlib.sha256(signature_seed.encode('utf-8')).hexdigest()

        return ProvideInputResponse(
            status="success",
            input_hash=input_hash_value or secure_input_hash,
            signature=signature
        )
        
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
    base_identifier, variant = parse_flow_identifier(flow_identifier)
    flow = await db_manager.get_flow_by_uid_or_name(base_identifier)
    if not flow:
        raise HTTPException(status_code=404, detail=f"Flow '{base_identifier}' not found")

    agent_identifier, _ = resolve_agent_identifier(flow, variant)
    if not agent_identifier:
        raise HTTPException(status_code=404, detail="Variant not available for this flow")

    variant_suffix = "" if variant == FlowVariant.DEFAULT else f" ({variant.value})"

    return AvailabilityResponse(
        status="available",
        type="masumi-agent",
        message=f"Flow '{base_identifier}{variant_suffix}' is ready to accept jobs"
    )

@app.get("/{flow_identifier}/input_schema", response_model=InputSchemaResponse)
@limiter.limit(rate_limits.get("/{flow_identifier}/input_schema", "30/minute"))
async def get_input_schema(
    flow_identifier: str = Path(..., description="Flow identifier (UID or name)"),
    request: Request = None
):
    """Get the input schema for the specified flow with caching."""
    try:
        base_identifier, variant = parse_flow_identifier(flow_identifier)
        # Get flow from database (by UID or name)
        flow = await db_manager.get_flow_by_uid_or_name(base_identifier)
        if not flow:
            raise HTTPException(status_code=404, detail=f"Flow '{base_identifier}' not found")

        agent_identifier, _ = resolve_agent_identifier(flow, variant)
        if not agent_identifier:
            raise HTTPException(status_code=404, detail="Variant not available for this flow")

        # Check cache after validating the variant
        cached_response = await schema_cache.get("input_schema", {"flow_identifier": base_identifier})
        if cached_response:
            # Create a deep copy to avoid modifying cached data
            import copy
            response_copy = copy.deepcopy(cached_response)
            if 'input_data' in response_copy:
                for field in response_copy['input_data']:
                    if 'validations' in field and field['validations'] is None:
                        field['validations'] = []
                    elif 'validations' not in field:
                        field['validations'] = []
            import json
            json_str = json.dumps(response_copy)
            return Response(content=json_str, media_type="application/json")
        
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
        await schema_cache.set("input_schema", {"flow_identifier": base_identifier}, response_data)
        
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
    payload = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "circuit_breaker": payment_service.get_circuit_breaker_status()
    }
    return JSONResponse(payload, headers={"Connection": "close"})

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
        
        payload = {
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
        return JSONResponse(payload, headers={"Connection": "close"})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e)
        }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
