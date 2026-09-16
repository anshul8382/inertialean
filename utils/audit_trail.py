"""
Audit Trail System - Complete calculation logging in human-readable format
Logs all API calls, calculations, and intermediate steps for verification
"""
import os
import glob
from datetime import datetime
from decimal import Decimal


class AuditTrail:
    """Creates comprehensive audit trail files for portfolio calculations"""
    
    def __init__(self, client_id, analysis_type, start_date, end_date):
        self.client_id = client_id
        self.analysis_type = analysis_type
        self.start_date = start_date
        self.end_date = end_date
        self.lines = []
        self.step_number = 0
        
        # Create audit file
        audit_dir = '/home/inertia/app/logs/audit_trails'
        os.makedirs(audit_dir, exist_ok=True)
        
        # Clean up existing audit files for same client and date range
        self._cleanup_existing_audit_files(client_id, analysis_type, start_date, end_date)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'client_{client_id}_{analysis_type}_{start_date}_to_{end_date}_{timestamp}.txt'
        self.filepath = os.path.join(audit_dir, filename)
        
        # Write header
        self.add_header()
    
    def add_header(self):
        """Add audit trail header"""
        self.lines.append("=" * 140)
        self.lines.append(f"AUDIT TRAIL: PORTFOLIO PERIOD ANALYSIS")
        self.lines.append("=" * 140)
        self.lines.append(f"Client ID: {self.client_id}")
        self.lines.append(f"Analysis Type: {self.analysis_type}")
        self.lines.append(f"Period: {self.start_date} to {self.end_date}")
        self.lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.lines.append("=" * 140)
        self.lines.append("")
    
    def add_step(self, title):
        """Add a new step header"""
        self.step_number += 1
        self.lines.append("")
        self.lines.append("=" * 140)
        self.lines.append(f"STEP {self.step_number}: {title}")
        self.lines.append("=" * 140)
        self.lines.append("")
    
    def add_api_call(self, api_name, params):
        """Log an API call with parameters"""
        self.lines.append(f"API CALL: {api_name}")
        self.lines.append(f"Parameters:")
        for key, value in params.items():
            self.lines.append(f"  {key}: {value}")
        self.lines.append("")
    
    def add_table(self, title, headers, rows, totals=None):
        """Add a data table"""
        self.lines.append(f"{title}:")
        self.lines.append("")
        
        # Calculate column widths
        col_widths = [max(len(str(h)), 12) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(cell)))
        
        # Header
        header_line = " ".join(f"{headers[i]:<{col_widths[i]}}" for i in range(len(headers)))
        self.lines.append(header_line)
        self.lines.append("-" * sum(col_widths) + "-" * (len(headers) - 1) * 1)
        
        # Rows
        for row in rows:
            row_line = " ".join(f"{str(row[i]):<{col_widths[i]}}" for i in range(len(row)))
            self.lines.append(row_line)
        
        # Totals
        if totals:
            self.lines.append("-" * sum(col_widths) + "-" * (len(headers) - 1) * 1)
            total_line = " ".join(f"{str(totals[i]):<{col_widths[i]}}" for i in range(len(totals)))
            self.lines.append(total_line)
        
        self.lines.append("")
    
    def add_holdings_table(self, holdings, title="Holdings"):
        """Add holdings table with standard format"""
        headers = ['Symbol', 'Quantity', 'Price', 'Value']
        rows = []
        total_value = 0
        
        for h in holdings:
            symbol = h.get('symbol', 'N/A')
            qty = float(h.get('quantity', 0))
            price = float(h.get('current_price', h.get('price', 0)))
            value = float(h.get('current_value', h.get('value', qty * price)))
            
            rows.append([
                symbol,
                f"{qty:.2f}",
                f"₹{price:,.2f}",
                f"₹{value:,.2f}"
            ])
            total_value += value
        
        totals = ['TOTAL', '', '', f"₹{total_value:,.2f}"]
        self.add_table(title, headers, rows, totals)
        
        return total_value
    
    def add_calculation(self, description, formula, result):
        """Add a calculation with formula"""
        self.lines.append(f"Calculation: {description}")
        self.lines.append(f"  Formula: {formula}")
        self.lines.append(f"  Result: {result}")
        self.lines.append("")
    
    def add_text(self, text):
        """Add plain text"""
        self.lines.append(text)
    
    def add_section(self, title):
        """Add a section divider"""
        self.lines.append("")
        self.lines.append("-" * 140)
        self.lines.append(f"{title}")
        self.lines.append("-" * 140)
        self.lines.append("")
    
    def _cleanup_existing_audit_files(self, client_id, analysis_type, start_date, end_date):
        """Clean up ALL existing audit files for the same client and date range"""
        try:
            # Clean up ALL audit trail files for this client and date range
            audit_dir = '/home/inertia/app/logs/audit_trails'
            patterns = [
                f'client_{client_id}_{analysis_type}_{start_date}_to_{end_date}_*.txt',
                f'client_{client_id}_*_{start_date}_to_{end_date}_*.txt',
                f'client_{client_id}_*_{start_date}_*.txt',
                f'client_{client_id}_*_{end_date}_*.txt'
            ]
            
            for pattern in patterns:
                existing_files = glob.glob(os.path.join(audit_dir, pattern))
                for file_path in existing_files:
                    try:
                        os.remove(file_path)
                        print(f"Deleted existing audit file: {file_path}")
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")
            
            # Clean up ALL portfolio snapshot files for this client
            snapshot_dir = '/home/inertia/app/logs/portfolio_snapshots'
            snapshot_patterns = [
                f'client_{client_id}_{start_date}.txt',
                f'client_{client_id}_{end_date}.txt',
                f'client_{client_id}_*.txt'  # Delete all snapshots for this client
            ]
            
            for pattern in snapshot_patterns:
                existing_files = glob.glob(os.path.join(snapshot_dir, pattern))
                for file_path in existing_files:
                    try:
                        os.remove(file_path)
                        print(f"Deleted existing snapshot file: {file_path}")
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")
            
            # Clean up ALL reconstruction audit files for this client
            reconstruction_patterns = [
                f'reconstruction_client_{client_id}_{start_date}.txt',
                f'reconstruction_client_{client_id}_{end_date}.txt',
                f'reconstruction_client_{client_id}_*.txt'  # Delete all reconstruction files for this client
            ]
            
            for pattern in reconstruction_patterns:
                existing_files = glob.glob(os.path.join(audit_dir, pattern))
                for file_path in existing_files:
                    try:
                        os.remove(file_path)
                        print(f"Deleted existing reconstruction file: {file_path}")
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")
                        
        except Exception as e:
            print(f"Error during audit file cleanup: {e}")

    def save(self):
        """Save audit trail to file"""
        try:
            with open(self.filepath, 'w') as f:
                f.write('\n'.join(self.lines))
            return self.filepath
        except Exception as e:
            return None



