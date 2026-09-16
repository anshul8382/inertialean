"""
Comprehensive Client Deletion Analyzer
Analyzes all database dependencies and safely deletes a client and all related records.
"""

from models import (
    Client, Portfolio, Transaction, Holding, Cashflow, Meeting, Document,
    CallLog, Recommendation, RecommendationSession, Review, 
    MonthlyInvestment, ModelAssignment, PortfolioSnapshot,
    ClientAdvisorAssignment, ServiceTicket, Workflow, WorkflowAction,
    MonthlyInvestmentSchedule, ReviewSchedule, ReviewWorkflow, Lead,
    AgreementVariables
)
from alert_system_models import Alert as AlertModel
from extensions import db
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class ClientDeletionAnalyzer:
    """Analyzes and handles client deletion with all dependencies"""
    
    def __init__(self, client_id: int):
        self.client_id = client_id
        self.client = None
        self.dependencies = {}
        self.deletion_order = []
        
    def analyze_dependencies(self) -> Dict[str, Dict]:
        """
        Analyze all database dependencies for the client.
        Returns a dictionary with table names and record counts.
        """
        if not self.client:
            self.client = Client.query.get(self.client_id)
            if not self.client:
                raise ValueError(f"Client with ID {self.client_id} not found")
        
        dependencies = {}
        
        # Direct relationships (client_id foreign key)
        dependencies['Portfolios'] = {
            'count': Portfolio.query.filter_by(client_id=self.client_id).count(),
            'table': 'portfolio',
            'critical': True,
            'description': 'Client portfolios'
        }
        
        dependencies['Transactions'] = {
            'count': Transaction.query.filter_by(client_id=self.client_id).count(),
            'table': 'transaction',
            'critical': True,
            'description': 'All buy/sell transactions'
        }
        
        dependencies['Holdings'] = {
            'count': Holding.query.filter_by(client_id=self.client_id).count(),
            'table': 'holding',
            'critical': True,
            'description': 'Current security holdings'
        }
        
        dependencies['Cashflows'] = {
            'count': Cashflow.query.filter_by(client_id=self.client_id).count(),
            'table': 'cashflow',
            'critical': True,
            'description': 'Cashflow records (investments/withdrawals)'
        }
        
        dependencies['Meetings'] = {
            'count': Meeting.query.filter_by(client_id=self.client_id).count(),
            'table': 'meeting',
            'critical': False,
            'description': 'Scheduled and past meetings'
        }
        
        dependencies['Documents'] = {
            'count': Document.query.filter_by(client_id=self.client_id).count(),
            'table': 'document',
            'critical': False,
            'description': 'Uploaded documents'
        }
        
        dependencies['Call Logs'] = {
            'count': CallLog.query.filter_by(client_id=self.client_id).count(),
            'table': 'call_log',
            'critical': False,
            'description': 'Call history logs'
        }
        
        dependencies['Recommendations'] = {
            'count': Recommendation.query.filter_by(client_id=self.client_id).count(),
            'table': 'recommendation',
            'critical': True,
            'description': 'Investment recommendations'
        }
        
        dependencies['Recommendation Sessions'] = {
            'count': RecommendationSession.query.filter_by(client_id=self.client_id).count(),
            'table': 'recommendation_session',
            'critical': True,
            'description': 'Recommendation generation sessions'
        }
        
        dependencies['Reviews'] = {
            'count': Review.query.filter_by(client_id=self.client_id).count(),
            'table': 'review',
            'critical': True,
            'description': 'Portfolio review records'
        }
        
        dependencies['Monthly Investments'] = {
            'count': MonthlyInvestment.query.filter_by(client_id=self.client_id).count(),
            'table': 'monthly_investment',
            'critical': True,
            'description': 'Monthly investment records'
        }
        
        dependencies['Model Assignments'] = {
            'count': ModelAssignment.query.filter_by(client_id=self.client_id).count(),
            'table': 'model_assignment',
            'critical': False,
            'description': 'Asset allocation model assignments'
        }
        
        dependencies['Portfolio Snapshots'] = {
            'count': PortfolioSnapshot.query.filter_by(client_id=self.client_id).count(),
            'table': 'portfolio_snapshot',
            'critical': False,
            'description': 'Historical portfolio snapshots'
        }
        
        dependencies['Client Advisor Assignments'] = {
            'count': ClientAdvisorAssignment.query.filter_by(client_id=self.client_id).count(),
            'table': 'client_advisor_assignment',
            'critical': False,
            'description': 'Advisor assignment history'
        }
        
        dependencies['Service Tickets'] = {
            'count': ServiceTicket.query.filter_by(client_id=self.client_id).count(),
            'table': 'service_ticket',
            'critical': False,
            'description': 'Support/service tickets'
        }
        
        dependencies['Alerts'] = {
            'count': AlertModel.query.filter_by(client_id=self.client_id).count(),
            'table': 'alert',
            'critical': False,
            'description': 'System alerts and notifications'
        }
        
        dependencies['Review Schedules'] = {
            'count': ReviewSchedule.query.filter_by(client_id=self.client_id).count(),
            'table': 'review_schedule',
            'critical': False,
            'description': 'Review schedule configurations (cascade delete)'
        }
        
        dependencies['Review Workflows'] = {
            'count': ReviewWorkflow.query.filter_by(client_id=self.client_id).count(),
            'table': 'review_workflow',
            'critical': False,
            'description': 'Review workflow records (cascade delete)'
        }
        
        dependencies['Monthly Investment Schedule'] = {
            'count': MonthlyInvestmentSchedule.query.filter_by(client_id=self.client_id).count(),
            'table': 'monthly_investment_schedule',
            'critical': False,
            'description': 'Recurring investment schedule (cascade delete)'
        }
        
        dependencies['Agreement Variables'] = {
            'count': AgreementVariables.query.filter_by(client_id=self.client_id).count(),
            'table': 'agreement_variables',
            'critical': False,
            'description': 'Agreement template variables (cascade delete)'
        }
        
        dependencies['Lead'] = {
            'count': Lead.query.filter_by(client_id=self.client_id).count(),
            'table': 'lead',
            'critical': False,
            'description': 'Lead record (if converted from lead)'
        }
        
        # Indirect relationships (through MonthlyInvestment -> Workflow)
        monthly_investment_ids = [
            mi.id for mi in MonthlyInvestment.query.filter_by(client_id=self.client_id).all()
        ]
        
        dependencies['Workflows'] = {
            'count': Workflow.query.filter(Workflow.monthly_investment_id.in_(monthly_investment_ids)).count() if monthly_investment_ids else 0,
            'table': 'workflow',
            'critical': True,
            'description': 'Investment workflows (via monthly investments)',
            'indirect': True
        }
        
        # WorkflowActions (through Workflow)
        workflow_ids = [
            w.id for w in Workflow.query.filter(
                Workflow.monthly_investment_id.in_(monthly_investment_ids)
            ).all()
        ] if monthly_investment_ids else []
        
        dependencies['Workflow Actions'] = {
            'count': WorkflowAction.query.filter(WorkflowAction.workflow_id.in_(workflow_ids)).count() if workflow_ids else 0,
            'table': 'workflow_action',
            'critical': False,
            'description': 'Workflow action history (via workflows)',
            'indirect': True
        }
        
        self.dependencies = dependencies
        return dependencies
    
    def get_deletion_summary(self) -> Dict:
        """Get a formatted summary of what will be deleted"""
        total_records = sum(dep['count'] for dep in self.dependencies.values())
        critical_records = sum(
            dep['count'] for dep in self.dependencies.values() 
            if dep.get('critical', False)
        )
        
        return {
            'client_name': self.client.name,
            'client_id': self.client_id,
            'client_email': self.client.email,
            'total_tables': len([d for d in self.dependencies.values() if d['count'] > 0]),
            'total_records': total_records,
            'critical_records': critical_records,
            'dependencies': {k: v for k, v in self.dependencies.items() if v['count'] > 0}
        }
    
    def delete_client(self) -> Dict:
        """
        Delete client and all related records in proper order.
        Returns deletion results.
        """
        if not self.client:
            self.client = Client.query.get(self.client_id)
            if not self.client:
                raise ValueError(f"Client with ID {self.client_id} not found")
        
        if not self.dependencies:
            self.analyze_dependencies()
        
        results = {
            'deleted_tables': {},
            'errors': [],
            'total_deleted': 0
        }
        
        try:
            # Delete in proper order to avoid foreign key violations
            # Order: Most dependent -> Least dependent
            
            # 1. Delete alerts first (no dependencies)
            count = AlertModel.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Alerts'] = count
                results['total_deleted'] += count
            
            # 2. Delete workflow actions (via workflows)
            monthly_investment_ids = [
                mi.id for mi in MonthlyInvestment.query.filter_by(client_id=self.client_id).all()
            ]
            if monthly_investment_ids:
                workflow_ids = [
                    w.id for w in Workflow.query.filter(
                        Workflow.monthly_investment_id.in_(monthly_investment_ids)
                    ).all()
                ]
                if workflow_ids:
                    count = WorkflowAction.query.filter(
                        WorkflowAction.workflow_id.in_(workflow_ids)
                    ).delete()
                    if count > 0:
                        results['deleted_tables']['Workflow Actions'] = count
                        results['total_deleted'] += count
            
            # 3. Delete workflows
            if monthly_investment_ids:
                count = Workflow.query.filter(
                    Workflow.monthly_investment_id.in_(monthly_investment_ids)
                ).delete()
                if count > 0:
                    results['deleted_tables']['Workflows'] = count
                    results['total_deleted'] += count
            
            # 4. Delete transactions (affects holdings and cashflows)
            count = Transaction.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Transactions'] = count
                results['total_deleted'] += count
            
            # 5. Delete cashflows
            count = Cashflow.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Cashflows'] = count
                results['total_deleted'] += count
            
            # 6. Delete holdings
            count = Holding.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Holdings'] = count
                results['total_deleted'] += count
            
            # 7. Delete portfolio snapshots
            count = PortfolioSnapshot.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Portfolio Snapshots'] = count
                results['total_deleted'] += count
            
            # 8. Delete recommendation sessions and recommendations
            count = RecommendationSession.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Recommendation Sessions'] = count
                results['total_deleted'] += count
            
            count = Recommendation.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Recommendations'] = count
                results['total_deleted'] += count
            
            # 9. Delete reviews
            count = Review.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Reviews'] = count
                results['total_deleted'] += count
            
            # 10. Delete monthly investments
            count = MonthlyInvestment.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Monthly Investments'] = count
                results['total_deleted'] += count
            
            # 11. Delete model assignments
            count = ModelAssignment.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Model Assignments'] = count
                results['total_deleted'] += count
            
            # 12. Delete portfolios
            count = Portfolio.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Portfolios'] = count
                results['total_deleted'] += count
            
            # 13. Delete support records
            count = Meeting.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Meetings'] = count
                results['total_deleted'] += count
            
            count = Document.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Documents'] = count
                results['total_deleted'] += count
            
            count = CallLog.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Call Logs'] = count
                results['total_deleted'] += count
            
            count = ServiceTicket.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Service Tickets'] = count
                results['total_deleted'] += count
            
            count = ClientAdvisorAssignment.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Client Advisor Assignments'] = count
                results['total_deleted'] += count
            
            # 14. Delete cascade relationships (these should cascade, but we'll delete explicitly)
            count = ReviewWorkflow.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Review Workflows'] = count
                results['total_deleted'] += count
            
            count = ReviewSchedule.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Review Schedules'] = count
                results['total_deleted'] += count
            
            count = MonthlyInvestmentSchedule.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Monthly Investment Schedule'] = count
                results['total_deleted'] += count
            
            count = AgreementVariables.query.filter_by(client_id=self.client_id).delete()
            if count > 0:
                results['deleted_tables']['Agreement Variables'] = count
                results['total_deleted'] += count
            
            # 15. Update lead (set client_id to NULL if exists)
            Lead.query.filter_by(client_id=self.client_id).update({'client_id': None})
            
            # 16. Finally, delete the client
            db.session.delete(self.client)
            
            # Commit all changes
            db.session.commit()
            
            results['success'] = True
            results['client_name'] = self.client.name
            
        except Exception as e:
            db.session.rollback()
            results['success'] = False
            results['errors'].append(str(e))
            logger.error(f"Error deleting client {self.client_id}: {str(e)}")
            raise
        
        return results

