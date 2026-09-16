# Portfolio Manager API Documentation

## Overview
This document provides comprehensive documentation for all API endpoints in the Portfolio Manager system. All APIs follow RESTful conventions and return standardized JSON responses.

## Base URL
```
http://localhost:5000/api/v1
```

## Response Format
All APIs return responses in the following format:
```json
{
  "success": true/false,
  "message": "Description of the operation",
  "data": { ... },
  "timestamp": "2025-09-08T15:30:00.000000",
  "version": "v1"
}
```

---

## 1. CLIENT MANAGEMENT APIs

### 1.1 List All Clients
**Endpoint:** `GET /clients/`
**Description:** Retrieve a paginated list of all clients

**Query Parameters:**
- `page` (int, optional): Page number (default: 1)
- `per_page` (int, optional): Items per page (default: 50, max: 100)
- `search` (string, optional): Search by name or email
- `status` (string, optional): Filter by status

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 82,
      "name": "Ritesh Rai",
      "email": "ritesh@example.com",
      "phone": "7893109483",
      "risk_profile": "moderate",
      "created_at": "2025-08-25T14:41:24"
    }
  ],
  "meta": {
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 1,
      "total_pages": 1
    }
  }
}
```

### 1.2 Get Client Details
**Endpoint:** `GET /clients/{client_id}`
**Description:** Get comprehensive client information including status, model assignments, and portfolio summary

**Path Parameters:**
- `client_id` (int, required): Client ID

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 82,
    "name": "Ritesh Rai",
    "email": "ritesh@example.com",
    "phone": "7893109483",
    "address": "Client Address",
    "risk_profile": "moderate",
    "risk_profile_updated_at": "2025-08-25T14:41:24",
    "date_of_birth": "1990-01-01",
    "created_at": "2025-08-25T14:41:24",
    "user_id": 3,
    "advisor_id": 1,
    "advisor": {
      "id": 1,
      "name": "Advisor Name"
    },
    "status": {
      "client_id": 82,
      "client_name": "Ritesh Rai",
      "score": 100,
      "status": "good",
      "recommendations": ["✅ EXCELLENT: Client status is healthy"],
      "details": {
        "total_active_alerts": 0,
        "resolution_rate": 1.0
      }
    },
    "model_assignment": {
      "id": 26,
      "asset_model_id": 2,
      "asset_model_name": "Moderate Portfolio",
      "stock_model_id": 1,
      "stock_model_name": "Inertia Core Portfolio",
      "assigned_at": "2025-08-25T14:41:24"
    },
    "review_schedule": {
      "id": 1,
      "frequency": "quarterly",
      "next_review_date": "2025-12-01",
      "is_active": true
    },
    "advisor_assignment": {
      "id": 1,
      "advisor_id": 1,
      "advisor_name": "Advisor Name",
      "assigned_at": "2025-08-25T14:41:24"
    },
    "lead": {
      "id": 1,
      "name": "Lead Name",
      "status": "converted",
      "created_at": "2025-08-25T14:41:24"
    },
    "active_agreement": {
      "id": 1,
      "status": "active",
      "effective_date": "2025-08-25",
      "billing_frequency": "monthly",
      "billing_status": "current"
    }
  }
}
```

### 1.3 Create Client
**Endpoint:** `POST /clients/`
**Description:** Create a new client

**Request Body:**
```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "phone": "9876543210",
  "address": "Client Address",
  "risk_profile": "moderate",
  "date_of_birth": "1990-01-01",
  "advisor_id": 1
}
```

### 1.4 Update Client
**Endpoint:** `PUT /clients/{client_id}`
**Description:** Update client information

**Path Parameters:**
- `client_id` (int, required): Client ID

**Request Body:** Same as Create Client

### 1.5 Delete Client
**Endpoint:** `DELETE /clients/{client_id}`
**Description:** Delete a client

**Path Parameters:**
- `client_id` (int, required): Client ID

### 1.6 Get Client Status
**Endpoint:** `GET /clients/{client_id}/status`
**Description:** Get client health status and recommendations

