#!/usr/bin/env python3
"""
NIFTY Price Scanner
Scans NIFTY benchmark prices for issues, anomalies, and data quality problems
"""

import sys
import os
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Benchmark, BenchmarkData

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/nifty_scan.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def scan_nifty_prices():
    """Scan NIFTY prices for issues"""
    try:
        app = create_app()
        
        with app.app_context():
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                print("❌ NIFTY 50 benchmark not found!")
                return
            
            print(f"\n{'='*80}")
            print(f"NIFTY PRICE SCAN REPORT")
            print(f"{'='*80}\n")
            print(f"Benchmark: {benchmark.name} ({benchmark.symbol})")
            print(f"Scan Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            # Get all NIFTY prices
            all_prices = BenchmarkData.query.filter_by(
                benchmark_id=benchmark.id
            ).order_by(BenchmarkData.date).all()
            
            if not all_prices:
                print("❌ No NIFTY price data found in database!")
                return
            
            total_records = len(all_prices)
            print(f"Total Records: {total_records:,}")
            
            # Convert to list of dicts for analysis
            prices_data = []
            for p in all_prices:
                prices_data.append({
                    'id': p.id,
                    'date': p.date,
                    'price': float(p.price),
                    'created_at': p.created_at
                })
            
            # 1. Basic Statistics
            print(f"\n{'─'*80}")
            print("BASIC STATISTICS")
            print(f"{'─'*80}")
            
            prices = [p['price'] for p in prices_data]
            min_price = min(prices)
            max_price = max(prices)
            avg_price = sum(prices) / len(prices)
            
            print(f"Date Range: {prices_data[0]['date']} to {prices_data[-1]['date']}")
            print(f"Min Price: ₹{min_price:,.2f}")
            print(f"Max Price: ₹{max_price:,.2f}")
            print(f"Avg Price: ₹{avg_price:,.2f}")
            
            # 2. Check for Suspiciously Low Prices
            print(f"\n{'─'*80}")
            print("SUSPICIOUSLY LOW PRICES (< 10,000)")
            print(f"{'─'*80}")
            
            low_prices = [p for p in prices_data if p['price'] < 10000]
            if low_prices:
                print(f"⚠️  Found {len(low_prices)} records with prices below ₹10,000:")
                print(f"{'Date':<12} {'Price':<15} {'ID':<10} {'Created At'}")
                print("-" * 60)
                for p in sorted(low_prices, key=lambda x: x['date']):
                    print(f"{p['date']}  ₹{p['price']:>12,.2f}  {p['id']:<10} {p['created_at']}")
            else:
                print("✅ No suspiciously low prices found")
            
            # 3. Check for Known Bad Range (3,000-4,000)
            print(f"\n{'─'*80}")
            print("KNOWN BAD RANGE (3,000 - 4,000)")
            print(f"{'─'*80}")
            
            bad_range = [p for p in prices_data if 3000 <= p['price'] <= 4000]
            if bad_range:
                print(f"⚠️  Found {len(bad_range)} records in known bad range:")
                print(f"{'Date':<12} {'Price':<15} {'ID':<10} {'Created At'}")
                print("-" * 60)
                for p in sorted(bad_range, key=lambda x: x['date']):
                    print(f"{p['date']}  ₹{p['price']:>12,.2f}  {p['id']:<10} {p['created_at']}")
            else:
                print("✅ No prices in known bad range")
            
            # 4. Check for Suspiciously High Prices
            print(f"\n{'─'*80}")
            print("SUSPICIOUSLY HIGH PRICES (> 50,000)")
            print(f"{'─'*80}")
            
            high_prices = [p for p in prices_data if p['price'] > 50000]
            if high_prices:
                print(f"⚠️  Found {len(high_prices)} records with prices above ₹50,000:")
                print(f"{'Date':<12} {'Price':<15} {'ID':<10} {'Created At'}")
                print("-" * 60)
                for p in sorted(high_prices, key=lambda x: x['date']):
                    print(f"{p['date']}  ₹{p['price']:>12,.2f}  {p['id']:<10} {p['created_at']}")
            else:
                print("✅ No suspiciously high prices found")
            
            # 5. Check for Large Price Changes (Day-over-Day)
            print(f"\n{'─'*80}")
            print("LARGE DAY-OVER-DAY PRICE CHANGES (> 10%)")
            print(f"{'─'*80}")
            
            large_changes = []
            for i in range(1, len(prices_data)):
                prev_price = prices_data[i-1]['price']
                curr_price = prices_data[i]['price']
                change_pct = abs((curr_price - prev_price) / prev_price * 100) if prev_price > 0 else 0
                
                if change_pct > 10:
                    large_changes.append({
                        'date': prices_data[i]['date'],
                        'prev_date': prices_data[i-1]['date'],
                        'prev_price': prev_price,
                        'curr_price': curr_price,
                        'change_pct': change_pct,
                        'id': prices_data[i]['id']
                    })
            
            if large_changes:
                print(f"⚠️  Found {len(large_changes)} large price changes:")
                print(f"{'Date':<12} {'Prev Price':<15} {'Curr Price':<15} {'Change %':<10} {'ID'}")
                print("-" * 70)
                for change in large_changes[:20]:  # Show first 20
                    print(f"{change['date']}  ₹{change['prev_price']:>12,.2f}  ₹{change['curr_price']:>12,.2f}  {change['change_pct']:>8.2f}%  {change['id']}")
                if len(large_changes) > 20:
                    print(f"... and {len(large_changes) - 20} more")
            else:
                print("✅ No suspiciously large day-over-day changes found")
            
            # 6. Check for Missing Dates (Gaps)
            print(f"\n{'─'*80}")
            print("MISSING DATES (GAPS IN DATA)")
            print(f"{'─'*80}")
            
            if len(prices_data) > 1:
                gaps = []
                current_date = prices_data[0]['date']
                end_date = prices_data[-1]['date']
                
                # Check for gaps larger than 7 days (weekends + holidays)
                for i in range(1, len(prices_data)):
                    prev_date = prices_data[i-1]['date']
                    curr_date = prices_data[i]['date']
                    days_diff = (curr_date - prev_date).days
                    
                    if days_diff > 7:
                        gaps.append({
                            'gap_start': prev_date,
                            'gap_end': curr_date,
                            'days': days_diff
                        })
                
                if gaps:
                    print(f"⚠️  Found {len(gaps)} significant gaps (> 7 days):")
                    print(f"{'Gap Start':<12} {'Gap End':<12} {'Days':<10}")
                    print("-" * 40)
                    for gap in gaps[:20]:  # Show first 20
                        print(f"{gap['gap_start']}  {gap['gap_end']}  {gap['days']:>8} days")
                    if len(gaps) > 20:
                        print(f"... and {len(gaps) - 20} more")
                else:
                    print("✅ No significant gaps found (all gaps <= 7 days)")
            
            # 7. Check for Duplicate Dates
            print(f"\n{'─'*80}")
            print("DUPLICATE DATES")
            print(f"{'─'*80}")
            
            date_counts = defaultdict(list)
            for p in prices_data:
                date_counts[p['date']].append(p)
            
            duplicates = {date: records for date, records in date_counts.items() if len(records) > 1}
            
            if duplicates:
                print(f"⚠️  Found {len(duplicates)} dates with multiple records:")
                print(f"{'Date':<12} {'Count':<10} {'IDs':<30} {'Prices'}")
                print("-" * 80)
                for dup_date, records in sorted(duplicates.items())[:20]:  # Show first 20
                    ids = ', '.join(str(r['id']) for r in records)
                    prices_str = ', '.join(f"₹{r['price']:,.2f}" for r in records)
                    print(f"{dup_date}  {len(records):<10} {ids:<30} {prices_str}")
                if len(duplicates) > 20:
                    print(f"... and {len(duplicates) - 20} more duplicate dates")
            else:
                print("✅ No duplicate dates found")
            
            # 8. Price Distribution Analysis
            print(f"\n{'─'*80}")
            print("PRICE DISTRIBUTION ANALYSIS")
            print(f"{'─'*80}")
            
            # Count prices in different ranges
            ranges = [
                (0, 5000, "0 - 5,000"),
                (5000, 10000, "5,000 - 10,000"),
                (10000, 15000, "10,000 - 15,000"),
                (15000, 20000, "15,000 - 20,000"),
                (20000, 25000, "20,000 - 25,000"),
                (25000, 30000, "25,000 - 30,000"),
                (30000, 50000, "30,000 - 50,000"),
                (50000, float('inf'), "50,000+")
            ]
            
            print(f"{'Range':<20} {'Count':<10} {'Percentage':<10}")
            print("-" * 40)
            for min_val, max_val, label in ranges:
                count = sum(1 for p in prices_data if min_val <= p['price'] < max_val)
                pct = (count / total_records * 100) if total_records > 0 else 0
                print(f"{label:<20} {count:<10,} {pct:>8.2f}%")
            
            # 9. Recent Data Check
            print(f"\n{'─'*80}")
            print("RECENT DATA CHECK (Last 30 Days)")
            print(f"{'─'*80}")
            
            today = date.today()
            thirty_days_ago = today - timedelta(days=30)
            recent_prices = [p for p in prices_data if p['date'] >= thirty_days_ago]
            
            if recent_prices:
                print(f"Records in last 30 days: {len(recent_prices)}")
                print(f"Latest date: {max(p['date'] for p in recent_prices)}")
                print(f"Latest price: ₹{max(p['price'] for p in recent_prices):,.2f}")
                
                days_since_latest = (today - max(p['date'] for p in recent_prices)).days
                if days_since_latest > 3:
                    print(f"⚠️  Latest data is {days_since_latest} days old (may need update)")
                else:
                    print(f"✅ Data is recent ({days_since_latest} days old)")
            else:
                print("⚠️  No data in last 30 days!")
            
            # 10. Summary
            print(f"\n{'─'*80}")
            print("SUMMARY")
            print(f"{'─'*80}")
            
            issues_found = []
            if low_prices:
                issues_found.append(f"{len(low_prices)} suspiciously low prices")
            if bad_range:
                issues_found.append(f"{len(bad_range)} prices in known bad range")
            if high_prices:
                issues_found.append(f"{len(high_prices)} suspiciously high prices")
            if large_changes:
                issues_found.append(f"{len(large_changes)} large price changes")
            if duplicates:
                issues_found.append(f"{len(duplicates)} duplicate dates")
            
            if issues_found:
                print("⚠️  ISSUES FOUND:")
                for issue in issues_found:
                    print(f"   - {issue}")
                print(f"\n💡 Recommendation: Review and fix the issues above")
            else:
                print("✅ No major issues detected!")
            
            print(f"\n{'='*80}\n")
            
            return {
                'total_records': total_records,
                'low_prices': len(low_prices),
                'bad_range': len(bad_range),
                'high_prices': len(high_prices),
                'large_changes': len(large_changes),
                'duplicates': len(duplicates),
                'issues_found': len(issues_found) > 0
            }
            
    except Exception as e:
        logger.error(f"Error scanning NIFTY prices: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}")
        return None

if __name__ == "__main__":
    print("Starting NIFTY price scan...")
    result = scan_nifty_prices()
    
    if result:
        if result['issues_found']:
            print(f"\n⚠️  Scan completed with {sum([result['low_prices'], result['bad_range'], result['high_prices'], result['large_changes'], result['duplicates']])} issues found")
        else:
            print("\n✅ Scan completed - no issues found")

