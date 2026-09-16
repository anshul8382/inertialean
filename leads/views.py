from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime
from . import leads
from .. import db
from ..models import Lead, Client
from ..forms import LeadForm

@leads.route('/')
@login_required
def list_leads():
    """List all leads"""
    leads = Lead.query.all()
    return render_template('leads/list.html', leads=leads)

@leads.route('/add', methods=['GET', 'POST'])
@login_required
def add_lead():
    """Add a new lead"""
    form = LeadForm()
    if form.validate_on_submit():
        new_lead = Lead(
            name=form.name.data,
            contact_number=form.contact_number.data,
            reference_source=form.reference_source.data,
            temperature=form.temperature.data,
            lead_type=form.lead_type.data,
            status='new'
        )
        db.session.add(new_lead)
        db.session.commit()
        flash('Lead added successfully!', 'success')
        return redirect(url_for('leads.list_leads'))
    return render_template('leads/add.html', form=form)

@leads.route('/edit/<int:lead_id>', methods=['GET', 'POST'])
@login_required
def edit_lead(lead_id):
    """Edit an existing lead"""
    lead = Lead.query.get_or_404(lead_id)
    form = LeadForm(obj=lead)
    if form.validate_on_submit():
        lead.name = form.name.data
        lead.contact_number = form.contact_number.data
        lead.reference_source = form.reference_source.data
        lead.temperature = form.temperature.data
        lead.lead_type = form.lead_type.data
        db.session.commit()
        flash('Lead updated successfully!', 'success')
        return redirect(url_for('leads.list_leads'))
    return render_template('leads/edit.html', form=form, lead=lead)

@leads.route('/delete/<int:lead_id>', methods=['POST'])
@login_required
def delete_lead(lead_id):
    """Delete a lead"""
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    flash('Lead deleted successfully!', 'success')
    return redirect(url_for('leads.list_leads'))

@leads.route('/update-status/<int:lead_id>', methods=['POST'])
@login_required
def update_lead_status(lead_id):
    """Update lead status and convert to client if onboarded"""
    lead = Lead.query.get_or_404(lead_id)
    new_status = request.form.get('status')
    
    if new_status == 'onboarded':
        # Create new client from lead data
        client = Client(
            name=lead.name,
            email=f"{lead.name.lower().replace(' ', '.')}@example.com",  # Generate temporary email
            phone=lead.contact_number,
            user_id=current_user.id,
            risk_profile='Moderate'  # Default risk profile
        )
        db.session.add(client)
        db.session.flush()  # Get the client ID without committing
        
        # Update lead status and link to client
        lead.status = new_status
        lead.client_id = client.id
        
        db.session.commit()
        flash('Lead converted to client successfully!', 'success')
        return redirect(url_for('clients.edit_client', client_id=client.id))
    else:
        lead.status = new_status
        db.session.commit()
        flash('Lead status updated successfully!', 'success')
        return redirect(url_for('leads.list_leads')) 