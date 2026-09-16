from app import create_app
from extensions import db
from models import Security, AssetClass, User
from datetime import datetime

app = create_app()
with app.app_context():
    # Get the first admin user for created_by field
    admin_user = User.query.filter_by(role='admin').first()
    if not admin_user:
        print("No admin user found. Please create an admin user first.")
        exit(1)
    
    # Sample securities data
    sample_securities = [
        # Large Cap Stocks
        {'symbol': 'AAPL', 'name': 'Apple Inc.', 'type': 'STOCK', 'asset_class': 'Large Cap'},
        {'symbol': 'MSFT', 'name': 'Microsoft Corporation', 'type': 'STOCK', 'asset_class': 'Large Cap'},
        {'symbol': 'GOOGL', 'name': 'Alphabet Inc.', 'type': 'STOCK', 'asset_class': 'Large Cap'},
        
        # Mid Cap Stocks
        {'symbol': 'UBER', 'name': 'Uber Technologies Inc.', 'type': 'STOCK', 'asset_class': 'Mid Cap'},
        {'symbol': 'SNAP', 'name': 'Snap Inc.', 'type': 'STOCK', 'asset_class': 'Mid Cap'},
        
        # Small Cap Stocks
        {'symbol': 'PLTR', 'name': 'Palantir Technologies Inc.', 'type': 'STOCK', 'asset_class': 'Small Cap'},
        {'symbol': 'RBLX', 'name': 'Roblox Corporation', 'type': 'STOCK', 'asset_class': 'Small Cap'},
        
        # ETFs
        {'symbol': 'SPY', 'name': 'SPDR S&P 500 ETF', 'type': 'ETF', 'asset_class': 'Large Cap'},
        {'symbol': 'QQQ', 'name': 'Invesco QQQ Trust', 'type': 'ETF', 'asset_class': 'Large Cap'},
        
        # Mutual Funds
        {'symbol': 'VTSAX', 'name': 'Vanguard Total Stock Market Index Fund', 'type': 'MUTUAL_FUND', 'asset_class': 'Large Cap'},
        {'symbol': 'VGT', 'name': 'Vanguard Information Technology ETF', 'type': 'ETF', 'asset_class': 'Large Cap'},
        
        # Bonds
        {'symbol': 'AGG', 'name': 'iShares Core U.S. Aggregate Bond ETF', 'type': 'BOND', 'asset_class': 'Fixed Income'},
        {'symbol': 'TLT', 'name': 'iShares 20+ Year Treasury Bond ETF', 'type': 'BOND', 'asset_class': 'Fixed Income'},
        
        # Gold
        {'symbol': 'GLD', 'name': 'SPDR Gold Shares', 'type': 'ETF', 'asset_class': 'Gold'},
        {'symbol': 'IAU', 'name': 'iShares Gold Trust', 'type': 'ETF', 'asset_class': 'Gold'},
        
        # International
        {'symbol': 'EFA', 'name': 'iShares MSCI EAFE ETF', 'type': 'ETF', 'asset_class': 'International'},
        {'symbol': 'VXUS', 'name': 'Vanguard Total International Stock ETF', 'type': 'ETF', 'asset_class': 'International'},
        
        # REITs
        {'symbol': 'VNQ', 'name': 'Vanguard Real Estate ETF', 'type': 'ETF', 'asset_class': 'REIT'},
        {'symbol': 'IYR', 'name': 'iShares U.S. Real Estate ETF', 'type': 'ETF', 'asset_class': 'REIT'}
    ]

    print("Adding sample securities...")
    for security_data in sample_securities:
        # Get the asset class
        asset_class = AssetClass.query.filter_by(name=security_data['asset_class']).first()
        if not asset_class:
            print(f"Asset class {security_data['asset_class']} not found, skipping {security_data['symbol']}")
            continue

        # Check if security already exists
        existing = Security.query.filter_by(symbol=security_data['symbol']).first()
        if existing:
            print(f"Security {security_data['symbol']} already exists, skipping")
            continue

        # Create new security
        security = Security(
            symbol=security_data['symbol'],
            name=security_data['name'],
            type=security_data['type'],
            asset_class_id=asset_class.id,
            created_at=datetime.utcnow(),
            created_by=admin_user.id
        )
        db.session.add(security)
        print(f"Added {security_data['symbol']}")

    try:
        db.session.commit()
        print("\nSuccessfully added sample securities!")
    except Exception as e:
        db.session.rollback()
        print(f"\nError adding securities: {str(e)}") 