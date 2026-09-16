#!/bin/bash

# Analytics Setup Script for Investment Management System
# This script sets up the foundation for analytics implementation

echo "🚀 Setting up Analytics System for Investment Management"
echo "========================================================"

# Create analytics directory structure
echo "📁 Creating analytics directory structure..."
mkdir -p analytics/{services,utils,api,dashboards,models}
touch analytics/__init__.py
touch analytics/services/__init__.py
touch analytics/utils/__init__.py
touch analytics/api/__init__.py
touch analytics/dashboards/__init__.py

# Create logs directory
echo "📝 Creating logs directory..."
mkdir -p logs
touch logs/analytics.log

# Add analytics dependencies to requirements.txt
echo "📦 Adding analytics dependencies to requirements.txt..."
cat >> requirements.txt << EOF

# Analytics Dependencies
pandas>=1.5.0
numpy>=1.21.0
scikit-learn>=1.1.0
plotly>=5.0.0
dash>=2.0.0
redis>=4.0.0
joblib>=1.1.0
EOF

# Create analytics configuration
echo "⚙️ Creating analytics configuration..."
cat > analytics_config.env << EOF
# Analytics Configuration
ANALYTICS_ENABLED=True
ANALYTICS_LOG_LEVEL=INFO
ANALYTICS_DATA_RETENTION_DAYS=365
ANALYTICS_BATCH_SIZE=1000
ANALYTICS_REALTIME_ENABLED=False
ANALYTICS_ML_ENABLED=True
ANALYTICS_DASHBOARD_ENABLED=True
EOF

# Create test script
echo "🧪 Creating analytics test script..."
cat > test_analytics.py << 'EOF'
#!/usr/bin/env python3
"""
Analytics Test Script
Test basic analytics functionality
"""

import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_analytics_import():
    """Test if analytics can be imported"""
    try:
        from analytics import config
        print("✅ Analytics config imported successfully")
        print(f"   Analytics enabled: {config.is_enabled()}")
        return True
    except ImportError as e:
        print(f"❌ Error importing analytics: {e}")
        return False

def test_event_tracker():
    """Test event tracking functionality"""
    try:
        from analytics.utils.event_tracker import EventTracker
        tracker = EventTracker()
        print("✅ Event tracker created successfully")
        return True
    except ImportError as e:
        print(f"❌ Error importing event tracker: {e}")
        return False

def test_client_behavior_service():
    """Test client behavior service"""
    try:
        from analytics.services.client_behavior import ClientBehaviorService
        service = ClientBehaviorService()
        print("✅ Client behavior service created successfully")
        return True
    except ImportError as e:
        print(f"❌ Error importing client behavior service: {e}")
        return False

def main():
    """Run all tests"""
    print("🧪 Testing Analytics System")
    print("==========================")
    
    tests = [
        test_analytics_import,
        test_event_tracker,
        test_client_behavior_service
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Analytics system is ready.")
        print("\n📋 Next steps:")
        print("   1. Add analytics models to your database")
        print("   2. Update main.py to initialize analytics")
        print("   3. Add event tracking to your routes")
        print("   4. Test with real client data")
    else:
        print("⚠️ Some tests failed. Check the errors above.")

if __name__ == "__main__":
    main()
EOF

# Make test script executable
chmod +x test_analytics.py

# Create integration guide
echo "📚 Creating integration guide..."
cat > ANALYTICS_INTEGRATION_GUIDE.md << 'EOF'
# Analytics Integration Guide

## Quick Start

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Test Analytics System**
   ```bash
   python test_analytics.py
   ```

3. **Add Analytics to Main App**
   ```python
   # In main.py, add:
   from analytics import init_analytics
   
   # In create_app function:
   init_analytics(app)
   ```

4. **Add Event Tracking**
   ```python
   # In your routes, add:
   from analytics.utils.event_tracker import track_portfolio_view
   
   # Track events:
   track_portfolio_view(client_id)
   ```

## Configuration

Set environment variables in `analytics_config.env`:
- `ANALYTICS_ENABLED`: Enable/disable analytics
- `ANALYTICS_LOG_LEVEL`: Logging level
- `ANALYTICS_REALTIME_ENABLED`: Enable real-time tracking

## Testing

Run the test script to verify everything is working:
```bash
python test_analytics.py
```

## Documentation

- `analytics_roadmap.md`: Complete implementation roadmap
- `analytics_implementation_guide.md`: Step-by-step guide
- `analytics/`: Analytics source code

## Support

If you encounter issues:
1. Check the logs: `tail -f logs/analytics.log`
2. Verify configuration: `echo $ANALYTICS_ENABLED`
3. Test imports: `python -c "from analytics import config"`
EOF

echo "✅ Analytics setup complete!"
echo ""
echo "📋 What was created:"
echo "   - analytics/ directory with all subdirectories"
echo "   - requirements.txt updated with analytics dependencies"
echo "   - analytics_config.env with configuration"
echo "   - test_analytics.py for testing"
echo "   - ANALYTICS_INTEGRATION_GUIDE.md for next steps"
echo ""
echo "🚀 Next steps:"
echo "   1. Install dependencies: pip install -r requirements.txt"
echo "   2. Test the system: python test_analytics.py"
echo "   3. Follow the integration guide: ANALYTICS_INTEGRATION_GUIDE.md"
echo "   4. Check the roadmap: analytics_roadmap.md"
echo ""
echo "📚 Documentation:"
echo "   - analytics_roadmap.md: Complete implementation roadmap"
echo "   - analytics_implementation_guide.md: Step-by-step guide"
echo "   - ANALYTICS_INTEGRATION_GUIDE.md: Quick start guide"
echo ""
echo "🎉 Analytics foundation is ready for implementation!"

