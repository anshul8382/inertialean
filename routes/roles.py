"""
Routes for role and permission management
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Role, RolePermission, User
from utils.permissions import get_all_routes
from functools import wraps
import logging

logger = logging.getLogger(__name__)
roles = Blueprint('roles', __name__)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

@roles.route('/')
@login_required
@admin_required
def list_roles():
    """List all roles"""
    roles_list = Role.query.order_by(Role.name).all()
    return render_template('roles/list.html', roles=roles_list)

@roles.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_role():
    """Add a new role"""
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            display_name = request.form.get('display_name', '').strip()
            description = request.form.get('description', '').strip()
            parent_role_id = request.form.get('parent_role_id') or None
            is_system_role = request.form.get('is_system_role') == 'on'
            
            if not name or not display_name:
                flash('Name and display name are required.', 'error')
                return redirect(url_for('roles.add_role'))
            
            # Check if role name already exists
            existing = Role.query.filter_by(name=name).first()
            if existing:
                flash('A role with this name already exists.', 'error')
                return redirect(url_for('roles.add_role'))
            
            # Validate parent role if provided
            if parent_role_id:
                parent_role = Role.query.get(parent_role_id)
                if not parent_role:
                    flash('Invalid parent role selected.', 'error')
                    return redirect(url_for('roles.add_role'))
            
            new_role = Role(
                name=name,
                display_name=display_name,
                description=description,
                parent_role_id=int(parent_role_id) if parent_role_id else None,
                is_system_role=is_system_role
            )
            db.session.add(new_role)
            db.session.commit()
            
            flash('Role created successfully.', 'success')
            return redirect(url_for('roles.edit_role_permissions', role_id=new_role.id))
        except Exception as e:
            logger.error(f"Error adding role: {str(e)}")
            db.session.rollback()
            flash(f'Error creating role: {str(e)}', 'error')
            return redirect(url_for('roles.add_role'))
    
    # GET request - show form
    all_roles = Role.query.filter_by(is_active=True).order_by(Role.name).all()
    return render_template('roles/add.html', roles=all_roles)

@roles.route('/<int:role_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_role(role_id):
    """Edit a role"""
    role = Role.query.get_or_404(role_id)
    
    if request.method == 'POST':
        try:
            role.display_name = request.form.get('display_name', '').strip()
            role.description = request.form.get('description', '').strip()
            parent_role_id = request.form.get('parent_role_id') or None
            
            # Validate parent role (can't be self or create circular dependency)
            if parent_role_id:
                parent_role = Role.query.get(parent_role_id)
                if not parent_role:
                    flash('Invalid parent role selected.', 'error')
                    return redirect(url_for('roles.edit_role', role_id=role_id))
                
                # Check for circular dependency
                if parent_role.id == role.id:
                    flash('A role cannot be its own parent.', 'error')
                    return redirect(url_for('roles.edit_role', role_id=role_id))
                
                # Check if parent is a child of this role (circular)
                def is_descendant(parent, child):
                    if parent.parent_role_id == child.id:
                        return True
                    if parent.parent_role_id:
                        return is_descendant(Role.query.get(parent.parent_role_id), child)
                    return False
                
                if is_descendant(parent_role, role):
                    flash('Cannot create circular dependency in role hierarchy.', 'error')
                    return redirect(url_for('roles.edit_role', role_id=role_id))
                
                role.parent_role_id = int(parent_role_id)
            else:
                role.parent_role_id = None
            
            role.is_active = request.form.get('is_active') == 'on'
            db.session.commit()
            
            flash('Role updated successfully.', 'success')
            return redirect(url_for('roles.list_roles'))
        except Exception as e:
            logger.error(f"Error editing role: {str(e)}")
            db.session.rollback()
            flash(f'Error updating role: {str(e)}', 'error')
            return redirect(url_for('roles.edit_role', role_id=role_id))
    
    # GET request - show form
    all_roles = Role.query.filter(Role.id != role_id, Role.is_active == True).order_by(Role.name).all()
    return render_template('roles/edit.html', role=role, roles=all_roles)

@roles.route('/<int:role_id>/permissions', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_role_permissions(role_id):
    """Edit permissions for a role"""
    role = Role.query.get_or_404(role_id)
    
    if request.method == 'POST':
        try:
            # Get all route endpoints from form
            all_routes = get_all_routes()
            route_endpoints = []
            for category, routes in all_routes.items():
                for endpoint, _ in routes:
                    route_endpoints.append(endpoint)
            
            # Get selected permissions from form
            selected_routes = request.form.getlist('permissions')
            
            # Delete existing permissions
            RolePermission.query.filter_by(role_id=role_id).delete()
            
            # Add new permissions
            for route in selected_routes:
                if route in route_endpoints:
                    permission = RolePermission(
                        role_id=role_id,
                        route_endpoint=route,
                        has_access=True
                    )
                    db.session.add(permission)
            
            db.session.commit()
            flash('Permissions updated successfully.', 'success')
            return redirect(url_for('roles.list_roles'))
        except Exception as e:
            logger.error(f"Error updating permissions: {str(e)}")
            db.session.rollback()
            flash(f'Error updating permissions: {str(e)}', 'error')
            return redirect(url_for('roles.edit_role_permissions', role_id=role_id))
    
    # GET request - show permissions form
    all_routes = get_all_routes()
    current_permissions = {perm.route_endpoint: True for perm in role.permissions}
    inherited_permissions = role.get_all_permissions()
    
    return render_template('roles/permissions.html', 
                         role=role, 
                         all_routes=all_routes,
                         current_permissions=current_permissions,
                         inherited_permissions=inherited_permissions)

@roles.route('/<int:role_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_role(role_id):
    """Delete a role (soft delete by setting is_active=False)"""
    role = Role.query.get_or_404(role_id)
    
    if role.is_system_role:
        flash('System roles cannot be deleted.', 'error')
        return redirect(url_for('roles.list_roles'))
    
    # Check if any users are using this role
    users_with_role = User.query.filter_by(role_id=role_id).count()
    if users_with_role > 0:
        flash(f'Cannot delete role. {users_with_role} user(s) are assigned to this role.', 'error')
        return redirect(url_for('roles.list_roles'))
    
    try:
        role.is_active = False
        db.session.commit()
        flash('Role deactivated successfully.', 'success')
    except Exception as e:
        logger.error(f"Error deleting role: {str(e)}")
        db.session.rollback()
        flash(f'Error deactivating role: {str(e)}', 'error')
    
    return redirect(url_for('roles.list_roles'))






