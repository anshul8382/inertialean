# Client Review System with Airflow Integration

## Overview

The Client Review System provides comprehensive portfolio analysis and reporting capabilities with automated generation using Apache Airflow. The system generates detailed client reviews including performance analysis, asset allocation, transaction analysis, and recommendations.

## Features

### Core Functionality
- **Review Generation**: Create comprehensive, performance, allocation, and transaction reviews
- **Review Viewing**: Detailed web interface for viewing completed reviews
- **Review Sharing**: Secure client sharing with filtered data
- **Status Tracking**: Real-time status updates for review generation
- **Template Management**: Configurable review templates for different types

### Airflow Integration
- **Automated Generation**: Scheduled review generation using Airflow DAGs
- **Report Automation**: Daily and monthly report generation
- **SLA Monitoring**: Automated SLA checking and alerting
- **Error Handling**: Comprehensive error tracking and notification

## Architecture

### Database Models
- `Review`: Main review records with metadata and status
- `ReviewSection`: Individual sections of reviews (performance, allocation, etc.)
- `ReviewShare`: Client-shared versions with filtered data
- `ReviewTemplate`: Configurable templates for different review types

### Key Components
- `ReviewService`: Core business logic for review generation
- `review_routes.py`: Flask routes for web interface
- `templates/reviews/`: Jinja2 templates for web interface
- `airflow/dags/`: Airflow DAGs for automation

## Installation & Setup

### 1. Database Setup
```bash
# Create review tables
mysql -u inertia_admin -p'!Nert!a2025$' -h localhost -D inertia_app2025 < create_review_tables.sql
```

### 2. Airflow Setup
```bash
# Start Airflow services
./start_airflow.sh

# Stop Airflow services
./stop_airflow.sh
```

### 3. Sample Data
```bash
# Create sample review templates
python3 -c "
from main import create_app
from models import ReviewTemplate, User
from extensions import db

app = create_app()
with app.app_context():
    user = User.query.first()
    if user:
        templates = [
            {'name': 'Comprehensive Review', 'template_type': 'comprehensive'},
            {'name': 'Performance Review', 'template_type': 'performance'},
            {'name': 'Allocation Review', 'template_type': 'allocation'}
        ]
        for t in templates:
            template = ReviewTemplate(
                name=t['name'],
                template_type=t['template_type'],
                is_active=True,
                created_by=user.id
            )
            db.session.add(template)
        db.session.commit()
        print('Sample templates created')
"
```

## Usage

### Web Interface

#### Generate Review
1. Navigate to `/review/reviews/generate`
2. Select client, review type, and date range
3. Click "Generate Review"
4. Monitor status on review list page

#### View Reviews
1. Navigate to `/review/reviews`
2. Click "View" on completed reviews
3. Review includes:
   - Performance analysis with benchmark comparison
   - Asset allocation with drift analysis
   - Transaction summary and significant changes
   - Security performance (best/worst performers)
   - Recommendations and action items

#### Share Reviews
1. Click "Share" on completed reviews
2. Select sections to include in client version
3. Choose delivery method (email/download)
4. Client receives filtered, professional report

### Airflow DAGs

#### Client Review DAG (`client_review_dag.py`)
- **Schedule**: Daily at 9:00 AM
- **Purpose**: Generate comprehensive reviews for all clients
- **Features**: 
  - Automatic review generation
  - Email notifications
  - Error handling and retries

#### Daily Reports DAG (`daily_reports_dag.py`)
- **Schedule**: Daily at 8:00 AM
- **Purpose**: Generate daily operational reports
- **Reports**:
  - Leads report
  - Monthly investments report
  - Workflow report

#### Monthly Portfolio DAG (`monthly_portfolio_dag.py`)
- **Schedule**: Monthly on 1st at 10:00 AM
- **Purpose**: Generate monthly portfolio analysis
- **Features**:
  - Portfolio performance tracking
  - Asset allocation analysis
  - Rebalancing recommendations

#### SLA Check DAG (`sla_check_dag.py`)
- **Schedule**: Every 30 minutes
- **Purpose**: Monitor SLA compliance
- **Features**:
  - Lead response time monitoring
  - Workflow completion tracking
  - Alert generation for violations

#### Alert Report DAG (`alert_report_dag.py`)
- **Schedule**: Daily at 7:00 AM
- **Purpose**: Generate alert summary reports
- **Features**:
  - Alert statistics
  - Response time analysis
  - Escalation tracking

## Configuration

