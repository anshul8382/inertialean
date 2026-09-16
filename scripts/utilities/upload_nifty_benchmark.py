#!/usr/bin/env python3

import pandas as pd
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, BenchmarkData, Benchmark
from datetime import datetime

def upload_nifty_benchmark_data():
    """Upload Nifty data from spreadsheet to benchmark_data table"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Uploading Nifty Benchmark Data ===")
        
        # Check if benchmark exists
        benchmark = Benchmark.query.filter_by(id=1).first()
        if not benchmark:
            print("❌ Benchmark ID 1 (NIFTY 50) not found in database")
            print("   Creating benchmark first...")
            
            benchmark = Benchmark(
                id=1,
                name='NIFTY 50',
                symbol='^NSEI',
                description='Nifty 50 Index - Benchmark for Indian equity market',
                is_active=True
            )
            db.session.add(benchmark)
            db.session.commit()
            print("✅ Created NIFTY 50 benchmark")
        else:
            print(f"✅ Found benchmark: {benchmark.name} (ID: {benchmark.id})")
        
        # Read the spreadsheet
        spreadsheet_path = input("Enter the path to your spreadsheet file: ").strip()
        
        if not spreadsheet_path:
            print("❌ No spreadsheet path provided")
            return
        
        if not os.path.exists(spreadsheet_path):
            print(f"❌ File not found: {spreadsheet_path}")
            return
        
        try:
            print(f"📊 Reading spreadsheet: {spreadsheet_path}")
            print(f"   Sheet name: Nifty")
            
            # Read the Nifty sheet
            df = pd.read_excel(spreadsheet_path, sheet_name='Nifty')
            
            print(f"✅ Successfully read spreadsheet")
            print(f"   Rows: {len(df)}")
            print(f"   Columns: {list(df.columns)}")
            
            # Display first few rows
            print(f"\n📋 First 5 rows:")
            print(df.head())
            
            # Ask user to confirm column mapping
            print(f"\n🔍 Column Mapping:")
            print(f"   Please confirm the column names for:")
            
            # Try to identify date and price columns
            date_col = None
            price_col = None
            
            for col in df.columns:
                col_lower = col.lower()
                if 'date' in col_lower:
                    date_col = col
                elif 'price' in col_lower or 'close' in col_lower or 'value' in col_lower:
                    price_col = col
            
            if not date_col:
                date_col = input("Enter column name for DATE: ").strip()
            else:
                print(f"   Date column: {date_col}")
            
            if not price_col:
                price_col = input("Enter column name for PRICE: ").strip()
            else:
                print(f"   Price column: {price_col}")
            
            # Validate columns exist
            if date_col not in df.columns:
                print(f"❌ Date column '{date_col}' not found in spreadsheet")
                return
            
            if price_col not in df.columns:
                print(f"❌ Price column '{price_col}' not found in spreadsheet")
                return
            
            # Clean and process data
            print(f"\n🔄 Processing data...")
            
            # Remove rows with missing data
            df_clean = df.dropna(subset=[date_col, price_col])
            print(f"   Clean rows: {len(df_clean)} (removed {len(df) - len(df_clean)} rows with missing data)")
            
            # Convert date column to datetime
            df_clean[date_col] = pd.to_datetime(df_clean[date_col], errors='coerce')
            df_clean = df_clean.dropna(subset=[date_col])
            print(f"   Valid dates: {len(df_clean)}")
            
            # Convert price to numeric
            df_clean[price_col] = pd.to_numeric(df_clean[price_col], errors='coerce')
            df_clean = df_clean.dropna(subset=[price_col])
            print(f"   Valid prices: {len(df_clean)}")
            
            # Sort by date
            df_clean = df_clean.sort_values(date_col)
            
            # Display sample data
            print(f"\n📊 Sample processed data:")
            print(df_clean[[date_col, price_col]].head(10))
            
            # Ask for confirmation
            confirm = input(f"\n❓ Upload {len(df_clean)} records to benchmark_data table? (y/n): ").strip().lower()
            
            if confirm != 'y':
                print("❌ Upload cancelled")
                return
            
            # Clear existing data for this benchmark
            existing_count = BenchmarkData.query.filter_by(benchmark_id=1).count()
            if existing_count > 0:
                print(f"🗑️  Deleting {existing_count} existing records for NIFTY 50...")
                BenchmarkData.query.filter_by(benchmark_id=1).delete()
                db.session.commit()
            
            # Insert new data
            print(f"📤 Inserting {len(df_clean)} new records...")
            
            inserted_count = 0
            for index, row in df_clean.iterrows():
                try:
                    benchmark_data = BenchmarkData(
                        benchmark_id=1,
                        date=row[date_col].date(),
                        price=row[price_col],
                        created_at=datetime.utcnow()
                    )
                    db.session.add(benchmark_data)
                    inserted_count += 1
                    
                    # Commit in batches
                    if inserted_count % 100 == 0:
                        db.session.commit()
                        print(f"   Inserted {inserted_count} records...")
                        
                except Exception as e:
                    print(f"   ❌ Error inserting row {index}: {str(e)}")
                    continue
            
            # Final commit
            db.session.commit()
            
            print(f"\n✅ Successfully uploaded {inserted_count} Nifty benchmark records!")
            
            # Calculate some statistics
            if inserted_count > 0:
                min_date = df_clean[date_col].min()
                max_date = df_clean[date_col].max()
                min_price = df_clean[price_col].min()
                max_price = df_clean[price_col].max()
                avg_price = df_clean[price_col].mean()
                
                print(f"\n📊 Data Summary:")
                print(f"   Date range: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
                print(f"   Price range: ₹{min_price:.2f} to ₹{max_price:.2f}")
                print(f"   Average price: ₹{avg_price:.2f}")
                print(f"   Total records: {inserted_count}")
            
        except Exception as e:
            print(f"❌ Error processing spreadsheet: {str(e)}")
            db.session.rollback()

if __name__ == '__main__':
    upload_nifty_benchmark_data() 