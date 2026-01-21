#!/usr/bin/env python3
"""
MIP003 Schema Converter

This module converts Kodosumi input schemas to MIP003 compliant schemas.
The conversion is designed to be easily extensible for future enhancements.
"""

import json
import re
from typing import Dict, List, Any, Optional

DEFAULT_FILE_MAX_SIZE = 4_718_592  # 4.5 MB default limit for file uploads
DEFAULT_ACCEPT_EXTS = [".pdf", ".png", ".jpg", ".jpeg"]
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "svg"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm"}
AUDIO_EXTS = {"mp3", "wav", "aac", "flac", "ogg"}
DOCUMENT_EXTS = {"pdf", "doc", "docx", "txt", "rtf", "ppt", "pptx", "xls", "xlsx", "csv"}
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz"}
KNOWN_ACCEPT_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOCUMENT_EXTS | ARCHIVE_EXTS

class MIP003Converter:
    """
    Converter class for transforming Kodosumi input schemas to MIP003 format.
    """
    
    def __init__(self):
        """Initialize the converter with mapping configurations."""
        self.type_mapping = {
            'text': 'text',
            'password': 'password',
            'number': 'number',
            'textarea': 'textarea',
            'date': 'date',
            'time': 'time',
            'datetime-local': 'datetime',
            'boolean': 'boolean',
            'select': 'option',
            'checkbox': 'boolean',
            'file': 'file'
        }
    
    def convert_schema(self, kodosumi_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convert a complete Kodosumi schema to MIP003 format.
        
        Args:
            kodosumi_schema: The original Kodosumi schema
            
        Returns:
            List of MIP003 compliant input definitions
        """
        mip003_schema = []
        
        # Extract elements from Kodosumi schema
        elements = kodosumi_schema.get('elements', [])
        
        for element in elements:
            element_type = element.get('type', '')
            
            # Skip non-input elements (markdown, html, submit, cancel)
            if element_type in ['markdown', 'html', 'submit', 'cancel']:
                continue
                
            # Convert input element to MIP003 format
            mip003_element = self._convert_element(element)
            if mip003_element:
                mip003_schema.append(mip003_element)
        
        return mip003_schema
    
    def _convert_element(self, element: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Convert a single Kodosumi element to MIP003 format.

        Args:
            element: Single Kodosumi input element

        Returns:
            MIP003 compliant element or None if not convertible
        """
        element_type = element.get('type', '')
        element_name = element.get('name', '')

        # Skip elements without name or unsupported types
        if not element_name or element_type not in self.type_mapping:
            return None

        # Get the base MIP003 type from mapping
        mip003_type = self.type_mapping[element_type]

        # Detect more specific types for text inputs
        if element_type == 'text':
            mip003_type = self._detect_text_subtype(element, element_name)

        # Ensure name is never null - use label, fall back to element_name, then to id
        display_name = element.get('label') or element_name or 'Input'
        # Convert snake_case/camelCase to Title Case for display
        if display_name == element_name:
            display_name = self._format_display_name(element_name)

        # Base MIP003 structure - don't include validations by default
        mip003_element = {
            'id': element_name,
            'type': mip003_type,
            'name': display_name,
            'data': {}
        }

        # Add data fields
        self._add_data_fields(element, mip003_element)

        # Add validations - this will only add the field if there are validations
        self._add_validations(element, mip003_element)

        return mip003_element

    def _detect_text_subtype(self, element: Dict[str, Any], element_name: str) -> str:
        """
        Detect if a text input should be a more specific MIP003 type.

        Args:
            element: Original Kodosumi element
            element_name: The element's name/id

        Returns:
            The appropriate MIP003 type (url, email, or text)
        """
        name_lower = element_name.lower()
        pattern = (element.get('pattern') or '').lower()
        placeholder = (element.get('placeholder') or '').lower()
        label = (element.get('label') or '').lower()

        # Check for URL type
        if ('url' in name_lower or 'url' in pattern or
            'url' in placeholder or 'url' in label or
            'http' in placeholder or 'https' in placeholder):
            return 'url'

        # Check for email type
        if ('email' in name_lower or 'email' in pattern or
            'email' in placeholder or 'email' in label or
            '@' in pattern):
            return 'email'

        return 'text'

    def _format_display_name(self, name: str) -> str:
        """
        Format a field name into a human-readable display name.

        Args:
            name: The field name (e.g., 'input_url', 'skipHitl')

        Returns:
            A formatted display name (e.g., 'Input Url', 'Skip Hitl')
        """
        # Handle snake_case
        result = name.replace('_', ' ')
        # Handle camelCase - insert space before uppercase letters
        result = re.sub(r'([a-z])([A-Z])', r'\1 \2', result)
        # Title case each word
        return result.title()
    
    def _add_data_fields(self, element: Dict[str, Any], mip003_element: Dict[str, Any]):
        """
        Add data fields to MIP003 element based on Kodosumi element.
        
        Args:
            element: Original Kodosumi element
            mip003_element: MIP003 element being constructed
        """
        data = mip003_element['data']
        
        # Add placeholder if available
        if element.get('placeholder'):
            data['placeholder'] = element['placeholder']
        
        # Handle select options
        if element.get('type') == 'select' and element.get('option'):
            values = []
            for option in element['option']:
                if isinstance(option, dict):
                    # For Kodosumi InputOption objects:
                    # - 'name' contains the actual option value
                    # - 'label' contains the display text
                    # - 'value' is a boolean indicating if selected
                    if option.get('name') is not None:
                        values.append(option['name'])
                elif isinstance(option, str):
                    # For string options, use as-is
                    values.append(option)
            
            if values:
                data['values'] = values
        
        # Handle checkbox options
        elif element.get('type') == 'checkbox' and element.get('option'):
            # For checkboxes, the option text can serve as placeholder
            data['placeholder'] = element.get('option', '')

        # Handle file inputs
        elif element.get('type') == 'file':
            # Default to URL output format; allow explicit override if provided
            data['outputFormat'] = element.get('outputFormat', 'url')
            # Add friendly size guidance if original schema specifies or falls back to default
            size_hint = element.get('max_size') or element.get('maxSize') or DEFAULT_FILE_MAX_SIZE
            description = element.get('description') or data.get('description')
            if size_hint and not description:
                try:
                    size_value = int(size_hint)
                    size_mb = size_value / (1024 * 1024)
                    if size_mb.is_integer():
                        friendly_size = f"{int(size_mb)}MB"
                    else:
                        friendly_size = f"{size_mb:.1f}MB".rstrip('0').rstrip('.')
                    data['description'] = f"Max upload size {friendly_size}"
                except (TypeError, ValueError):
                    data['description'] = "Max upload size 4.5MB"
    
    def _add_validations(self, element: Dict[str, Any], mip003_element: Dict[str, Any]):
        """
        Add validation rules to MIP003 element based on Kodosumi element.
        Only adds the validations field if there are actual validations.
        
        Args:
            element: Original Kodosumi element
            mip003_element: MIP003 element being constructed
        """
        validations = []
        element_type = element.get('type', '')
        
        # MIP003 spec: all fields are required by default
        # Only add optional validation if field is NOT required
        # EXCEPTION: Select fields are always required (override Kodosumi setting)
        if not element.get('required', False) and element_type not in ['select', 'file']:
            validations.append({
                'validation': 'optional',
                'value': 'true'
            })
        # Note: Required fields get no validation since required is the default
        
        # String-based validations
        if element_type in ['text', 'password', 'textarea']:
            self._add_string_validations(element, validations)
        
        # Number-based validations
        elif element_type == 'number':
            self._add_number_validations(element, validations)
        
        # Date/time validations
        elif element_type in ['date', 'time', 'datetime-local']:
            self._add_datetime_validations(element, validations)
        
        # Select validations
        elif element_type == 'select':
            self._add_select_validations(element, validations)
        elif element_type == 'file':
            self._add_file_validations(element, validations)
        
        # Only add validations field if there are actual validations
        if validations:
            mip003_element['validations'] = validations
    
    def _add_string_validations(self, element: Dict[str, Any], validations: List[Dict[str, Any]]):
        """Add string-specific validations."""
        
        # Min length
        if element.get('min_length'):
            validations.append({
                'validation': 'min',
                'value': str(element['min_length'])
            })
        
        # Max length
        if element.get('max_length'):
            validations.append({
                'validation': 'max',
                'value': str(element['max_length'])
            })
        
        # Pattern validation
        if element.get('pattern'):
            # Try to infer common patterns
            pattern = element['pattern']
            if 'email' in pattern.lower():
                validations.append({
                    'validation': 'format',
                    'value': 'email'
                })
            elif 'url' in pattern.lower():
                validations.append({
                    'validation': 'format',
                    'value': 'url'
                })
        
        # Password-specific validations
        if element.get('type') == 'password':
            # Ensure non-empty for passwords
            validations.append({
                'validation': 'format',
                'value': 'nonempty'
            })
    
    def _add_number_validations(self, element: Dict[str, Any], validations: List[Dict[str, Any]]):
        """Add number-specific validations."""
        
        # Min value
        if element.get('min_value') is not None:
            validations.append({
                'validation': 'min',
                'value': str(element['min_value'])
            })
        
        # Max value
        if element.get('max_value') is not None:
            validations.append({
                'validation': 'max',
                'value': str(element['max_value'])
            })
        
        # Step validation (convert to integer format if step is 1)
        if element.get('step') == 1:
            validations.append({
                'validation': 'format',
                'value': 'integer'
            })
    
    def _add_datetime_validations(self, element: Dict[str, Any], validations: List[Dict[str, Any]]):
        """Add date/time-specific validations."""
        
        # Min date/time
        if element.get('min_date') or element.get('min_time') or element.get('min_datetime'):
            min_val = element.get('min_date') or element.get('min_time') or element.get('min_datetime')
            validations.append({
                'validation': 'min',
                'value': str(min_val)
            })
        
        # Max date/time
        if element.get('max_date') or element.get('max_time') or element.get('max_datetime'):
            max_val = element.get('max_date') or element.get('max_time') or element.get('max_datetime')
            validations.append({
                'validation': 'max',
                'value': str(max_val)
            })
    
    def _add_select_validations(self, element: Dict[str, Any], validations: List[Dict[str, Any]]):
        """Add select-specific validations."""
        
        # Treat all select fields as single-select since Kodosumi doesn't distinguish
        # between single and multi-select. This ensures consistent behavior where
        # select fields only accept a single value.
        
        # All select fields are forced to be required, so always add min=1 and max=1
        validations.append({
            'validation': 'min',
            'value': '1'
        })
        validations.append({
            'validation': 'max',
            'value': '1'
        })

    def _add_file_validations(self, element: Dict[str, Any], validations: List[Dict[str, Any]]):
        """Add file-specific validations."""
        # Determine allowed file counts
        min_files = self._safe_int(
            element.get('min_files')
            or element.get('minFiles')
            or element.get('min_count')
            or element.get('minCount')
        )
        max_files = self._safe_int(
            element.get('max_files')
            or element.get('maxFiles')
            or element.get('max_count')
            or element.get('maxCount')
        )
        allows_multiple = element.get('multiple', False)
        is_required = element.get('required', False)

        if min_files is None:
            min_files = 1
        else:
            min_files = max(1, min_files)
        if max_files is None:
            max_files = max(min_files, 1) if allows_multiple else max(1, min_files or 0)
        if max_files < min_files:
            max_files = min_files

        validations.append({
            'validation': 'min',
            'value': str(min_files)
        })
        validations.append({
            'validation': 'max',
            'value': str(max_files)
        })

        # Add accepted file types if available
        accept_value = self._extract_file_accepts(element)
        validations.append({
            'validation': 'accept',
            'value': accept_value
        })

    def _extract_file_accepts(self, element: Dict[str, Any]) -> str:
        """Extract accepted file types from the Kodosumi element."""
        possible_keys = [
            'accept',
            'accepts',
            'accepted_types',
            'acceptedTypes',
            'allowed_file_types',
            'allowedFileTypes',
            'file_types',
            'fileTypes',
            'mime_types',
            'mimeTypes'
        ]

        values: List[str] = []
        for key in possible_keys:
            raw_value = element.get(key)
            if raw_value:
                if isinstance(raw_value, list):
                    values = [str(item).strip() for item in raw_value if str(item).strip()]
                elif isinstance(raw_value, str):
                    values = [part.strip() for part in raw_value.split(',') if part.strip()]
                else:
                    values = [str(raw_value).strip()]
                break

        if not values:
            inferred = self._infer_accept_from_text(element)
            if inferred:
                values = inferred

        if not values:
            values = DEFAULT_ACCEPT_EXTS.copy()

        normalised_values = self._normalise_accept_values(values)

        # Preserve order while removing duplicates
        seen: Dict[str, None] = {}
        for item in normalised_values:
            if item not in seen:
                seen[item] = None

        return ",".join(seen.keys())

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """Convert value to int when possible."""
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _infer_accept_from_text(self, element: Dict[str, Any]) -> List[str]:
        """Infer file accept values from descriptive text."""
        text_sources = []
        for key in ('label', 'description', 'placeholder', 'helperText'):
            raw_text = element.get(key)
            if isinstance(raw_text, str):
                text_sources.append(raw_text)

        combined_text = " ".join(text_sources)
        if not combined_text:
            return []

        tokens = set()
        # Look for tokens inside parentheses first
        for group in re.findall(r'\(([^)]+)\)', combined_text):
            tokens.update(self._tokenize_accept_tokens(group))

        # Fallback to scanning the whole text for known tokens
        if not tokens:
            tokens.update(self._tokenize_accept_tokens(combined_text))

        if not tokens:
            return []

        # Return explicit extensions in dotted format
        return [f".{token}" for token in sorted(tokens) if token]

    def _tokenize_accept_tokens(self, raw_value: str) -> set:
        """Split raw text into known file tokens."""
        tokens = set()
        for part in re.split(r'[,\s/]+', raw_value):
            sanitized = part.strip().lower().lstrip('.')
            if sanitized in KNOWN_ACCEPT_EXTS:
                tokens.add(sanitized)
        return tokens

    def _normalise_accept_values(self, values: List[str]) -> List[str]:
        """Ensure accept values follow extension or wildcard formats."""
        normalised = []
        for value in values:
            if not value:
                continue
            val = value.strip().lower()
            if not val:
                continue
            if val in ("*/*", "*") or val.endswith("/*"):
                normalised.append(val)
            elif val.startswith("."):
                normalised.append(val)
            else:
                # treat as extension without dot
                normalised.append(f".{val}")
        return normalised


def convert_kodosumi_to_mip003(kodosumi_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convenience function to convert Kodosumi schema to MIP003 format.
    
    Args:
        kodosumi_schema: The original Kodosumi schema
        
    Returns:
        List of MIP003 compliant input definitions
    """
    converter = MIP003Converter()
    return converter.convert_schema(kodosumi_schema)


def main():
    """
    Test the converter with example schemas.
    """
    # Test schema 1: YouTube Channel Analysis
    test_schema_1 = {
        "description": "AI-powered YouTube content analysis and research tool",
        "elements": [
            {
                "text": "# YouTube Channel Analysis\nEnter one query per line in the text area below.",
                "type": "markdown"
            },
            {
                "text": "<div class=\"space\"></div>",
                "type": "html"
            },
            {
                "cols": None,
                "label": "User Query",
                "max_length": None,
                "name": "queries",
                "placeholder": "Enter your YouTube Channel Analysis query here...",
                "required": True,
                "rows": None,
                "type": "textarea",
                "value": None
            },
            {
                "text": "Generate YouTube Channel Analysis Report",
                "type": "submit"
            }
        ]
    }
    
    print("Testing MIP003 Converter")
    print("=" * 50)
    
    converter = MIP003Converter()
    result = converter.convert_schema(test_schema_1)
    
    print("Input Schema:")
    print(json.dumps(test_schema_1, indent=2))
    print("\nMIP003 Output:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
