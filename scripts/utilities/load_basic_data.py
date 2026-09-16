from app import app, db
from models import User, StockAllocationModel, StockAllocation, Stock
from datetime import datetime

def load_basic_data():
    with app.app_context():
        # Check if admin user exists
        admin = User.query.filter_by(email='admin@example.com').first()
        if not admin:
            admin = User(
                username='inertia',
                email='admin@example.com'
            )
            admin.set_password('admin')
            db.session.add(admin)
            db.session.flush()  # Get admin ID
        else:
            print("Admin user already exists, skipping creation")

        # Create stock allocation models
        large_cap_model = StockAllocationModel(
            name='Large Cap Focus',
            description='Portfolio focused on large-cap stocks'
        )
        db.session.add(large_cap_model)
        db.session.flush()

        # Add allocations for large cap model
        large_cap_stocks = Stock.query.filter(Stock.symbol.in_(['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK'])).all()
        if large_cap_stocks:
            allocation_percentage = 100 / len(large_cap_stocks)
            for stock in large_cap_stocks:
                allocation = StockAllocation(
                    model_id=large_cap_model.id,
                    stock_id=stock.id,
                    allocation_percentage=allocation_percentage
                )
                db.session.add(allocation)

        mid_cap_model = StockAllocationModel(
            name='Mid Cap Focus',
            description='Portfolio focused on mid-cap stocks'
        )
        db.session.add(mid_cap_model)
        db.session.flush()

        # Add allocations for mid cap model
        mid_cap_stocks = Stock.query.filter(Stock.symbol.in_(['PERSISTENT', 'TATACOMM', 'MUTHOOTFIN', 'INDUSINDBK', 'BAJFINANCE'])).all()
        if mid_cap_stocks:
            allocation_percentage = 100 / len(mid_cap_stocks)
            for stock in mid_cap_stocks:
                allocation = StockAllocation(
                    model_id=mid_cap_model.id,
                    stock_id=stock.id,
                    allocation_percentage=allocation_percentage
                )
                db.session.add(allocation)

        # Commit all changes
        db.session.commit()
        print("Basic data loaded successfully!")

if __name__ == '__main__':
    load_basic_data() 