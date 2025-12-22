"""
Input validation module for MIP003 compliant schemas.

This module provides validation functions to ensure input data
matches the expected schema format and constraints.
"""

from typing import Dict, List, Any, Union, Optional
import mimetypes
import re
from urllib.parse import urlparse


class ValidationError(Exception):
    """Custom exception for validation errors."""
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"Validation error for field '{field}': {message}")


def validate_input_data(
    input_data: Dict[str, Union[str, int, float, bool, List[str], List[int], None]], 
    mip003_schema: List[Dict[str, Any]]
) -> None:
    """
    Validate input data against MIP003 schema.
    
    Args:
        input_data: The input data to validate
        mip003_schema: The MIP003 schema definition
        
    Raises:
        ValidationError: If validation fails
    """
    # Create a map of field definitions for easy lookup
    field_map = {field['id']: field for field in mip003_schema}
    
    # Check for required fields
    for field in mip003_schema:
        field_id = field['id']
        field_type = field['type']
        validations = field.get('validations', [])
        
        # Check if field is optional
        is_optional = any(
            v.get('validation') == 'optional' and v.get('value') == 'true' 
            for v in validations
        )
        
        # Check if field is present
        if field_id not in input_data:
            if not is_optional:
                raise ValidationError(field_id, f"Required field '{field_id}' is missing")
            continue
        
        # Get the value
        value = input_data[field_id]
        
        # Skip validation for None values in optional fields
        if value is None and is_optional:
            continue
        
        # Validate based on type
        if field_type == 'string' or field_type == 'textarea':
            validate_string_field(field_id, value, field, validations)
        elif field_type == 'number':
            validate_number_field(field_id, value, field, validations)
        elif field_type == 'boolean':
            validate_boolean_field(field_id, value, field)
        elif field_type == 'option':
            validate_option_field(field_id, value, field, validations)
        elif field_type == 'file':
            validate_file_field(field_id, value, field, validations)
    
    # Check for unexpected fields
    expected_fields = set(field_map.keys())
    provided_fields = set(input_data.keys())
    unexpected_fields = provided_fields - expected_fields
    
    if unexpected_fields:
        # Log warning but don't fail - be lenient with extra fields
        # In production, you might want to be stricter
        pass


def validate_string_field(
    field_id: str, 
    value: Any, 
    field: Dict[str, Any], 
    validations: List[Dict[str, Any]]
) -> None:
    """Validate a string field."""
    if not isinstance(value, str):
        raise ValidationError(field_id, f"Expected string, got {type(value).__name__}")
    
    for validation in validations:
        val_type = validation.get('validation')
        val_value = validation.get('value')
        
        if val_type == 'min' and len(value) < int(val_value):
            raise ValidationError(field_id, f"Length must be at least {val_value} characters")
        elif val_type == 'max' and len(value) > int(val_value):
            raise ValidationError(field_id, f"Length must not exceed {val_value} characters")
        elif val_type == 'format':
            validate_string_format(field_id, value, val_value)


def validate_string_format(field_id: str, value: str, format_type: str) -> None:
    """Validate string format constraints."""
    if format_type == 'email':
        # Simple email validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, value):
            raise ValidationError(field_id, "Invalid email format")
    elif format_type == 'url':
        # Simple URL validation
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        if not re.match(url_pattern, value):
            raise ValidationError(field_id, "Invalid URL format")
    elif format_type == 'nonempty':
        if not value.strip():
            raise ValidationError(field_id, "Field cannot be empty")


def validate_number_field(
    field_id: str, 
    value: Any, 
    field: Dict[str, Any], 
    validations: List[Dict[str, Any]]
) -> None:
    """Validate a number field."""
    if not isinstance(value, (int, float)):
        raise ValidationError(field_id, f"Expected number, got {type(value).__name__}")
    
    for validation in validations:
        val_type = validation.get('validation')
        val_value = validation.get('value')
        
        if val_type == 'min' and value < float(val_value):
            raise ValidationError(field_id, f"Value must be at least {val_value}")
        elif val_type == 'max' and value > float(val_value):
            raise ValidationError(field_id, f"Value must not exceed {val_value}")
        elif val_type == 'format' and val_value == 'integer':
            if not isinstance(value, int) and value != int(value):
                raise ValidationError(field_id, "Value must be an integer")


def validate_boolean_field(
    field_id: str, 
    value: Any, 
    field: Dict[str, Any]
) -> None:
    """Validate a boolean field."""
    if not isinstance(value, bool):
        raise ValidationError(field_id, f"Expected boolean, got {type(value).__name__}")


def validate_option_field(
    field_id: str, 
    value: Any, 
    field: Dict[str, Any], 
    validations: List[Dict[str, Any]]
) -> None:
    """Validate an option field (always expects array format with indices)."""
    # Get allowed values from field data
    allowed_values = field.get('data', {}).get('values', [])
    if not allowed_values:
        # No values defined, can't validate
        return
    
    # All option fields expect arrays (for consistency)
    if not isinstance(value, list):
        raise ValidationError(field_id, f"Expected array for option field, got {type(value).__name__}")
    
    # Validate each value in the array
    for item in value:
        if not isinstance(item, int):
            raise ValidationError(field_id, f"All options must be integers (indices), got {type(item).__name__}")
        # Validate that the index is within range
        if item < 0 or item >= len(allowed_values):
            raise ValidationError(
                field_id, 
                f"Invalid option index '{item}'. Valid indices are 0 to {len(allowed_values) - 1}"
            )
    
    # Check min/max constraints if specified
    min_val = None
    max_val = None
    
    for validation in validations:
        val_type = validation.get('validation')
        val_value = validation.get('value')
        
        if val_type == 'min':
            min_val = int(val_value)
        elif val_type == 'max':
            max_val = int(val_value)
    
    # Apply constraints
    if min_val is not None and len(value) < min_val:
        raise ValidationError(field_id, f"Must select at least {min_val} option(s)")
    if max_val is not None and len(value) > max_val:
        raise ValidationError(field_id, f"Must select at most {max_val} option(s)")


