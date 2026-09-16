from datetime import datetime, timedelta
from models import db, GenericWorkflow, GenericWorkflowAction, Lead, Client, Portfolio, Transaction
from flask_login import current_user

def get_current_user_id():
    try:
        if current_user and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return 1  # Default user ID for CLI/testing

class WorkflowService:
    """Service class to handle workflows for different modules"""
    
    # Workflow stage definitions for different modules
    WORKFLOW_STAGES = {
        'lead': [
            'REFERRAL',
            'MEETING',
            'RISK_PROFILE',
            'PROPOSAL',
            'ACCEPTANCE',
            'AGREEMENT',
            'AGREEMENT_SIGNING',
            'KYC',
            'INVOICE',
            'PAYMENT',
            'ONBOARDING',
            'WELCOME_RECOMMENDATION',
            'COMPLETED'
        ],
        'client_onboarding': [
            'DOCUMENTATION',
            'KYC_VERIFICATION',
            'RISK_ASSESSMENT',
            'PORTFOLIO_SETUP',
            'MODEL_ASSIGNMENT',
            'FIRST_INVESTMENT',
            'COMPLETED'
        ],
        'portfolio': [
            'ANALYSIS',
            'RECOMMENDATION',
            'CLIENT_APPROVAL',
            'EXECUTION',
            'CONFIRMATION',
            'UPDATE',
            'COMPLETED'
        ],
        'transaction': [
            'REQUEST',
            'VALIDATION',
            'APPROVAL',
            'EXECUTION',
            'CONFIRMATION',
            'SETTLEMENT',
            'COMPLETED'
        ]
    }
    
    @classmethod
    def create_workflow(cls, module_type, record_id, initial_stage=None, target_date=None, notes=None):
        """Create a new workflow for a module"""
        if module_type not in cls.WORKFLOW_STAGES:
            raise ValueError(f"Invalid module type: {module_type}")
        
        # Set default initial stage
        if not initial_stage:
            initial_stage = cls.WORKFLOW_STAGES[module_type][0]
        
        # Set default target date (7 days from now)
        if not target_date:
            target_date = datetime.now().date() + timedelta(days=7)
        
        workflow = GenericWorkflow(
            module_type=module_type,
            record_id=record_id,
            current_stage=initial_stage,
            target_completion_date=target_date,
            notes=notes,
            created_by=get_current_user_id()
        )
        
        db.session.add(workflow)
        db.session.commit()
        
        # Create initial action
        cls.add_action(workflow.id, 'WORKFLOW_CREATED', f'Workflow created for {module_type}', None)
        
        return workflow
    
    @classmethod
    def add_action(cls, workflow_id, action_type, notes=None, amount=None):
        """Add an action to a workflow"""
        # Convert empty string to None for amount field
        if amount == '' or amount is None:
            amount = None
        elif isinstance(amount, str) and amount.strip() == '':
            amount = None
            
        action = GenericWorkflowAction(
            workflow_id=workflow_id,
            action_type=action_type,
            action_date=datetime.utcnow(),
            notes=notes,
            amount=amount,
            user_id=get_current_user_id()
        )
        
        db.session.add(action)
        db.session.commit()
        return action
    
    @classmethod
    def update_stage(cls, workflow_id, new_stage, notes=None):
        """Update workflow stage"""
        workflow = GenericWorkflow.query.get_or_404(workflow_id)
        
        # Validate stage
        if workflow.module_type not in cls.WORKFLOW_STAGES:
            raise ValueError(f"Invalid module type: {workflow.module_type}")
        
        valid_stages = cls.WORKFLOW_STAGES[workflow.module_type]
        if new_stage not in valid_stages:
            raise ValueError(f"Invalid stage '{new_stage}' for module '{workflow.module_type}'")
        
        old_stage = workflow.current_stage
        workflow.current_stage = new_stage
        
        # Update completion date if completed
        if new_stage == 'COMPLETED':
            workflow.actual_completion_date = datetime.now().date()
            workflow.status = 'completed'
        
        db.session.commit()
        
        # Add action
        action_notes = f"Stage changed from {old_stage} to {new_stage}"
        if notes:
            action_notes += f" - {notes}"
        
        cls.add_action(workflow_id, 'STAGE_UPDATED', action_notes)
        
        return workflow
    
    @classmethod
    def get_workflow(cls, module_type, record_id):
        """Get workflow for a specific record"""
        return GenericWorkflow.query.filter_by(
            module_type=module_type,
            record_id=record_id
        ).first()
    
    @classmethod
    def get_workflows_by_module(cls, module_type, status=None):
        """Get all workflows for a module type"""
        query = GenericWorkflow.query.filter_by(module_type=module_type)
        if status:
            query = query.filter_by(status=status)
        return query.order_by(GenericWorkflow.created_at.desc()).all()
    
    @classmethod
    def get_next_stage(cls, module_type, current_stage):
        """Get the next stage in the workflow"""
        if module_type not in cls.WORKFLOW_STAGES:
            return None
        
        stages = cls.WORKFLOW_STAGES[module_type]
        try:
            current_index = stages.index(current_stage)
            if current_index < len(stages) - 1:
                return stages[current_index + 1]
        except ValueError:
            pass
        return None
    
    @classmethod
    def get_available_actions(cls, module_type, current_stage):
        """Get available actions for a stage"""
        actions = {
            'lead': {
                'REFERRAL': ['REFERRAL_CONTACTED', 'REFERRAL_FAILED', 'REFERRAL_RESCHEDULED'],
                'MEETING': ['MEETING_SCHEDULED', 'MEETING_COMPLETED', 'MEETING_CANCELLED'],
                'RISK_PROFILE': ['RISK_PROFILE_SENT', 'RISK_PROFILE_COMPLETED', 'RISK_PROFILE_PENDING'],
                'PROPOSAL': ['PROPOSAL_SENT', 'PROPOSAL_REVIEWED', 'PROPOSAL_REVISED'],
                'ACCEPTANCE': ['PROPOSAL_ACCEPTED', 'PROPOSAL_REJECTED', 'PROPOSAL_UNDER_REVIEW'],
                'AGREEMENT': ['AGREEMENT_SENT', 'AGREEMENT_REVIEWED', 'AGREEMENT_REVISED'],
                'AGREEMENT_SIGNING': ['AGREEMENT_SIGNED', 'AGREEMENT_CANCELLED', 'AGREEMENT_PENDING'],
                'KYC': ['KYC_STARTED', 'KYC_COMPLETED', 'KYC_REJECTED'],
                'INVOICE': ['INVOICE_SENT', 'INVOICE_PAID', 'INVOICE_PENDING'],
                'PAYMENT': ['PAYMENT_RECEIVED', 'PAYMENT_PENDING', 'PAYMENT_OVERDUE'],
                'ONBOARDING': ['ONBOARDING_STARTED', 'ONBOARDING_COMPLETED', 'ONBOARDING_DELAYED'],
                'WELCOME_RECOMMENDATION': ['WELCOME_SENT', 'RECOMMENDATION_SENT', 'COMPLETED']
            },
            'client_onboarding': {
                'DOCUMENTATION': ['DOCS_REQUESTED', 'DOCS_RECEIVED', 'DOCS_INCOMPLETE'],
                'KYC_VERIFICATION': ['KYC_STARTED', 'KYC_COMPLETED', 'KYC_REJECTED'],
                'RISK_ASSESSMENT': ['ASSESSMENT_SENT', 'ASSESSMENT_COMPLETED'],
                'PORTFOLIO_SETUP': ['PORTFOLIO_CREATED', 'PORTFOLIO_CONFIGURED'],
                'MODEL_ASSIGNMENT': ['MODEL_ASSIGNED', 'MODEL_CUSTOMIZED'],
                'FIRST_INVESTMENT': ['INVESTMENT_PLANNED', 'INVESTMENT_EXECUTED']
            },
            'portfolio': {
                'ANALYSIS': ['ANALYSIS_STARTED', 'ANALYSIS_COMPLETED'],
                'RECOMMENDATION': ['RECOMMENDATION_GENERATED', 'RECOMMENDATION_REVIEWED'],
                'CLIENT_APPROVAL': ['CLIENT_NOTIFIED', 'CLIENT_APPROVED', 'CLIENT_REJECTED'],
                'EXECUTION': ['TRADES_EXECUTED', 'EXECUTION_PARTIAL'],
                'CONFIRMATION': ['TRADES_CONFIRMED', 'CONFIRMATION_PENDING'],
                'UPDATE': ['PORTFOLIO_UPDATED', 'HOLDINGS_UPDATED']
            },
            'transaction': {
                'REQUEST': ['REQUEST_RECEIVED', 'REQUEST_VALIDATED', 'REQUEST_REJECTED'],
                'VALIDATION': ['VALIDATION_PASSED', 'VALIDATION_FAILED'],
                'APPROVAL': ['APPROVAL_GRANTED', 'APPROVAL_DENIED'],
                'EXECUTION': ['EXECUTION_STARTED', 'EXECUTION_COMPLETED', 'EXECUTION_FAILED'],
                'CONFIRMATION': ['CONFIRMATION_SENT', 'CONFIRMATION_RECEIVED'],
                'SETTLEMENT': ['SETTLEMENT_INITIATED', 'SETTLEMENT_COMPLETED']
            }
        }
        
        return actions.get(module_type, {}).get(current_stage, [])
    
    @classmethod
    def cancel_workflow(cls, workflow_id, reason=None):
        """Cancel a workflow"""
        workflow = GenericWorkflow.query.get_or_404(workflow_id)
        workflow.status = 'cancelled'
        workflow.actual_completion_date = datetime.now().date()
        
        db.session.commit()
        
        notes = f"Workflow cancelled"
        if reason:
            notes += f" - {reason}"
        
        cls.add_action(workflow_id, 'WORKFLOW_CANCELLED', notes)
        return workflow
    
    @classmethod
    def pause_workflow(cls, workflow_id, reason=None):
        """Pause a workflow"""
        workflow = GenericWorkflow.query.get_or_404(workflow_id)
        workflow.status = 'paused'
        
        db.session.commit()
        
        notes = f"Workflow paused"
        if reason:
            notes += f" - {reason}"
        
        cls.add_action(workflow_id, 'WORKFLOW_PAUSED', notes)
        return workflow
    
    @classmethod
    def resume_workflow(cls, workflow_id, reason=None):
        """Resume a paused workflow"""
        workflow = GenericWorkflow.query.get_or_404(workflow_id)
        workflow.status = 'active'
        
        db.session.commit()
        
        notes = f"Workflow resumed"
        if reason:
            notes += f" - {reason}"
        
        cls.add_action(workflow_id, 'WORKFLOW_RESUMED', notes)
        return workflow

    @classmethod
    def get_stages(cls, module_type):
        """Get all stages for a module type"""
        return cls.WORKFLOW_STAGES.get(module_type, [])

    @classmethod
    def workflow_progress_percent(cls, module_type: str, current_stage: str, workflow_status: str = None) -> int:
        """
        Rough pipeline progress from current stage index vs defined stages (0–100).
        Uses WORKFLOW_STAGES order; unknown stages return 0.
        """
        if (workflow_status or "").lower() == "completed" or current_stage == "COMPLETED":
            return 100
        stages = cls.WORKFLOW_STAGES.get(module_type) or []
        if not stages:
            return 0
        try:
            idx = stages.index(current_stage)
        except ValueError:
            return 0
        return int(round((idx + 1) * 100.0 / len(stages)))

    @classmethod
    def get_previous_stage(cls, module_type, current_stage):
        """Get the previous stage in the workflow"""
        if module_type not in cls.WORKFLOW_STAGES:
            return None
        stages = cls.WORKFLOW_STAGES[module_type]
        try:
            current_index = stages.index(current_stage)
            if current_index > 0:
                return stages[current_index - 1]
        except ValueError:
            pass
        return None 