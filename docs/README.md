# Inertia Investment Management System

A comprehensive investment management system for tracking client portfolios, managing stock allocations, and generating investment recommendations.

## Features

- User Authentication and Authorization
- Client Portfolio Management
- Stock Tracking and Price Updates
- Transaction Management
- Asset and Stock Allocation Models
- Investment Recommendations
- Email Notifications
- Lead Management

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Linux/Mac
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure the database:
- Create a MySQL database named `inertia_app2025`
- Update database credentials in `config.py`

4. Initialize the database:
```bash
flask db upgrade
```

5. Create admin user:
```bash
python create_admin.py
```

6. Run the application:
```bash
gunicorn --bind 0.0.0.0:5000 wsgi:application
```

## Configuration

The application uses different configurations for development and production environments. Update the settings in `config.py` as needed.

## Version Control

This project uses Git for version control. The main branch is `master`.

## License

Proprietary - All rights reserved 