**Response:**
```json
{
  "success": true,
  "data": {
    "client_id": 82,
    "client_name": "Ritesh Rai",
    "score": 100,
    "status": "good",
    "recommendations": ["✅ EXCELLENT: Client status is healthy"],
    "details": {
      "total_active_alerts": 0,
      "critical_alerts": 0,
      "warning_alerts": 0,
      "info_alerts": 0,
      "resolution_rate": 1.0,
      "avg_alert_age_hours": 0
    },
    "violations": [],
    "last_updated": "2025-09-08T15:30:00.000000Z"
  }
}
```

### 1.7 Model Assignment APIs
**Endpoints:**
- `GET /clients/{client_id}/model-assignment` - Get model assignment
- `POST /clients/{client_id}/model-assignment` - Assign model to client

### 1.8 Review Schedule APIs
**Endpoints:**
- `GET /clients/{client_id}/review-schedule` - Get review schedule
- `POST /clients/{client_id}/review-schedule` - Set review schedule

### 1.9 Advisor Assignment APIs
**Endpoints:**
- `GET /clients/{client_id}/advisor-assignment` - Get advisor assignment
- `POST /clients/{client_id}/advisor-assignment` - Assign advisor to client

---

## 2. HOLDINGS APIs

### 2.1 Get Client Holdings
**Endpoint:** `GET /clients/{client_id}/holdings`
**Description:** Get all holdings for a specific client

**Query Parameters:**
- `page` (int, optional): Page number (default: 1)
- `per_page` (int, optional): Items per page (default: 50, max: 100)
- `include_zero` (bool, optional): Include zero holdings (default: false)
- `sort_by` (string, optional): Sort by field (current_value, quantity, security_name)
- `sort_order` (string, optional): Sort order (asc, desc)

**Response:**
```json
{
  "success": true,
  "data": {
    "holdings": [
      {
        "id": 511,
        "client_id": 82,
        "security_id": 2129,
        "security_name": "Bharat Bond ETF – April 2031",
        "security_symbol": "EBBETF0431",
        "quantity": 388.0,
        "average_price": 1288.08,
        "current_price": 1372.0,
        "total_cost": 499775.04,
        "current_value": 532336.0,
        "unrealized_pnl": 32560.96,
        "unrealized_pnl_percent": 6.52,
        "last_updated": "2025-08-25T19:53:52"
      }
    ],
    "summary": {
      "total_holdings": 38,
      "total_value": 3224061.23,
      "total_cost": 3122568.54,
      "total_unrealized_pnl": 101492.69,
      "page": 1,
      "per_page": 50,
      "pages": 1
    }
  }
}
```

### 2.2 Get Specific Holding
**Endpoint:** `GET /clients/{client_id}/holdings/{holding_id}`
**Description:** Get details of a specific holding

### 2.3 Refresh Holdings
**Endpoint:** `POST /clients/{client_id}/holdings/refresh`
**Description:** Refresh holdings data from latest transactions

### 2.4 Get Holdings Summary
**Endpoint:** `GET /clients/{client_id}/holdings/summary`
**Description:** Get portfolio summary statistics

---

## 3. TRANSACTIONS APIs

### 3.1 Get Client Transactions
**Endpoint:** `GET /clients/{client_id}/transactions`
**Description:** Get transaction history for a client

