"""
Service Readiness Scanner
Automatically detects service implementations and tracks adoption
"""
import os
import re
from datetime import datetime
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class ServiceReadinessService:
    """Scans codebase to detect actual service usage vs direct DB access"""
    
    def __init__(self):
        self.app_root = "/home/inertia/app"
        
        # Define all expected services and their patterns
        self.service_patterns = {
            'PriceService': {
                'service_file': 'services/price_service.py',
                'usage_patterns': [
                    r'PriceService\.get_price',
                    r'from services\.price_service import',
                    r'import.*price_service'
                ],
                'direct_db_patterns': [
                    r'HistoricalPrice\.query',
                    r'Security\.query\.get',
                    r'db\.session\.query\(HistoricalPrice\)'
                ],
                'category': 'Core Data'
            },
            'CashflowService': {
                'service_file': 'services/cashflow_service.py',
                'usage_patterns': [
                    r'CashflowService\.',
                    r'from services\.cashflow_service import'
                ],
                'direct_db_patterns': [
                    r'Cashflow\.query',
                    r'db\.session\.query\(Cashflow\)'
                ],
                'category': 'Core Data'
            },
            'BenchmarkService': {
                'service_file': 'services/benchmark_service.py',
                'usage_patterns': [
                    r'BenchmarkService\.',
                    r'from services\.benchmark_service import'
                ],
                'direct_db_patterns': [
                    r'BenchmarkData\.query',
                    r'Benchmark\.query'
                ],
                'category': 'Core Data'
            },
            'AdjustmentsService': {
                'service_file': 'services/adjustments_service.py',
                'usage_patterns': [
                    r'AdjustmentsService\.',
                    r'from services\.adjustments_service import'
                ],
                'direct_db_patterns': [
                    r'CorporateAction\.query',
                    r'db\.session\.query\(CorporateAction\)'
                ],
                'category': 'Core Data'
            },
            'ForwardHoldingCalculationService': {
                'service_file': 'services/forward_holding_calculation_service.py',
                'usage_patterns': [
                    r'ForwardHoldingCalculationService\.',
                    r'from services\.forward_holding_calculation_service import'
                ],
                'direct_db_patterns': [
                    r'Holding\.query',
                    r'db\.session\.query\(Holding\)'
                ],
                'category': 'Portfolio'
            },
            'MergerReconstructionService': {
                'service_file': 'services/merger_reconstruction_service.py',
                'usage_patterns': [
                    r'MergerReconstructionService\.',
                    r'from services\.merger_reconstruction_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Portfolio'
            },
            'QuantityAdjustmentService': {
                'service_file': 'services/quantity_adjustment_service.py',
                'usage_patterns': [
                    r'QuantityAdjustmentService\.',
                    r'from services\.quantity_adjustment_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Portfolio'
            },
            'AuditTrailService': {
                'service_file': 'services/audit_trail_service.py',
                'usage_patterns': [
                    r'AuditTrailService\.',
                    r'from services\.audit_trail_service import'
                ],
                'direct_db_patterns': [],
                'category': 'System'
            },
            'AIInsightsService': {
                'service_file': 'services/ai_insights_service.py',
                'usage_patterns': [
                    r'AIInsightsService\.',
                    r'from services\.ai_insights_service import'
                ],
                'direct_db_patterns': [],
                'category': 'AI'
            },
            # Services that exist but are NOT in services/ directory
            'WorkflowService': {
                'service_file': 'workflow_service.py',
                'usage_patterns': [
                    r'WorkflowService\.',
                    r'from workflow_service import'
                ],
                'direct_db_patterns': [
                    r'Workflow\.query',
                    r'db\.session\.query\(Workflow\)'
                ],
                'category': 'Business Logic'
            },
            'ClientStatusService': {
                'service_file': 'client_status_service.py',
                'usage_patterns': [
                    r'ClientStatusService\.',
                    r'from client_status_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Business Logic'
            },
            'AlertService': {
                'service_file': 'alert_service.py',
                'usage_patterns': [
                    r'AlertService\.',
                    r'from alert_service import'
                ],
                'direct_db_patterns': [
                    r'Alert\.query',
                    r'db\.session\.query\(Alert\)'
                ],
                'category': 'System'
            },
            'PerformanceService': {
                'service_file': 'performance_service.py',
                'usage_patterns': [
                    r'PerformanceService\.',
                    r'from performance_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Analytics'
            },
            'UnifiedRecommendationService': {
                'service_file': 'unified_recommendation_service.py',
                'usage_patterns': [
                    r'UnifiedRecommendationService\.',
                    r'from unified_recommendation_service import'
                ],
                'direct_db_patterns': [
                    r'Recommendation\.query',
                    r'db\.session\.query\(Recommendation\)'
                ],
                'category': 'Business Logic'
            },
            'InvoiceGenerationService': {
                'service_file': 'invoice_generation_service.py',
                'usage_patterns': [
                    r'InvoiceGenerationService\.',
                    r'from invoice_generation_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Business Logic'
            },
            'MarketCommentaryService': {
                'service_file': 'market_commentary_service.py',
                'usage_patterns': [
                    r'MarketCommentaryService\.',
                    r'from market_commentary_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Content'
            },
            'UserService': {
                'service_file': 'user_service.py',
                'usage_patterns': [
                    r'UserService\.',
                    r'from user_service import'
                ],
                'direct_db_patterns': [
                    r'User\.query',
                    r'db\.session\.query\(User\)'
                ],
                'category': 'System'
            },
            'BillingCalculationService': {
                'service_file': 'billing_calculation_service.py',
                'usage_patterns': [
                    r'BillingCalculationService\.',
                    r'from billing_calculation_service import'
                ],
                'direct_db_patterns': [],
                'category': 'Business Logic'
            },
            'MLFeedbackService': {
                'service_file': 'ml_feedback_service.py',
                'usage_patterns': [
                    r'MLFeedbackService\.',
                    r'from ml_feedback_service import'
                ],
                'direct_db_patterns': [],
                'category': 'AI'
            }
        }
        
        # Missing services that SHOULD exist
        self.missing_services = {
            'LeadService': {
                'category': 'CRM',
                'reason': 'Lead management operations use direct DB access',
                'priority': 'High'
            },
            'TransactionService': {
                'category': 'Core Data',
                'reason': 'Transaction operations use direct DB access',
                'priority': 'High'
            },
            'ClientService': {
                'category': 'CRM',
                'reason': 'Client operations use direct DB access',
                'priority': 'High'
            },
            'SecurityService': {
                'category': 'Core Data',
                'reason': 'Security management uses direct DB access',
                'priority': 'Medium'
            },
            'PortfolioService': {
                'category': 'Portfolio',
                'reason': 'Portfolio operations use direct DB access',
                'priority': 'High'
            },
            'ModelAssignmentService': {
                'category': 'Business Logic',
                'reason': 'Model assignment operations use direct DB access',
                'priority': 'Medium'
            },
            'AssetAllocationService': {
                'category': 'Business Logic',
                'reason': 'Asset allocation model operations use direct DB access',
                'priority': 'Medium'
            },
            'SecurityAllocationService': {
                'category': 'Business Logic',
                'reason': 'Security allocation model operations use direct DB access',
                'priority': 'Medium'
            },
            'MeetingService': {
                'category': 'CRM',
                'reason': 'Meeting operations use direct DB access',
                'priority': 'Low'
            },
            'DocumentService': {
                'category': 'System',
                'reason': 'Document operations use direct DB access',
                'priority': 'Low'
            }
        }
    
    def scan_codebase(self) -> Dict:
        """Scan all Python files to detect service usage"""
        results = {}
        
        for service_name, config in self.service_patterns.items():
            results[service_name] = self._analyze_service_usage(service_name, config)
        
        return results
    
    def _analyze_service_usage(self, service_name: str, config: Dict) -> Dict:
        """Analyze usage of a specific service"""
        service_usage = 0
        direct_db_usage = 0
        files_using_service = []
        files_using_direct_db = []
        
        # Scan all Python files
        for root, dirs, files in os.walk(self.app_root):
            # Skip certain directories
            if any(skip_dir in root for skip_dir in ['__pycache__', '.git', 'node_modules', 'venv', 'env']):
                continue
                
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, self.app_root)
                    
                    # Skip the service file itself
                    if config['service_file'] in relative_path:
                        continue
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            
                        # Check for service usage
                        for pattern in config['usage_patterns']:
                            if re.search(pattern, content):
                                service_usage += 1
                                if relative_path not in files_using_service:
                                    files_using_service.append(relative_path)
                                break
                        
                        # Check for direct DB usage
                        for pattern in config['direct_db_patterns']:
                            if re.search(pattern, content):
                                direct_db_usage += 1
                                if relative_path not in files_using_direct_db:
                                    files_using_direct_db.append(relative_path)
                                break
                                
                    except Exception as e:
                        continue
        
        # Calculate adoption percentage
        total_usage = service_usage + direct_db_usage
        adoption_percentage = (service_usage / total_usage * 100) if total_usage > 0 else 0
        
        return {
            'service_usage': service_usage,
            'direct_db_usage': direct_db_usage,
            'adoption_percentage': round(adoption_percentage, 1),
            'files_using_service': files_using_service[:10],  # Limit to 10 files
            'files_using_direct_db': files_using_direct_db[:10],  # Limit to 10 files
            'status': self._determine_status(adoption_percentage),
            'category': config['category'],
            'service_file': config['service_file']
        }
    
    def _determine_status(self, adoption_percentage: float) -> str:
        """Determine service status based on adoption percentage"""
        if adoption_percentage >= 80:
            return 'Complete'
        elif adoption_percentage >= 50:
            return 'Partial'
        elif adoption_percentage >= 20:
            return 'Limited'
        else:
            return 'Not Adopted'
    
    def calculate_summary(self, service_status: Dict) -> Dict:
        """Calculate overall summary statistics"""
        total_services = len(service_status)
        complete_services = sum(1 for s in service_status.values() if s['status'] == 'Complete')
        partial_services = sum(1 for s in service_status.values() if s['status'] == 'Partial')
        limited_services = sum(1 for s in service_status.values() if s['status'] == 'Limited')
        not_adopted_services = sum(1 for s in service_status.values() if s['status'] == 'Not Adopted')
        
        overall_adoption = sum(s['adoption_percentage'] for s in service_status.values()) / total_services if total_services > 0 else 0
        
        return {
            'total_services': total_services,
            'complete_services': complete_services,
            'partial_services': partial_services,
            'limited_services': limited_services,
            'not_adopted_services': not_adopted_services,
            'overall_adoption': round(overall_adoption, 1),
            'missing_services': len(self.missing_services)
        }
    
    def get_missing_services(self) -> Dict:
        """Get list of services that should exist but don't"""
        return self.missing_services

