from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired
from wtforms import (
    StringField,
    PasswordField,
    BooleanField,
    SubmitField,
    SelectField,
    DecimalField,
    DateField,
    TextAreaField,
    IntegerField,
    FloatField,
    FileField,
    SelectMultipleField,
    DateTimeField,
)
from wtforms.validators import DataRequired, Email, Length, EqualTo, NumberRange, Optional, ValidationError
from models import Client, Security, AssetClass, Portfolio, Lead, Workflow, User, Role
from access_control import get_accessible_clients_ordered


def _form_client_choices(*, active_only=False):
    clients = get_accessible_clients_ordered()
    if active_only:
        clients = [c for c in clients if getattr(c, "is_active", True)]
    return [(c.id, c.name) for c in clients]


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Reset Password')

class RegistrationForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

class ProfileForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    current_password = PasswordField('Current Password', validators=[Optional()])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[Optional(), EqualTo('new_password')])
    submit = SubmitField('Update Profile')

class AddUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    is_admin = BooleanField('Admin User')
    is_active = BooleanField('Active User', default=True)
    submit = SubmitField('Add User')

class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    is_admin = BooleanField('Admin User')
    is_active = BooleanField('Active User')
    role_id = SelectField('Role', coerce=int, validators=[Optional()])
    submit = SubmitField('Update User')
    
    def __init__(self, *args, **kwargs):
        super(EditUserForm, self).__init__(*args, **kwargs)
        # Populate role choices
        roles = Role.query.filter_by(is_active=True).order_by(Role.name).all()
        self.role_id.choices = [(0, 'None (Use Legacy Role)')] + [(role.id, role.display_name) for role in roles]

class PasswordResetRequestForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class PasswordResetForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')

class SetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Set Password')

class LeadForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    contact_number = StringField('Contact Number', validators=[DataRequired(), Length(min=10, max=15)])
    reference_source = StringField('Reference Source', validators=[DataRequired()])
    temperature = SelectField('Temperature', choices=[
        ('HOT', 'Hot'),
        ('WARM', 'Warm'),
        ('COLD', 'Cold')
    ], validators=[DataRequired()])
    lead_type = SelectField('Lead Type', choices=[
        ('INDIVIDUAL', 'Individual'),
        ('CORPORATE', 'Corporate')
    ], validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('proposal_sent', 'Proposal Sent'),
        ('proposal_reviewed', 'Proposal Reviewed'),
        ('proposal_revised', 'Proposal Revised'),
        ('agreement_sent', 'Agreement Sent'),
        ('agreement_reviewed', 'Agreement Reviewed'),
        ('agreement_signed', 'Agreement Signed'),
        ('onboarding_started', 'Onboarding Started'),
        ('onboarding_completed', 'Onboarding Completed'),
        ('dropped', 'Dropped')
    ], validators=[DataRequired()])
    submit = SubmitField('Add Lead')

class ClientForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    secondary_emails = TextAreaField(
        'Secondary Emails (CC)',
        validators=[Optional()],
        render_kw={
            'rows': 2,
            'placeholder': 'Optional: spouse@example.com, assistant@example.com',
        },
    )
    # Keep within client.phone / client.address column sizes (VARCHAR 20 / 200)
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    address = TextAreaField('Address', validators=[Optional(), Length(max=200)])
    risk_profile = SelectField('Risk Profile', choices=[
        ('conservative', 'Conservative'),
        ('moderate', 'Moderate'),
        ('aggressive', 'Aggressive')
    ])
    risk_profile_updated_at = StringField('Risk Profile Last Updated', render_kw={'readonly': True})
    background_notes = TextAreaField(
        'Add Background Note',
        validators=[Optional()],
        render_kw={'rows': 3, 'placeholder': 'Add a new background note (saved with date and author)...'},
    )
    other_notes = TextAreaField(
        'Add Other Note',
        validators=[Optional()],
        render_kw={'rows': 3, 'placeholder': 'Add a new note (saved with date and author)...'},
    )
    date_of_birth = DateField('Date of Birth', validators=[Optional()])
    starting_aua = DecimalField('Starting AUA', validators=[Optional(), NumberRange(min=0)])
    designation = StringField('Designation', validators=[Optional(), Length(max=100)])
    linkedin_profile_url = StringField('LinkedIn Profile URL', validators=[Optional(), Length(max=500)])
    date_of_joining = DateField('Date of Joining', validators=[Optional()])
    type_of_engagement = StringField('Type of Engagement', validators=[Optional(), Length(max=200)])
    portfolio_inherited = BooleanField('Portfolio Inherited', default=False)
    company_name = StringField('Company Name', validators=[Optional(), Length(max=200)])
    industry = StringField('Industry', validators=[Optional(), Length(max=100)])
    planning_synopsis = TextAreaField(
        'Add Investment Plan Note',
        validators=[Optional()],
        render_kw={'rows': 4, 'placeholder': 'Add a new planning note (saved with date and author)...'},
    )
    is_active = BooleanField('Active Client', default=True)
    submit = SubmitField('Save Client')

class PortfolioForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    description = TextAreaField('Description')
    submit = SubmitField('Save Portfolio')

    def __init__(self, *args, **kwargs):
        super(PortfolioForm, self).__init__(*args, **kwargs)
        self.client_id.choices = _form_client_choices(active_only=True)

class TransactionForm(FlaskForm):
    portfolio_id = SelectField('Portfolio', coerce=int, validators=[DataRequired()])
    security_id = SelectField('Security', coerce=int, validators=[DataRequired()])
    type = SelectField('Type', choices=[
        ('buy', 'Buy'),
        ('sell', 'Sell')
    ], validators=[DataRequired()])
    quantity = DecimalField('Quantity', validators=[DataRequired(), NumberRange(min=0)])
    price = DecimalField('Price', validators=[Optional(), NumberRange(min=0)])
    transaction_date = DateField('Transaction Date', validators=[DataRequired()])
    notes = TextAreaField('Notes')
    submit = SubmitField('Save Transaction')

    def __init__(self, *args, **kwargs):
        super(TransactionForm, self).__init__(*args, **kwargs)
        self.portfolio_id.choices = [(p.id, p.name) for p in Portfolio.query.order_by(Portfolio.name).all()]
        self.security_id.choices = [(s.id, f"{s.symbol} - {s.name}") for s in Security.query.order_by(Security.symbol).all()]

class MonthlyInvestmentForm(FlaskForm):
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    planned_amount = DecimalField('Planned Amount', validators=[DataRequired()])  # Removed min=0 to allow negative/zero
    investment_date = DateField('Investment Date', validators=[DataRequired()])
    change_frequency_months = IntegerField('Portfolio Change Frequency (months)', validators=[Optional()], default=1, description="How often portfolio changes occur (1=monthly, 2=every 2 months, etc.)")
    submit = SubmitField('Create Monthly Investment')

    def __init__(self, *args, **kwargs):
        super(MonthlyInvestmentForm, self).__init__(*args, **kwargs)
        self.client_id.choices = _form_client_choices(active_only=True)

class AssetModelForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    description = TextAreaField('Description')
    risk_level = SelectField('Risk Level', choices=[
        ('1', 'Very Conservative'),
        ('2', 'Conservative'),
        ('3', 'Moderate'),
        ('4', 'Aggressive'),
        ('5', 'Very Aggressive')
    ], validators=[DataRequired()])
    asset_class_id = SelectField('Asset Class', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Save Asset Model')

    def __init__(self, *args, **kwargs):
        super(AssetModelForm, self).__init__(*args, **kwargs)
        self.asset_class_id.choices = [(ac.id, ac.name) for ac in AssetClass.query.order_by(AssetClass.name).all()]

class SecurityModelForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    description = TextAreaField('Description')
    risk_level = SelectField('Risk Level', choices=[
        ('1', 'Very Conservative'),
        ('2', 'Conservative'),
        ('3', 'Moderate'),
        ('4', 'Aggressive'),
        ('5', 'Very Aggressive')
    ], validators=[DataRequired()])
    asset_class_id = SelectField('Asset Class', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Save Security Model')

    def __init__(self, *args, **kwargs):
        super(SecurityModelForm, self).__init__(*args, **kwargs)
        self.asset_class_id.choices = [(ac.id, ac.name) for ac in AssetClass.query.order_by(AssetClass.name).all()]

class SecurityForm(FlaskForm):
    symbol = StringField('Symbol', validators=[DataRequired(), Length(min=1, max=20)])
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    type = SelectField('Type', choices=[
        ('STOCK', 'Stock'),
        ('ETF', 'ETF'),
        ('MUTUAL_FUND', 'Mutual Fund'),
        ('BOND', 'Bond'),
        ('OTHER', 'Other')
    ], validators=[DataRequired()])
    current_price = DecimalField('Current Price', validators=[Optional(), NumberRange(min=0)])
    asset_class_id = SelectField('Asset Class', coerce=int, validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional()])
    submit = SubmitField('Save Security')

    def __init__(self, *args, **kwargs):
        super(SecurityForm, self).__init__(*args, **kwargs)
        self.asset_class_id.choices = [(ac.id, ac.name) for ac in AssetClass.query.order_by(AssetClass.name).all()]
        
        # Map security.security_type to form.type if security object is provided
        if 'obj' in kwargs and kwargs['obj']:
            security = kwargs['obj']
            if hasattr(security, 'security_type') and security.security_type:
                # Map database value to form choice
                type_mapping = {
                    'STOCK': 'STOCK',
                    'ETF': 'ETF',
                    'MUTUAL_FUND': 'MUTUAL_FUND',
                    'BOND': 'BOND',
                    'OTHER': 'OTHER',
                    'stock': 'STOCK',
                    'etf': 'ETF',
                    'mutual_fund': 'MUTUAL_FUND',
                    'bond': 'BOND',
                    'other': 'OTHER'
                }
                mapped_type = type_mapping.get(security.security_type, security.security_type.upper() if security.security_type else 'STOCK')
                if mapped_type in [choice[0] for choice in self.type.choices]:
                    self.type.data = mapped_type
            
            # Map meta_data to description if available
            if hasattr(security, 'meta_data') and security.meta_data:
                self.description.data = security.meta_data

class AssetClassForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2, max=100)])
    description = TextAreaField('Description')
    tax_ruleset = StringField(
        'Tax ruleset code',
        validators=[DataRequired(), Length(max=32)],
        default='equity',
    )
    tax_stcg_rate = FloatField(
        'STCG rate (0–1, e.g. 0.20 for 20%)',
        validators=[DataRequired(), NumberRange(min=0, max=1)],
        default=0.20,
    )
    tax_ltcg_rate = FloatField(
        'LTCG rate (0–1, e.g. 0.125 for 12.5%)',
        validators=[DataRequired(), NumberRange(min=0, max=1)],
        default=0.125,
    )
    tax_ltcg_exemption_limit = FloatField(
        'LTCG exemption limit (₹)',
        validators=[DataRequired(), NumberRange(min=0)],
        default=125000,
    )
    tax_ltcg_minimum_months = IntegerField(
        'LTCG minimum months',
        validators=[DataRequired(), NumberRange(min=1, max=600)],
        default=12,
    )
    tax_ltcg_eligibility_mode = SelectField(
        'LTCG eligibility mode',
        choices=[
            ('', 'Inherit global'),
            ('twelve_months', '12 calendar months'),
            ('holding_days', 'Minimum holding days'),
        ],
        validators=[Optional()],
    )
    tax_ltcg_holding_days = IntegerField(
        'LTCG minimum days (if mode is holding days)',
        validators=[Optional(), NumberRange(min=1, max=3660)],
    )
    tax_rules_active = BooleanField('Tax rules active', default=True)
    tax_notes = TextAreaField('Tax notes', validators=[Optional()])
    submit = SubmitField('Save Asset Class')