**Query Parameters:**
- `page` (int, optional): Page number (default: 1)
- `per_page` (int, optional): Items per page (default: 50, max: 100)
- `type` (string, optional): Filter by transaction type (BUY, SELL, SPLIT, BONUS)
- `security_id` (int, optional): Filter by security
- `start_date` (date, optional): Filter from date
- `end_date` (date, optional): Filter to date

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 2812,
      "client_id": 82,
      "security_id": 814,
      "security_name": "ICICI Lombard General Insurance Company Limited",
      "transaction_date": "2025-07-07T00:00:00",
      "type": "BUY",
      "quantity": 4.0,
      "price": 2036.2,
      "amount": 8144.8,
      "notes": null,
      "created_at": "2025-08-25T19:53:52"
    }
  ],
  "meta": {
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total": 165,
      "total_pages": 4
    }
  }
}
```

### 3.2 Create Transaction
**Endpoint:** `POST /clients/{client_id}/transactions`
**Description:** Create a new transaction

**Request Body:**
```json
{
  "security_id": 814,
  "transaction_date": "2025-09-08",
  "type": "BUY",
  "quantity": 10.0,
  "price": 2000.0,
  "notes": "Purchase notes"
}
```

### 3.3 Update Transaction
**Endpoint:** `PUT /clients/{client_id}/transactions/{transaction_id}`
**Description:** Update an existing transaction

### 3.4 Delete Transaction
**Endpoint:** `DELETE /clients/{client_id}/transactions/{transaction_id}`
**Description:** Delete a transaction

### 3.5 Bulk Create Transactions
**Endpoint:** `POST /clients/{client_id}/transactions/bulk`
**Description:** Create multiple transactions at once

**Request Body:**
```json
{
  "transactions": [
    {
      "security_id": 814,
      "transaction_date": "2025-09-08",
      "type": "BUY",
      "quantity": 10.0,
      "price": 2000.0
    },
    {
      "security_id": 815,
      "transaction_date": "2025-09-08",
      "type": "SELL",
      "quantity": 5.0,
      "price": 1500.0
    }
  ]
}
```

### 3.6 Upload Transactions File
**Endpoint:** `POST /clients/{client_id}/transactions/upload`
**Description:** Upload transactions from CSV/Excel file

**Request:** Multipart form data with file

---

## 4. CASHFLOWS APIs

### 4.1 Get Client Cashflows
**Endpoint:** `GET /clients/{client_id}/cashflows`
**Description:** Get cashflow history for a client

**Query Parameters:**
- `page` (int, optional): Page number
- `per_page` (int, optional): Items per page
- `type` (string, optional): Filter by type (INFLOW, OUTFLOW)
- `start_date` (date, optional): Filter from date
- `end_date` (date, optional): Filter to date

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "client_id": 82,
      "date": "2025-08-01T00:00:00",
      "amount": -100000.0,
      "type": "OUTFLOW",
      "description": "Initial Investment",
      "created_at": "2025-08-25T19:53:52"
    }
  ]
}
```

### 4.2 Create Cashflow
**Endpoint:** `POST /clients/{client_id}/cashflows`
**Description:** Create a new cashflow entry

**Request Body:**
```json
{
  "date": "2025-09-08",
  "amount": -50000.0,
  "type": "OUTFLOW",
  "description": "Additional Investment"
}
```

### 4.3 Update Cashflow
**Endpoint:** `PUT /clients/{client_id}/cashflows/{cashflow_id}`
**Description:** Update an existing cashflow

### 4.4 Delete Cashflow
**Endpoint:** `DELETE /clients/{client_id}/cashflows/{cashflow_id}`
**Description:** Delete a cashflow entry

### 4.5 Get Cashflow Summary
**Endpoint:** `GET /clients/{client_id}/cashflows/summary`
**Description:** Get cashflow summary statistics

---

## 5. PERFORMANCE APIs

### 5.1 Get Basic Performance
**Endpoint:** `GET /clients/{client_id}/performance`
**Description:** Get basic performance metrics

**Response:**
```json
{
  "success": true,
  "data": {
    "xirr": 0.039,
    "xirr_percent": 3.9,
    "total_invested": 3122568.54,
    "total_withdrawn": 20472.25,
    "net_investment": 3102096.29,
    "current_value": 3224061.23,
    "absolute_return": 3.9,
    "holdings_summary": {
      "total_holdings": 38,
      "total_value": 3224061.23,
      "total_cost": 3122568.54,
      "total_unrealized_pnl": 101492.69
    }
  }
}
```

### 5.2 Get Performance Returns
**Endpoint:** `GET /clients/{client_id}/performance/returns`
**Description:** Get detailed returns analysis

**Query Parameters:**
- `period` (string, optional): Time period (1M, 3M, 6M, 1Y, 3Y, 5Y, ALL)
- `frequency` (string, optional): Data frequency (daily, weekly, monthly)

### 5.3 Get Risk Metrics
**Endpoint:** `GET /clients/{client_id}/performance/risk`
**Description:** Get portfolio risk analysis

**Response:**
```json
{
  "success": true,
  "data": {
    "portfolio_volatility": 15.0,
    "max_drawdown": -10.0,
    "var_95": -5.0,
    "beta": 1.0,
    "sharpe_ratio": 0.26
  }
}
```

