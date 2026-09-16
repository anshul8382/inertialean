// Form validation
document.addEventListener('DOMContentLoaded', function() {
    // Login form validation
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', function(e) {
            const email = this.querySelector('input[name="email"]');
            const password = this.querySelector('input[name="password"]');
            let isValid = true;

            if (!email.value) {
                showError(email, 'Email is required');
                isValid = false;
            } else {
                clearError(email);
            }

            if (!password.value) {
                showError(password, 'Password is required');
                isValid = false;
            } else {
                clearError(password);
            }

            if (!isValid) {
                e.preventDefault();
            }
        });
    }
});

// Helper functions
function showError(input, message) {
    const formGroup = input.closest('.form-group');
    let errorDiv = formGroup.querySelector('.text-danger');
    
    if (!errorDiv) {
        errorDiv = document.createElement('div');
        errorDiv.className = 'text-danger';
        formGroup.appendChild(errorDiv);
    }
    
    errorDiv.textContent = message;
    input.classList.add('is-invalid');
}

function clearError(input) {
    const formGroup = input.closest('.form-group');
    const errorDiv = formGroup.querySelector('.text-danger');
    
    if (errorDiv) {
        errorDiv.remove();
    }
    
    input.classList.remove('is-invalid');
} 

// ---- Global fallback handlers (for legacy inline onclick usage) ----
// Some templates may still reference toggleSelectAll/onCheckboxChange inline.
// Define safe no-op aware globals so the page does not error and functionality works.
window.toggleSelectAll = function(checkbox) {
    try {
        const isChecked = checkbox && checkbox.checked;
        const clientCheckboxes = document.querySelectorAll('.client-checkbox:not([disabled])');
        clientCheckboxes.forEach(function(cb) { cb.checked = !!isChecked; });
        if (typeof window.updateApplyButton === 'function') {
            window.updateApplyButton();
        }
    } catch (e) {
        console.error('toggleSelectAll fallback error:', e);
    }
};

window.onCheckboxChange = function() {
    try {
        if (typeof window.updateApplyButton === 'function') {
            window.updateApplyButton();
        }
    } catch (e) {
        console.error('onCheckboxChange fallback error:', e);
    }
};

// Provide a global updater used by both the page script and fallbacks
window.updateApplyButton = window.updateApplyButton || function() {
    const applyBtn = document.getElementById('apply-btn');
    if (!applyBtn) return;
    const checked = document.querySelectorAll('.client-checkbox:checked').length;
    if (checked > 0) {
        applyBtn.disabled = false;
        applyBtn.innerHTML = '<i class="fas fa-play"></i> Apply to ' + checked + ' Clients';
    } else {
        applyBtn.disabled = true;
        applyBtn.innerHTML = '<i class="fas fa-play"></i> Apply Corporate Action';
    }
};