### Airflow Configuration
- **Web UI**: http://localhost:8080
- **Default credentials**: admin/admin
- **DAG folder**: `airflow/dags/`
- **Logs**: `airflow/logs/`

### Review System Configuration
- **Database**: MySQL with JSON column support
- **Templates**: Configurable via ReviewTemplate model
- **CSRF Protection**: Enabled for all forms
- **Error Handling**: Comprehensive logging and status tracking

## API Endpoints

### Review Management
- `GET /review/reviews` - List all reviews
- `GET /review/reviews/generate` - Review generation form
- `POST /review/reviews/generate` - Generate new review
- `GET /review/reviews/<id>` - View review details
- `GET /review/reviews/<id>/share` - Share review form
- `POST /review/reviews/<id>/share` - Share review with client
- `GET /api/reviews/<id>/status` - Get review status

### Review Data Structure
```json
{
  "client_id": 40,
  "client_name": "Anjali Menon",
  "start_date": "2025-02-24",
  "end_date": "2025-08-24",
  "review_type": "comprehensive",
  "generated_at": "2025-08-24T17:31:49",
  "sections": {
    "performance": {
      "start_value": 1000000,
      "end_value": 1100000,
      "total_return_percent": 10.0,
      "benchmark_return": 12.5,
      "excess_return": -2.5
    },
    "allocation": {
      "current_allocation": {...},
      "target_allocation": {...},
      "drift_analysis": {...}
    },
    "transactions": {
      "total_transactions": 95,
      "net_flow": 50000,
      "significant_changes": [...]
    },
    "securities": {
      "best_performers": [...],
      "worst_performers": [...]
    },
    "recommendations": [...]
  }
}
```

## Error Handling

### Common Issues & Solutions

#### Review Generation Fails
- **Cause**: Missing data or SQLAlchemy serialization issues
- **Solution**: Check client holdings and transactions, ensure JSON serialization

#### Template Formatting Errors
- **Cause**: Null values in template formatting
- **Solution**: Template includes null checks with fallbacks

#### Airflow DAG Failures
- **Cause**: Database connection or permission issues
- **Solution**: Check Airflow logs and database connectivity

### Logging
- **Application logs**: `app.log`
- **Airflow logs**: `airflow/logs/`
- **Review service logs**: Integrated with application logging

## Security

### Data Protection
- **Client filtering**: Reviews only accessible to assigned advisors
- **CSRF protection**: All forms protected against CSRF attacks
- **Data filtering**: Client-shared reviews exclude sensitive information

### Access Control
- **Role-based access**: Manager/advisor permissions
- **Client assignment**: Users can only access assigned clients
- **Audit trail**: All review actions logged

## Performance

### Optimization
- **Async processing**: Review generation runs asynchronously
- **Database indexing**: Optimized queries for large datasets
- **Caching**: Template and data caching for improved performance

### Monitoring
- **Status tracking**: Real-time review generation status
- **Performance metrics**: Generation time and data size tracking
- **Error monitoring**: Comprehensive error tracking and alerting

## Troubleshooting

### Review Stuck in "Generating" Status
```bash
# Check review status
mysql -u inertia_admin -p'!Nert!a2025$' -h localhost -D inertia_app2025 -e "SELECT * FROM review WHERE status='generating';"

# Manually trigger completion
python3 -c "
from main import create_app
from models import Review
from review_service import ReviewService

app = create_app()
with app.app_context():
    review = Review.query.filter_by(status='generating').first()
    if review:
        service = ReviewService()
        service.generate_review_async(review.id)
        print('Review generation completed')
"
```

### Airflow DAG Issues
```bash
# Check Airflow status
./start_airflow.sh

# View DAG logs
tail -f airflow/logs/scheduler/*.log

# Restart Airflow services
./stop_airflow.sh && ./start_airflow.sh
```

## Future Enhancements

### Planned Features
- **PDF Generation**: Export reviews as PDF documents
- **Email Integration**: Automated email delivery of reviews
- **Advanced Analytics**: Machine learning-based recommendations
- **Mobile App**: Mobile interface for review viewing
- **API Integration**: REST API for external integrations

### Performance Improvements
- **Background Processing**: Celery integration for heavy tasks
- **Database Optimization**: Advanced indexing and query optimization
- **Caching Layer**: Redis integration for improved performance

## Support

For technical support or questions about the review system:
- Check the logs for detailed error information
- Review the troubleshooting section above
- Contact the development team for complex issues

---

**Version**: 1.0.0  
**Last Updated**: August 24, 2025  
**Maintainer**: Inertia Investment Management System Team

