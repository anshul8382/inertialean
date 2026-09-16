# Inertia Investment Management Platform

A comprehensive investment management platform with AI-powered features, transaction processing, and portfolio analytics.

## 🚀 Features

### Core Features
- **Transaction Management** - Upload, preview, and process transactions with file validation
- **Portfolio Analytics** - Real-time portfolio tracking and performance analysis
- **Client Management** - Comprehensive client profiles and relationship management
- **AI-Powered Recommendations** - ML-driven investment recommendations
- **Market Commentary** - AI-generated market insights and analysis

### AI Models
- **Content Generation Model** - AI-powered content creation
- **Equity Research Model** - Stock analysis and research
- **Marketing Strategy Model** - Marketing strategy generation
- **Business Context Model** - Business intelligence and context

### Advanced Features
- **Workflow Management** - Automated investment workflows
- **Alert System** - Real-time notifications and alerts
- **Billing System** - Comprehensive billing and invoicing
- **Review System** - Client review scheduling and management
- **Cron Management** - Automated task scheduling

## 📁 Project Structure

```
inertia/
├── api/                    # API endpoints and core functionality
├── ai_models/             # AI models and ML infrastructure
├── analytics/             # Analytics and reporting
├── config/                # Configuration files
├── deployment/            # Deployment scripts
├── docs/                  # Documentation
├── leads/                 # Lead management
├── migrations/            # Database migrations
├── routes/                # Application routes
├── scripts/               # Utility scripts
│   ├── backup/           # Backup scripts
│   ├── migration/        # Migration utilities
│   └── utilities/        # General utilities
├── static/               # Static assets
├── templates/            # HTML templates
├── main.py              # Main application entry point
├── models.py            # Database models
├── config.py            # Application configuration
└── requirements.txt     # Python dependencies
```

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/anshul8382/inertia.git
   cd inertia
   ```

2. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp config.py.example config.py
   # Edit config.py with your settings
   ```

5. **Initialize database**
   ```bash
   python run_migrations.py
   ```

6. **Start the application**
   ```bash
   python main.py
   ```

## 🚀 Deployment

### Production Deployment
```bash
# Using the deployment script
./deployment/deploy-inertiainvest.in.sh
```

### Development Mode
```bash
python run_app.py
```

## 📊 API Documentation

The application provides comprehensive REST APIs:

- **Transactions API** - `/api/v1/transactions/`
- **Clients API** - `/api/v1/clients/`
- **AI Models API** - `/ai_models/`
- **Analytics API** - `/api/v1/performance/`

## 🤖 AI Models

The platform includes several AI models:

1. **Content Generation** - Generate market commentary and insights
2. **Equity Research** - Analyze stocks and provide recommendations
3. **Marketing Strategy** - Create marketing strategies
4. **Business Context** - Provide business intelligence

## 📈 Transaction Processing

### File Upload & Preview
- Support for Excel/CSV files
- Real-time validation and preview
- Security symbol prefix handling (NSE:, BSE:, BOM:)
- Support for SPLIT/BONUS transactions

### Bulk Processing
- Process up to 1000 transactions per batch
- Automatic holdings updates
- Cashflow integration
- Error handling and rollback

## 🔧 Configuration

Key configuration files:
- `config.py` - Main application configuration
- `config/gunicorn.conf.py` - Production server configuration
- `config/nginx.conf` - Web server configuration

## 📝 Documentation

Comprehensive documentation is available in the `docs/` directory:

- **API Documentation** - Complete API reference
- **User Manual** - End-user guide
- **Deployment Guide** - Production deployment instructions
- **Transaction Upload Guide** - File upload and processing guide

## 🏷️ Version History

- **v2025.09.26-comprehensive** - Complete feature set with AI models and transaction preview
- **v2025.09.26** - Transaction preview feature with file validation
- **v2025.08.27** - AI-powered market commentary system
- **v2025.06.08** - Initial release with basic functionality

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License

This project is proprietary software. All rights reserved.

## 📞 Support

For support and questions, please contact the development team.
