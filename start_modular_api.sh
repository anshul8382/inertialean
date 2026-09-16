#!/bin/bash

# Modular API Startup Script
# Enterprise-grade API server for Inertia Investment Platform

echo "🚀 Starting Inertia Investment Platform - Modular API"
echo "=================================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run setup first."
    exit 1
fi

# Activate virtual environment
echo "📦 Activating virtual environment..."
source venv/bin/activate

# Check if required packages are installed
echo "🔍 Checking dependencies..."
python -c "import flask, flask_jwt_extended, flask_cors, flask_migrate, sqlalchemy, pandas" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "❌ Missing dependencies. Installing..."
    pip install flask flask-jwt-extended flask-cors flask-migrate sqlalchemy pandas marshmallow
fi

# Set environment variables
export API_HOST=${API_HOST:-"0.0.0.0"}
export API_PORT=${API_PORT:-5001}
export API_DEBUG=${API_DEBUG:-"False"}
export SECRET_KEY=${SECRET_KEY:-"dev-secret-key-change-in-production"}
export JWT_SECRET_KEY=${JWT_SECRET_KEY:-"jwt-secret-string"}
export DATABASE_URL=${DATABASE_URL:-"sqlite:///inertia_app.db"}

echo "🔧 Configuration:"
echo "   Host: $API_HOST"
echo "   Port: $API_PORT"
echo "   Debug: $API_DEBUG"
echo "   Database: $DATABASE_URL"

# Create logs directory if it doesn't exist
mkdir -p logs

# Start the API server
echo "🌟 Starting API server..."
echo "   Access API at: http://$API_HOST:$API_PORT"
echo "   API Docs at: http://$API_HOST:$API_PORT/api/docs"
echo "   Health Check: http://$API_HOST:$API_PORT/api/health"
echo ""
echo "📊 API Endpoints Available:"
echo "   - GET  /api/v1/clients           - List clients"
echo "   - POST /api/v1/clients           - Create client"
echo "   - GET  /api/v1/clients/{id}      - Get client details"
echo "   - PUT  /api/v1/clients/{id}      - Update client"
echo "   - GET  /api/v1/transactions      - List transactions"
echo "   - POST /api/v1/transactions      - Create transaction"
echo "   - POST /api/v1/transactions/bulk - Bulk create transactions"
echo "   - POST /api/v1/transactions/upload - Upload transaction file"
echo "   - POST /api/v1/transactions/preview - Preview transaction file"
echo ""
echo "🔐 Authentication required for all endpoints"
echo "   Use JWT token in Authorization header: Bearer <token>"
echo ""
echo "=================================================="

# Start the server
cd backend
python start_api.py
