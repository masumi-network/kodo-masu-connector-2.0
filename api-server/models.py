from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Union
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    AWAITING_INPUT = "awaiting_input"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Amount(BaseModel):
    amount: int = Field(..., description="Payment amount (e.g., 1000000 for 1 ADA)")
    unit: str = Field(..., description="Currency unit (e.g., 'lovelace' for ADA)")

class StartJobRequest(BaseModel):
    identifier_from_purchaser: str = Field(..., description="Purchaser-defined identifier")
    input_data: Dict[str, Union[str, int, float, bool, None]] = Field(..., description="Input data for the job")

class StartJobResponse(BaseModel):
    status: str = Field(..., description="Status of job request")
    job_id: str = Field(..., description="Unique identifier for the started job")
    blockchainIdentifier: str = Field(..., description="Unique identifier for payment")
    submitResultTime: int = Field(..., description="Unix Time Code until which the result must be submitted")
    unlockTime: int = Field(..., description="Unix Time Code when the payment can be unlocked")
    externalDisputeUnlockTime: int = Field(..., description="Unix Time Code until when disputes can happen")
    payByTime: int = Field(..., description="Unix Time Code by which the payment must be made")
    agentIdentifier: str = Field(..., description="Agent Identifier")
    sellerVKey: str = Field(..., description="Wallet Public Key")
    identifierFromPurchaser: str = Field(..., description="Echoed identifier from purchaser")
    amounts: List[Amount] = Field(..., description="Payment amounts")
    input_hash: str = Field(..., description="Hash of the input data submitted")

class ValidationRule(BaseModel):
    validation: str = Field(..., description="Type of validation")
    value: str = Field(..., description="Validation value")

class InputField(BaseModel):
    id: str = Field(..., description="Unique identifier for the input field")
    type: str = Field(..., description="Type of the expected input")
    name: Optional[str] = Field(None, description="Displayed name for the input field")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional data for the field")
    validations: Optional[List[ValidationRule]] = Field(None, description="Validation rules")

class StatusResponse(BaseModel):
    job_id: str = Field(..., description="Job ID")
    status: JobStatus = Field(..., description="Current status of the job")
    message: Optional[str] = Field(None, description="Optional status message")
    input_data: Optional[List[InputField]] = Field(None, description="Required input data when status is awaiting_input")
    result: Optional[str] = Field(None, description="Job result if available")
    reasoning: Optional[str] = Field(None, description="AI reasoning or explanation for the result")

class ProvideInputRequest(BaseModel):
    job_id: str = Field(..., description="Job ID awaiting input")
    input_data: Dict[str, Union[str, int, float, bool, None]] = Field(..., description="Additional input data")

class ProvideInputResponse(BaseModel):
    status: str = Field(..., description="Status of the input provision")

class AvailabilityResponse(BaseModel):
    status: str = Field(..., description="Server status")
    type: str = Field(default="masumi-agent", description="Service type")
    message: Optional[str] = Field(None, description="Additional message")

class InputSchemaResponse(BaseModel):
    input_data: List[InputField] = Field(..., description="Expected input schema")

class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
    details: Optional[str] = Field(None, description="Additional error details")

class FlowInfo(BaseModel):
    uid: str = Field(..., description="Unique identifier for the flow")
    summary: str = Field(..., description="Short summary of the flow")
    description: Optional[str] = Field(None, description="Detailed description of the flow")
    author: Optional[str] = Field(None, description="Flow author")
    organization: Optional[str] = Field(None, description="Organization")
    tags: Optional[List[str]] = Field(None, description="Flow tags")
    url_identifier: Optional[str] = Field(None, description="URL-friendly identifier (e.g., YouTubeChannelAnalysis)")

class FlowListResponse(BaseModel):
    flows: List[FlowInfo] = Field(..., description="List of available flows")