class CallLogForm(FlaskForm):
    lead_id = SelectField('Lead', coerce=int, validators=[DataRequired()])
    date = DateField('Date', validators=[DataRequired()])
    notes = TextAreaField('Notes', validators=[DataRequired()])
    outcome = SelectField('Outcome', choices=[
        ('scheduled', 'Meeting Scheduled'),
        ('callback', 'Callback Requested'),
        ('uninterested', 'Not Interested'),
        ('other', 'Other')
    ], validators=[DataRequired()])
    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super(CallLogForm, self).__init__(*args, **kwargs)
        self.lead_id.choices = [(l.id, l.name) for l in Lead.query.order_by(Lead.name).all()]

class DocumentForm(FlaskForm):
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    name = StringField('Name', validators=[DataRequired()])
    type = SelectField('Type', choices=[
        ('contract', 'Contract'),
        ('proposal', 'Proposal'),
        ('report', 'Report'),
        ('other', 'Other')
    ], validators=[DataRequired()])
    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super(DocumentForm, self).__init__(*args, **kwargs)
        self.client_id.choices = _form_client_choices(active_only=True)

class CashflowForm(FlaskForm):
    client_id = SelectField('Client', coerce=int, validators=[DataRequired()])
    amount = DecimalField('Amount', validators=[DataRequired()])  # Removed min=0 to allow negative values
    type = SelectField('Type', choices=[
        ('INFLOW', 'Investment (Money In)'),
        ('OUTFLOW', 'Withdrawal (Money Out)')
    ], validators=[DataRequired()])
    date = DateField('Date', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional()])
    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super(CashflowForm, self).__init__(*args, **kwargs)
        from sqlalchemy import func

        self.client_id.choices = _form_client_choices()

class WorkflowForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    submit = SubmitField('Submit')

class WorkflowActionForm(FlaskForm):
    workflow_id = SelectField('Workflow', coerce=int, validators=[DataRequired()])
    name = StringField('Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    order = IntegerField('Order', validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super(WorkflowActionForm, self).__init__(*args, **kwargs)
        self.workflow_id.choices = [(w.id, w.name) for w in Workflow.query.order_by(Workflow.name).all()]

class SecuritiesCSVUploadForm(FlaskForm):
    file = FileField('CSV File', validators=[FileRequired(), FileAllowed(['csv'], 'CSV only')])
    has_header = BooleanField('File has header row', default=True)


class AgreementTemplateForm(FlaskForm):
    name = StringField('Template Name', validators=[DataRequired(), Length(min=2, max=200)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)])
    template_file = FileField('Template File', validators=[
        Optional(),
        FileAllowed(['doc', 'docx', 'pdf', 'html', 'txt'], 'Upload a .doc, .docx, .pdf, .html, or .txt file'),
    ])
    template_type = SelectField('Template Type', choices=[
        ('doc', 'Microsoft Word (.doc/.docx)'),
        ('pdf', 'PDF Document'),
        ('html', 'HTML Template')
    ], validators=[DataRequired()])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Template')

