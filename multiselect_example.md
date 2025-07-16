# Multi-Select API Example

## 1. Get Input Schema

```bash
curl -X GET "https://api.masumi.network/data_analysis/input_schema" \
  -H "Accept: application/json"
```

### Expected Response:
```json
{
  "input_data": [
    {
      "id": "analysis_type",
      "type": "option",
      "name": "Analysis Types",
      "data": {
        "values": ["Statistical Analysis", "Machine Learning", "Data Visualization", "Predictive Modeling", "Time Series Analysis"]
      },
      "validations": [
        {"validation": "min", "value": "1"},
        {"validation": "max", "value": "3"}
      ]
    },
    {
      "id": "output_format",
      "type": "option", 
      "name": "Output Formats",
      "data": {
        "values": ["PDF Report", "Interactive Dashboard", "CSV Export", "JSON API Response"]
      },
      "validations": []
    },
    {
      "id": "dataset_url",
      "type": "string",
      "name": "Dataset URL",
      "data": {
        "placeholder": "https://example.com/dataset.csv"
      },
      "validations": []
    }
  ]
}
```

## 2. Start Job with Multi-Select Indices

```bash
curl -X POST "https://api.masumi.network/data_analysis/start_job" \
  -H "Content-Type: application/json" \
  -d '{
    "identifier_from_purchaser": "analysis-job-789",
    "input_data": {
      "analysis_type": [0, 2, 4],
      "output_format": [0, 1],
      "dataset_url": "https://example.com/sales-data-2024.csv"
    }
  }'
```

### What the indices mean:
- `analysis_type: [0, 2, 4]` selects:
  - 0: "Statistical Analysis"
  - 2: "Data Visualization"  
  - 4: "Time Series Analysis"
  
- `output_format: [0, 1]` selects:
  - 0: "PDF Report"
  - 1: "Interactive Dashboard"

### Expected Response:
```json
{
  "status": "success",
  "job_id": "job_123abc456def",
  "blockchainIdentifier": "0x7f8e9d2c3b4a5f6e",
  "submitResultTime": 1736899200000,
  "unlockTime": 1736902800000,
  "externalDisputeUnlockTime": 1736906400000,
  "payByTime": 1736892000000,
  "agentIdentifier": "agent_data_analysis_v2",
  "sellerVKey": "addr1vxwhatever...",
  "identifierFromPurchaser": "analysis-job-789",
  "amounts": [
    {
      "amount": 5000000,
      "unit": "lovelace"
    }
  ],
  "input_hash": "sha256:abcd1234..."
}
```

## 3. Check Job Status

```bash
curl -X GET "https://api.masumi.network/data_analysis/status?job_id=job_123abc456def" \
  -H "Accept: application/json"
```

## Important Notes:

1. **Always use arrays** for multi-select fields, even for single selections:
   - Correct: `"analysis_type": [1]`
   - Wrong: `"analysis_type": 1`

2. **Indices are 0-based**: First option is 0, second is 1, etc.

3. **Validation constraints**:
   - If `min` is specified, you must select at least that many options
   - If `max` is specified, you cannot select more than that many options
   - In the example above, `analysis_type` requires 1-3 selections

4. **Invalid index errors**:
   - Sending `[5]` for a field with only 5 options (0-4) will return an error
   - Error: "Invalid option index '5'. Valid indices are 0 to 4"