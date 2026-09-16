#!/usr/bin/env python3

import pandas as pd
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, BenchmarkData, Benchmark
from datetime import datetime

def upload_nifty_from_gsheet():
    """Upload Nifty data from Google Sheets to benchmark_data table"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Uploading Nifty Benchmark Data from Google Sheets ===")
        
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
        
        # Google Sheets URL
        gsheet_url = "https://docs.google.com/spreadsheets/d/1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM/edit?gid=87941785#gid=87941785"
        
        try:
            print(f"📊 Reading Google Sheets...")
            print(f"   URL: {gsheet_url}")
            
            # Convert Google Sheets URL to CSV export URL
            # Extract the sheet ID from the URL
            sheet_id = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
            csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=87941785"
            
            print(f"   CSV URL: {csv_url}")
            
            # Read the CSV data
            df = pd.read_csv(csv_url)
            
            print(f"✅ Successfully read Google Sheets")
            print(f"   Rows: {len(df)}")
            print(f"   Columns: {list(df.columns)}")
            
            # Display first few rows
            print(f"\n📋 First 5 rows:")
            print(df.head())
            
            # The data should have Date in column A and Price in column B
            # Let's rename columns for clarity
            if len(df.columns) >= 2:
                df.columns = ['Date', 'Price'] + list(df.columns[2:])
            
            print(f"\n📊 Column mapping:")
            print(f"   Date column: {df.columns[0]}")
            print(f"   Price column: {df.columns[1]}")
            
            # Clean and process data
            print(f"\n🔄 Processing data...")
            
            # Remove rows with missing data or #VALUE! errors
            df_clean = df.dropna(subset=['Date', 'Price'])
            df_clean = df_clean[df_clean['Price'] != '#VALUE!']
            df_clean = df_clean[df_clean['Price'] != '#N/A']
            
            print(f"   Clean rows: {len(df_clean)} (removed {len(df) - len(df_clean)} rows with missing/invalid data)")
            
            # Convert date column to datetime
            df_clean['Date'] = pd.to_datetime(df_clean['Date'], errors='coerce')
            df_clean = df_clean.dropna(subset=['Date'])
            print(f"   Valid dates: {len(df_clean)}")
            
            # Convert price to numeric
            df_clean['Price'] = pd.to_numeric(df_clean['Price'], errors='coerce')
            df_clean = df_clean.dropna(subset=['Price'])
            print(f"   Valid prices: {len(df_clean)}")
            
            # Sort by date
            df_clean = df_clean.sort_values('Date')
            
            # Display sample data
            print(f"\n📊 Sample processed data:")
            print(df_clean[['Date', 'Price']].head(10))
            
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
                        date=row['Date'].date(),
                        price=row['Price'],
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
                min_date = df_clean['Date'].min()
                max_date = df_clean['Date'].max()
                min_price = df_clean['Price'].min()
                max_price = df_clean['Price'].max()
                avg_price = df_clean['Price'].mean()
                
                print(f"\n📊 Data Summary:")
                print(f"   Date range: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
                print(f"   Price range: ₹{min_price:.2f} to ₹{max_price:.2f}")
                print(f"   Average price: ₹{avg_price:.2f}")
                print(f"   Total records: {inserted_count}")
            
        except Exception as e:
            print(f"❌ Error processing Google Sheets: {str(e)}")
            print(f"   This might be due to:")
            print(f"   - Network connectivity issues")
            print(f"   - Google Sheets access permissions")
            print(f"   - Sheet structure changes")
            db.session.rollback()

if __name__ == '__main__':
    upload_nifty_from_gsheet() 