MONTH_CHOICES = [
    ('1', 'January'), ('2', 'February'), ('3', 'March'), ('4', 'April'),
    ('5', 'May'), ('6', 'June'), ('7', 'July'), ('8', 'August'),
    ('9', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December'),
]

BILLING_FREQUENCY_CHOICES = [
    ('yearly',      'Yearly (once a year)'),
    ('half_yearly', 'Half-yearly (twice a year)'),
    ('quarterly',   'Quarterly (four times a year)'),
]

VALUATION_DATE_RULE_CHOICES = [
    (
        'prepaid',
        'Pre-paid — AUA as at period start value (e.g. 31 Dec for Jan–Jun half-year)',
    ),
    (
        'postpaid',
        'Post-paid — AUA as at period end value (e.g. 30 Jun for Jan–Jun half-year)',
    ),
]

class AgreementForm(FlaskForm):
    template_id = SelectField('Template', coerce=int, validators=[DataRequired()])
    asset_types = SelectMultipleField('Asset Types', coerce=int, validators=[DataRequired()])
    advisory_model = SelectField('Advisory Model', choices=[
        ('aua', 'Assets under Advice (AUA) mode'),
        ('fixed_fee', 'Fixed Fee mode'),
        ('fixed_then_aua', 'Fixed fee — first year, then AUA'),
    ], validators=[DataRequired()])
    first_year_fixed_annual_fee = DecimalField(
        'First-year fixed fee (₹/year)',
        validators=[Optional()],
        places=2,
    )
    first_year_billing_frequency = SelectField(
        'First-year invoice frequency',
        choices=BILLING_FREQUENCY_CHOICES,
        default='yearly',
        validators=[Optional()],
    )
    billing_frequency = SelectField(
        'Billing frequency (from year 2 onwards for fixed-then-AUA)',
        choices=BILLING_FREQUENCY_CHOICES,
        default='yearly',
        validators=[DataRequired()],
    )
    billing_start_date = DateField(
        'Billing start date',
        validators=[DataRequired()],
        format='%Y-%m-%d',
    )
    period_start_month = SelectField(
        'Billing period start month',
        choices=MONTH_CHOICES,
        default='1',
        validators=[DataRequired()],
    )
    valuation_date_rule = SelectField(
        'AUA billing basis',
        choices=VALUATION_DATE_RULE_CHOICES,
        default='prepaid',
        validators=[DataRequired()],
    )
    special_note = TextAreaField('Special Note', default='The first invoice will be for ₹10,000 for one year. From next year, the billing will be based on AUA.')
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Generate Agreement')

    def __init__(self, *args, **kwargs):
        super(AgreementForm, self).__init__(*args, **kwargs)
        # Populate asset types from database
        from models import AssetClass
        asset_classes = AssetClass.query.order_by(AssetClass.name).all()
        self.asset_types.choices = [(ac.id, ac.name) for ac in asset_classes]

class RecordExistingAgreementForm(FlaskForm):
    """Form for recording an existing agreement that's already signed"""
    asset_types = SelectMultipleField('Asset Types', coerce=int, validators=[DataRequired()])
    advisory_model = SelectField('Advisory Model', choices=[
        ('aua', 'Assets under Advice (AUA) mode'),
        ('fixed_fee', 'Fixed Fee mode'),
        ('fixed_then_aua', 'Fixed fee — first year, then AUA'),
    ], validators=[DataRequired()])
    first_year_fixed_annual_fee = DecimalField(
        'First-year fixed fee (₹/year)',
        validators=[Optional()],
        places=2,
    )
    first_year_billing_frequency = SelectField(
        'First-year invoice frequency',
        choices=BILLING_FREQUENCY_CHOICES,
        default='yearly',
        validators=[Optional()],
    )
    billing_frequency = SelectField(
        'Billing frequency (from year 2 onwards for fixed-then-AUA)',
        choices=BILLING_FREQUENCY_CHOICES,
        default='yearly',
        validators=[DataRequired()],
    )
    billing_start_date = DateField(
        'Billing start date',
        validators=[Optional()],
        format='%Y-%m-%d',
    )
    period_start_month = SelectField(
        'Billing period start month',
        choices=MONTH_CHOICES,
        default='1',
        validators=[DataRequired()],
    )
    valuation_date_rule = SelectField(
        'AUA billing basis',
        choices=VALUATION_DATE_RULE_CHOICES,
        default='prepaid',
        validators=[DataRequired()],
    )
    special_note = TextAreaField('Special Note', default='The first invoice will be for ₹10,000 for one year. From next year, the billing will be based on AUA.')
    agreement_pdf = FileField('Upload Agreement PDF', validators=[DataRequired()])
    status = SelectField('Agreement Status', choices=[
        ('signed', 'Signed'),
        ('completed', 'Completed')
    ], validators=[DataRequired()], default='signed')
    sent_date = DateField('Sent Date', validators=[Optional()], format='%Y-%m-%d')
    signed_date = DateField('Signed Date', validators=[DataRequired()], format='%Y-%m-%d')
    pan = StringField(
        'PAN (client)',
        validators=[DataRequired(), Length(min=10, max=10)],
        filters=[lambda x: (x or '').strip().upper() or None],
    )
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Record Agreement')

    def validate_pan(self, field):
        import re
        if field.data and not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', field.data):
            raise ValidationError('Enter a valid PAN (e.g. ABCDE1234F).')

    def __init__(self, *args, **kwargs):
        super(RecordExistingAgreementForm, self).__init__(*args, **kwargs)
        # Populate asset types from database
        from models import AssetClass
        asset_classes = AssetClass.query.order_by(AssetClass.name).all()
        self.asset_types.choices = [(ac.id, ac.name) for ac in asset_classes]

class AgreementVariableForm(FlaskForm):
    """Dynamic form for agreement variables - will be created dynamically based on template variables"""
    submit = SubmitField('Generate PDF')

class ReviewScheduleForm(FlaskForm):
    first_review_date = DateField('First Review Date', validators=[DataRequired()], format='%Y-%m-%d')
    frequency = SelectField('Review Frequency', choices=[
        ('half_yearly', 'Half Yearly (6 months)'),
        ('yearly', 'Yearly (12 months)')
    ], validators=[DataRequired()])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Create Review Schedule')

class ReviewWorkflowForm(FlaskForm):
    review_date = DateField('Review Date', validators=[DataRequired()], format='%Y-%m-%d')
    status = SelectField('Status', choices=[
        ('initiated', 'Initiated'),
        ('sent', 'Review Sent'),
        ('meeting', 'Meeting Scheduled'),
        ('closed', 'Closed')
    ], validators=[DataRequired()])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=1000)])
    meeting_date = DateTimeField('Meeting Date', validators=[Optional()], format='%Y-%m-%dT%H:%M')
    meeting_notes = TextAreaField('Meeting Notes', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Update Workflow')

class ServiceTicketForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description', validators=[DataRequired()])
    alert_type = SelectField('Alert Type', choices=[
        ('workflow_sla', 'Workflow SLA'),
        ('recommendation', 'Recommendation'),
        ('communication', 'Communication'),
        ('portfolio', 'Portfolio'),
        ('system', 'System'),
        ('billing', 'Billing'),
        ('technical', 'Technical Support'),
        ('account', 'Account Issue'),
        ('other', 'Other')
    ], validators=[DataRequired()])
    priority = SelectField('Priority', choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], validators=[DataRequired()], default='medium')
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])
    submit = SubmitField('Create Ticket')