def validate_file_field(
    field_id: str,
    value: Any,
    field: Dict[str, Any],
    validations: List[Dict[str, Any]]
) -> None:
    """Validate a file field (expecting URL-based uploads)."""
    if value is None:
        raise ValidationError(field_id, "File value cannot be null")

    output_format = field.get('data', {}).get('outputFormat', 'url')
    if isinstance(output_format, str):
        output_format = output_format.lower()
    else:
        output_format = 'url'

    # Normalise to a list for consistent min/max checks
    raw_items = value if isinstance(value, list) else [value]

    if output_format == 'url':
        file_entries: List[Dict[str, Any]] = []

        def _normalise_url_entry(entry: Any) -> Dict[str, Any]:
            if isinstance(entry, str):
                trimmed_value = entry.strip()
                if not trimmed_value:
                    raise ValidationError(field_id, "File value cannot be empty")
                return {
                    "url": trimmed_value,
                    "name": None,
                    "size": None,
                    "type": None
                }
            if isinstance(entry, dict):
                url_value = str(entry.get("url") or entry.get("value") or "").strip()
                if not url_value:
                    raise ValidationError(field_id, "File URL is missing or empty")
                normalised = {
                    "url": url_value,
                    "name": entry.get("name"),
                    "size": entry.get("size"),
                    "type": entry.get("type") or entry.get("mimeType")
                }
                return normalised
            raise ValidationError(field_id, f"Unsupported file entry type: {type(entry).__name__}")

        for item in raw_items:
            file_entries.append(_normalise_url_entry(item))

    else:
        # output_format == "string" (or any other non-url format)
        file_entries = []
        for item in raw_items:
            if not isinstance(item, str):
                raise ValidationError(field_id, "File value must be a string")
            trimmed_value = item.strip()
            if not trimmed_value:
                raise ValidationError(field_id, "File value cannot be empty")
            file_entries.append({"value": trimmed_value})

    # Parse validation rules
    min_files: Optional[int] = None
    max_files: Optional[int] = None
    max_size: Optional[int] = None
    accept_rules: List[str] = []

    for validation in validations:
        val_type = validation.get("validation")
        val_value = validation.get("value")
        if val_type == "min":
            try:
                min_files = int(val_value)
            except (TypeError, ValueError):
                pass
        elif val_type == "max":
            try:
                max_files = int(val_value)
            except (TypeError, ValueError):
                pass
        elif val_type == "maxSize":
            try:
                max_size = int(val_value)
            except (TypeError, ValueError):
                pass
        elif val_type == "accept" and isinstance(val_value, str):
            accept_rules = [item.strip().lower() for item in val_value.split(",") if item.strip()]

    file_count = len(file_entries)
    if min_files is not None and file_count < min_files:
        raise ValidationError(field_id, f"At least {min_files} file(s) required")
    if max_files is not None and file_count > max_files:
        raise ValidationError(field_id, f"No more than {max_files} file(s) allowed")

    if output_format == 'url':
        # Validate individual entries for URL format
        for entry in file_entries:
            file_url = entry["url"]
            parsed = urlparse(file_url)
            if parsed.scheme not in ("http", "https"):
                raise ValidationError(field_id, "File URL must start with http:// or https://")

            if max_size is not None:
                size_value = entry.get("size")
                try:
                    if size_value is not None and int(size_value) > max_size:
                        raise ValidationError(field_id, f"File exceeds maximum size of {max_size} bytes")
                except (TypeError, ValueError):
                    # If size is not numeric, skip enforcement
                    pass

            if accept_rules:
                if not _file_matches_accept_rules(parsed, entry, accept_rules):
                    raise ValidationError(field_id, f"File type not permitted. Allowed: {', '.join(accept_rules)}")
    else:
        # For string formats, we can only enforce content presence; size/type cannot be reliably checked
        if max_size is not None:
            for entry in file_entries:
                try:
                    if len(entry["value"]) > max_size:
                        raise ValidationError(field_id, f"File content exceeds maximum size of {max_size} characters")
                except TypeError:
                    pass


def _file_matches_accept_rules(
    parsed_url,
    entry: Dict[str, Any],
    accept_rules: List[str]
) -> bool:
    """Check if file entry matches accept rules."""
    path = parsed_url.path or ""
    extension = ""
    if "." in path:
        extension = path[path.rfind("."):].lower()

    mime_type = entry.get("type")
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(path)
    if mime_type:
        mime_type = mime_type.lower()

    for rule in accept_rules:
        if rule in ("*/*", "*"):
            return True
        if rule.endswith("/*"):
            base = rule[:-2]
            if mime_type and mime_type.startswith(base):
                return True
        elif rule.startswith("."):
            if extension == rule:
                return True
        else:
            if mime_type == rule:
                return True

    return False
