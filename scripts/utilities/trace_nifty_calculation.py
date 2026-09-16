#!/usr/bin/env python3
"""
Script to trace through the exact Nifty calculation for Arpit Golash
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Client, Cashflow, Benchmark, BenchmarkData
from datetime import datetime, date
from decimal import Decimal

def trace_nifty_calculation():
    """Trace through the exact Nifty calculation for Arpit Golash"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Tracing Nifty Calculation for Arpit Golash ===")
        
        # Find Arpit Golash
        client = Client.query.filter(Client.name.ilike('%arpit%golash%')).first()
        
        if not client:
            print("❌ Arpit Golash not found")
            return
        
        print(f"✅ Client: {client.name} (ID: {client.id})")
        
        # Get cashflows
        cashflows = Cashflow.query.filter_by(client_id=client.id).order_by(Cashflow.date).all()
        print(f"📊 Found {len(cashflows)} cashflows")
        
        # Get Nifty benchmark data
        benchmark = Benchmark.query.filter_by(id=1).first()
        if not benchmark:
            print("❌ NIFTY 50 benchmark not found")
            return
        
        # Get benchmark data for the date range
        # Convert all dates to date objects before comparison to avoid datetime/date comparison errors
        date_list = [cf.date.date() if isinstance(cf.date, datetime) else cf.date for cf in cashflows]
        min_date = min(date_list)
        max_date = max(date_list)
        
        benchmark_data = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
            .filter(BenchmarkData.date >= min_date)\
            .filter(BenchmarkData.date <= max_date)\
            .order_by(BenchmarkData.date).all()
        
        # Create a price lookup dictionary
        price_lookup = {bd.date: float(bd.price) for bd in benchmark_data}
        
        # Get the latest benchmark price
        latest_benchmark = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
            .order_by(BenchmarkData.date.desc()).first()
        
        if not latest_benchmark:
            print("❌ No latest benchmark price found")
            return
        
        latest_price = float(latest_benchmark.price)
        
        print(f"\n📈 Latest Nifty Price: ₹{latest_price:,.2f} (as of {latest_benchmark.date.strftime('%Y-%m-%d')})")
        
        # Simulate Nifty investment step by step
        print(f"\n🧮 Step-by-Step Nifty Investment Simulation:")
        print("=" * 80)
        
        nifty_units = 0.0
        total_invested = 0.0
        total_withdrawn = 0.0
        weighted_total_cost = 0.0
        
        print(f"{'Step':<3} {'Date':<12} {'Amount':<15} {'Nifty Price':<12} {'Units':<10} {'Total Units':<12} {'Total Cost':<15}")
        print("-" * 80)
        
        step = 1
        
        # Process cashflows chronologically
        for cf in cashflows:
            cf_date = cf.date
            cf_amount = float(cf.amount)
            
            # Convert cf_date to date for comparison
            cf_date = cf_date.date() if hasattr(cf_date, 'date') else cf_date
            
            # Find the closest benchmark price for this date
            benchmark_price = None
            if cf_date in price_lookup:
                benchmark_price = price_lookup[cf_date]
            else:
                # Find the closest available price before this date
                available_dates = [d for d in price_lookup.keys() if d <= cf_date]
                if available_dates:
                    closest_date = max(available_dates)
                    benchmark_price = price_lookup[closest_date]
            
            if benchmark_price is None or benchmark_price <= 0:
                print(f"{step:<3} {cf_date.strftime('%Y-%m-%d'):<12} ₹{cf_amount:>13,.2f} {'N/A':<12} {'SKIP':<10} {nifty_units:>11,.4f} ₹{weighted_total_cost:>14,.2f}")
                step += 1
                continue
            
            if cf_amount < 0:  # Investment (negative amount)
                units_bought = abs(cf_amount) / benchmark_price
                nifty_units += units_bought
                total_invested += abs(cf_amount)
                weighted_total_cost += abs(cf_amount)  # Add to weighted cost
                
                print(f"{step:<3} {cf_date.strftime('%Y-%m-%d'):<12} ₹{cf_amount:>13,.2f} ₹{benchmark_price:>10,.2f} {units_bought:>9,.4f} {nifty_units:>11,.4f} ₹{weighted_total_cost:>14,.2f}")
                
            else:  # Withdrawal (positive amount)
                if nifty_units > 0:
                    units_sold = min(cf_amount / benchmark_price, nifty_units)
                    nifty_units -= units_sold
                    total_withdrawn += cf_amount
                    # Reduce weighted cost proportionally
                    if nifty_units > 0:
                        reduction_ratio = units_sold / (nifty_units + units_sold)
                        weighted_total_cost -= weighted_total_cost * reduction_ratio
                    
                    print(f"{step:<3} {cf_date.strftime('%Y-%m-%d'):<12} ₹{cf_amount:>13,.2f} ₹{benchmark_price:>10,.2f} -{units_sold:>8,.4f} {nifty_units:>11,.4f} ₹{weighted_total_cost:>14,.2f}")
                else:
                    print(f"{step:<3} {cf_date.strftime('%Y-%m-%d'):<12} ₹{cf_amount:>13,.2f} ₹{benchmark_price:>10,.2f} {'NO UNITS':<10} {nifty_units:>11,.4f} ₹{weighted_total_cost:>14,.2f}")
            
            step += 1
        
        print("-" * 80)
        
        # Calculate final values
        nifty_current_value = nifty_units * latest_price
        weighted_avg_nifty_price = weighted_total_cost / nifty_units if nifty_units > 0 else 0.0
        net_investment = total_invested - total_withdrawn
        
        # Calculate absolute return based on weighted average price
        if weighted_avg_nifty_price > 0 and nifty_units > 0:
            nifty_absolute_return = ((latest_price - weighted_avg_nifty_price) / weighted_avg_nifty_price * 100)
        else:
            nifty_absolute_return = 0.0
        
        print(f"\n📊 Final Calculation Results:")
        print(f"Total invested: ₹{total_invested:,.2f}")
        print(f"Total withdrawn: ₹{total_withdrawn:,.2f}")
        print(f"Net investment: ₹{net_investment:,.2f}")
        print(f"Nifty units: {nifty_units:.4f}")
        print(f"Weighted avg Nifty price: ₹{weighted_avg_nifty_price:,.2f}")
        print(f"Current Nifty price: ₹{latest_price:,.2f}")
        print(f"Nifty current value: ₹{nifty_current_value:,.2f}")
        print(f"Nifty absolute return: {nifty_absolute_return:.2f}%")
        
        # Manual verification
        print(f"\n🔍 Manual Verification:")
        print(f"Investment 1: ₹240,322 at ₹24,620.20 = {240322/24620.20:.4f} units")
        print(f"Investment 2: ₹39,787 at ₹25,637.80 = {39787/25637.80:.4f} units")
        print(f"Total units: {240322/24620.20 + 39787/25637.80:.4f}")
        print(f"Current value: {240322/24620.20 + 39787/25637.80:.4f} × ₹{latest_price:,.2f} = ₹{(240322/24620.20 + 39787/25637.80) * latest_price:,.2f}")

if __name__ == "__main__":
    trace_nifty_calculation() 