class ServiceTicketUpdateForm(FlaskForm):
    status = SelectField('Status', choices=[
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('snoozed', 'Snoozed'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled')
    ], validators=[DataRequired()])
    priority = SelectField('Priority', choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], validators=[DataRequired()])
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])
    resolution_notes = TextAreaField('Resolution Notes', validators=[Optional()])
    submit = SubmitField('Update Ticket') 


class ServiceTicketSnoozeForm(FlaskForm):
    """Snooze a service ticket until a specific datetime."""
    snoozed_until = DateTimeField(
        'Snooze Until',
        validators=[DataRequired()],
        format='%Y-%m-%dT%H:%M',
        render_kw={'type': 'datetime-local'}
    )
    snooze_reason = TextAreaField('Reason', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Snooze Ticket')


class ServiceTicketUnsnoozeForm(FlaskForm):
    """Unsnooze a service ticket immediately."""
    submit = SubmitField('Unsnooze')


class OpsTaskForm(FlaskForm):
    name = StringField('Task Name', validators=[DataRequired(), Length(max=200)])
    deadline = DateTimeField('Deadline', validators=[DataRequired()], format='%Y-%m-%dT%H:%M', render_kw={'type': 'datetime-local'})
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=5000)])
    priority = SelectField('Priority', choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High')
    ], default='medium')
    status = SelectField('Status', choices=[
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ], default='pending')
    client_id = SelectField('Related Client', coerce=int, validators=[Optional()])
    submit = SubmitField('Save Task')


class OpsTaskSnoozeForm(FlaskForm):
    snoozed_until = DateTimeField(
        'Snooze Until',
        validators=[DataRequired()],
        format='%Y-%m-%dT%H:%M',
        render_kw={'type': 'datetime-local'}
    )
    snooze_reason = TextAreaField('Reason', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Snooze Task')


class OpsTaskReminderForm(FlaskForm):
    reminder_at = DateTimeField(
        'Reminder At',
        validators=[DataRequired()],
        format='%Y-%m-%dT%H:%M',
        render_kw={'type': 'datetime-local'}
    )
    submit = SubmitField('Set Reminder')