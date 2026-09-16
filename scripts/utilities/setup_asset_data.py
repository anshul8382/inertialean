from app import app, db
from models import AssetClass, AssetAllocationModel, AssetAllocation
from datetime import datetime

def setup_asset_data():
    with app.app_context():
        # First, create asset classes
        asset_classes = [
            ('Fixed Income', 'DEBT', 'Fixed income securities like bonds and debt funds'),
            ('Large Cap', 'EQUITY', 'Large capitalization stocks'),
            ('Mid Cap', 'EQUITY', 'Mid capitalization stocks'),
            ('Small Cap', 'EQUITY', 'Small capitalization stocks'),
            ('Gold', 'GOLD', 'Gold and gold-related investments'),
            ('International', 'INTL_EQUITY', 'International equity investments')
        ]
        
        # Create asset classes and store their IDs
        asset_class_ids = {}
        for name, type_, description in asset_classes:
            asset_class = AssetClass(
                name=name,
                type=type_,
                description=description
            )
            db.session.add(asset_class)
            db.session.flush()
            asset_class_ids[name] = asset_class.id

        # Create asset allocation models
        models = [
            ('Conservative Portfolio', 'Low risk portfolio with focus on fixed income and large-cap stocks', [
                ('Fixed Income', 40),
                ('Large Cap', 30),
                ('Mid Cap', 20),
                ('Small Cap', 10)
            ]),
            ('Moderate Portfolio', 'Balanced portfolio with equal focus on growth and stability', [
                ('Fixed Income', 30),
                ('Large Cap', 35),
                ('Mid Cap', 25),
                ('Small Cap', 10)
            ]),
            ('Aggressive Portfolio', 'High risk portfolio with focus on growth stocks', [
                ('Fixed Income', 20),
                ('Large Cap', 30),
                ('Mid Cap', 30),
                ('Small Cap', 20)
            ])
        ]

        # Create models and their allocations
        for model_name, description, allocations in models:
            model = AssetAllocationModel(
                name=model_name,
                description=description
            )
            db.session.add(model)
            db.session.flush()

            # Create allocations for this model
            for asset_name, percentage in allocations:
                allocation = AssetAllocation(
                    model_id=model.id,
                    asset_class_id=asset_class_ids[asset_name],
                    allocation_percentage=percentage
                )
                db.session.add(allocation)

        # Commit all changes
        db.session.commit()
        print("Asset data setup completed successfully!")

if __name__ == '__main__':
    setup_asset_data() 