### 5.4 Get XIRR Analysis
**Endpoint:** `GET /clients/{client_id}/performance/xirr`
**Description:** Get XIRR calculation details

### 5.5 Get Benchmark Comparison
**Endpoint:** `GET /clients/{client_id}/performance/benchmark`
**Description:** Compare portfolio performance against benchmark

**Query Parameters:**
- `benchmark_id` (int, optional): Benchmark ID (default: 1 for NIFTY 50)

**Response:**
```json
{
  "success": true,
  "data": {
    "client_xirr": 0.039,
    "benchmark_xirr": 0.039,
    "client_current_value": 3224061.23,
    "benchmark_current_value": 3218386.69,
    "outperformance": 0.0,
    "client_absolute_return": 3.9,
    "benchmark_absolute_return": 3.05
  }
}
```

### 5.6 Get Comprehensive Analytics
**Endpoint:** `GET /clients/{client_id}/performance/analytics`
**Description:** Get comprehensive performance analytics including XIRR, benchmark comparison, and risk metrics

**Response:**
```json
{
  "success": true,
  "data": {
    "xirr_analysis": {
      "xirr": 0.039,
      "xirr_percent": 3.9,
      "total_invested": 3122568.54,
      "total_withdrawn": 20472.25,
      "net_investment": 3102096.29,
      "current_value": 3224061.23,
      "absolute_return": 3.9
    },
    "benchmark_comparison": {
      "client_xirr": 0.039,
      "benchmark_xirr": 0.039,
      "outperformance": 0.0
    },
    "risk_metrics": {
      "portfolio_volatility": 15.0,
      "max_drawdown": -10.0,
      "var_95": -5.0,
      "beta": 1.0
    },
    "holdings_summary": {
      "total_holdings": 38,
      "total_value": 3224061.23,
      "total_cost": 3122568.54,
      "total_unrealized_pnl": 101492.69
    },
    "cashflow_summary": {
      "total_cashflows": 21,
      "total_invested": 3122568.54,
      "total_withdrawn": 20472.25,
      "net_investment": 3102096.29
    }
  }
}
```

---

## 6. TEST APIs

### 6.1 Simple Test
**Endpoint:** `GET /test/test`
**Description:** Simple API test endpoint

**Response:**
```json
{
  "success": true,
  "message": "API is working",
  "timestamp": "2025-09-08T15:30:00.000000"
}
```

### 6.2 Test Error
**Endpoint:** `GET /test/test-error`
**Description:** Test error handling

---

## Error Responses

All APIs return standardized error responses:

```json
{
  "success": false,
  "message": "Error description",
  "error": {
    "code": "VALIDATION_ERROR",
    "details": "Specific error details"
  },
  "timestamp": "2025-09-08T15:30:00.000000",
  "version": "v1"
}
```

## Common Error Codes
- `VALIDATION_ERROR`: Invalid input data
- `NOT_FOUND`: Resource not found
- `UNAUTHORIZED`: Authentication required
- `FORBIDDEN`: Insufficient permissions
- `CONFLICT`: Resource conflict
- `INTERNAL_ERROR`: Server error

## Authentication
Currently, authentication decorators are commented out for development. In production, these should be enabled:
- `@api_auth_required`: Requires valid API token
- `@api_rate_limit`: Implements rate limiting

## Rate Limiting
- Default: 60 requests per minute per endpoint
- Bulk operations: 10 requests per minute
- File uploads: 5 requests per minute

## Pagination
All list endpoints support pagination:
- `page`: Page number (starts from 1)
- `per_page`: Items per page (max 100)
- Response includes `meta.pagination` with total counts

## Data Types
- **Dates**: ISO 8601 format (YYYY-MM-DDTHH:MM:SS)
- **Decimals**: Numeric values with appropriate precision
- **IDs**: Integer values
- **Status**: String enums (e.g., "active", "inactive")

## Integration Notes
- All APIs are integrated with the UI
- Holdings and Transactions APIs are actively used by the client details page
- Performance APIs provide real-time portfolio analytics
- Client Status API provides health monitoring
