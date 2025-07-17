#!/usr/bin/env python3
"""
MIP003 Schema Converter

This module converts Kodosumi input schemas to MIP003 compliant schemas.
The conversion is designed to be easily extensible for future enhancements.
"""

import json
from typing import Dict, List, Any, Optional

class MIP003Converter:
    """
    Converter class for transforming Kodosumi input schemas to MIP003 format.
    """
    
    def __init__(self):
        """Initialize the converter with mapping configurations."""
        self.type_mapping = {
            'text': 'string',
            'password': 'string',
            'number': 'number',
            'textarea': 'textarea',
            'date': 'string',
            'time': 'string',
            'datetime-local': 'string',
            'boolean': 'boolean',
            'select': 'option',
            'checkbox': 'boolean'
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
        
        # Base MIP003 structure - don't include validations by default
        mip003_element = {
            'id': element_name,
            'type': self.type_mapping[element_type],
            'name': element.get('label', element_name),
            'data': {}
        }
        
        # Add data fields
        self._add_data_fields(element, mip003_element)
        
        # Add validations - this will only add the field if there are validations
        self._add_validations(element, mip003_element)
        
        return mip003_element
    
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
        if not element.get('required', False) and element_type != 'select':
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