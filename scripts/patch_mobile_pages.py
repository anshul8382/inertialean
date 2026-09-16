#!/usr/bin/env python3
"""Insert dual-layout mobile wrappers into key templates (idempotent)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, old: str, new: str) -> bool:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if "im-mobile-only" in text and "im-desktop-only" in text:
        return False
    if old not in text:
        print(f"  SKIP (pattern missing): {path}")
        return False
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  PATCHED: {path}")
    return True


def main() -> None:
    patches = [
        (
            "templates/clients/client_details.html",
            "{% block content %}\n<div class=\"container mt-4\">",
            '{% block content %}\n{% include "partials/mobile/client_detail_mobile.html" %}\n'
            '<div class="im-desktop-only container mt-4" id="imDesktopDetailTabs">',
        ),
        (
            "templates/clients/client_details.html",
            "{% endblock %}\n\n{% block scripts %}",
            "</div>\n{% endblock %}\n\n{% block scripts %}",
        ),
        (
            "templates/monthly_investments.html",
            "{% block content %}\n<div class=\"container mt-4\">",
            '{% block content %}\n<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Monthly investments' %}"
            "{% set mobile_back_url = url_for('main.list_monthly_investments') %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<div class="im-mobile-search"><i class="fas fa-search"></i>'
            '<input type="search" data-im-mobile-filter="imMobileInvestmentsList" placeholder="Search workflows…"></div>'
            '<div class="im-list-stack" id="imMobileInvestmentsList">'
            "{% for investment in investments %}{% include 'partials/mobile/investment_card.html' %}{% endfor %}"
            "</div>"
            '<a href="{{ url_for(\'monthly_investments.new_monthly_investment\') }}" class="im-fab"><i class="fas fa-plus"></i></a>'
            "</div>"
            '<div class="im-desktop-only container mt-4">',
        ),
        (
            "templates/workflows/list.html",
            "{% block content %}\n<div class=\"container-fluid mt-4\">",
            '{% block content %}'
            '<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Workflows' %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<div class="im-list-stack">'
            "{% for item in workflow_data %}{% set workflow = item.workflow %}"
            '<a href="{{ url_for(\'workflows.view_workflow\', workflow_id=workflow.id) }}" class="im-list-card" data-im-search="{{ item.record_name|lower }}">'
            '<div class="im-list-card__header"><h2 class="im-list-card__title">{{ item.record_name }}</h2>'
            '<span class="im-badge im-badge--info">{{ workflow.current_stage }}</span></div>'
            '<p class="im-list-card__meta">{{ workflow.module_type|replace(\'_\', \' \')|title }}</p></a>'
            "{% endfor %}</div></div>"
            '<div class="im-desktop-only container-fluid mt-4">',
        ),
        (
            "templates/hub/dashboard.html",
            "{% block content %}\n<div class=\"container mt-4\">",
            '{% block content %}'
            '<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Intelligence Hub' %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<div class="im-list-stack">'
            "{% set hub_badges = '<span class=\"im-badge im-badge--warning\">' ~ open_issues_count ~ ' issues</span>' %}"
            "{% set hub_url = url_for('hub.operational_dashboard') %}{% set hub_title = 'Operational' %}{% set hub_desc = 'Issues, alerts, data integrity' %}{% set hub_icon = 'fas fa-cogs' %}{% include 'partials/mobile/hub_tile.html' %}"
            "{% set hub_badges = '' %}{% set hub_url = url_for('hub.investment_dashboard') %}{% set hub_title = 'Investment' %}{% set hub_desc = 'Performance, tax, capital gains' %}{% set hub_icon = 'fas fa-chart-line' %}{% include 'partials/mobile/hub_tile.html' %}"
            "{% set hub_url = url_for('hub.system_dashboard') %}{% set hub_title = 'System' %}{% set hub_desc = 'Task rules, Airflow, SLA' %}{% set hub_icon = 'fas fa-microchip' %}{% include 'partials/mobile/hub_tile.html' %}"
            "{% set hub_url = url_for('hub.business_dashboard') %}{% set hub_title = 'Business' %}{% set hub_desc = 'Analytics and referrals' %}{% set hub_icon = 'fas fa-briefcase' %}{% include 'partials/mobile/hub_tile.html' %}"
            "</div></div>"
            '<div class="im-desktop-only container mt-4">',
        ),
        (
            "templates/reviews/period_analysis_v2.html",
            "{% block content %}\n<div class=\"container-fluid mt-4\">",
            '{% block content %}'
            '<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Period Analysis' %}"
            "{% set mobile_back_url = url_for('main.maintenance_reviews') if has_endpoint('main.maintenance_reviews') else url_for('main.dashboard') %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<p class="im-mobile-banner"><i class="fas fa-chart-line"></i> Use controls below. Complex tables scroll horizontally.</p>'
            "</div>"
            '<div class="im-desktop-only container-fluid mt-4">',
        ),
        (
            "templates/unified_recommendations/index.html",
            "{% block content %}\n<div class=\"container mt-4\">",
            '{% block content %}'
            '<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Recommendations' %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<div class="im-list-stack">'
            "{% for session in recent_sessions|default([]) %}"
            '<a href="{{ url_for(\'unified_recommendations.review_recommendations\') }}?session_id={{ session.id }}" class="im-list-card">'
            '<div class="im-list-card__header"><h2 class="im-list-card__title">{{ session.client.name }}</h2>'
            '<span class="im-badge im-badge--muted">{{ session.status|default(\'draft\') }}</span></div>'
            '<p class="im-list-card__meta">₹{{ session.investment_amount|default(0) }}</p></a>'
            "{% endfor %}</div>"
            '<a href="{{ url_for(\'unified_recommendations.generate_unified_recommendations\') }}" class="im-fab"><i class="fas fa-plus"></i></a>'
            "</div>"
            '<div class="im-desktop-only container mt-4">',
        ),
        (
            "templates/settings/index.html",
            "{% block content %}\n<div class=\"container-fluid\">",
            '{% block content %}'
            '<div class="im-mobile-only im-mobile-page">'
            "{% set mobile_page_title = 'Settings' %}"
            '{% include "partials/mobile/app_header.html" %}'
            '<a href="{{ url_for(\'settings.cron_jobs\') }}" class="im-settings-link"><i class="fas fa-clock"></i><span>Cron jobs</span><i class="fas fa-chevron-right"></i></a>'
            '<a href="{{ url_for(\'settings.system_info\') }}" class="im-settings-link"><i class="fas fa-server"></i><span>System info</span><i class="fas fa-chevron-right"></i></a>'
            '<a href="{{ url_for(\'settings.service_readiness\') }}" class="im-settings-link"><i class="fas fa-heartbeat"></i><span>Service readiness</span><i class="fas fa-chevron-right"></i></a>'
            "</div>"
            '<div class="im-desktop-only container-fluid">',
        ),
        (
            "templates/auth/login.html",
            "{% block content %}\n<div class=\"container mt-5\">",
            '{% block content %}\n<div class="container mt-5 im-auth-mobile">',
        ),
        (
            "templates/login.html",
            "{% block content %}\n<div class=\"container\">",
            '{% block content %}\n<div class="container im-auth-mobile">',
        ),
        (
            "templates/two_factor/verify.html",
            "{% block content %}",
            '{% block content %}\n<div class="im-auth-mobile">',
        ),
    ]

    close_patches = [
        ("templates/monthly_investments.html", "{% endblock %}", "</div>\n{% endblock %}", "block scripts"),
        ("templates/workflows/list.html", "{% endblock %}", "</div>\n{% endblock %}", "block scripts"),
        ("templates/hub/dashboard.html", "{% endblock %}", "</div>\n{% endblock %}", None),
        ("templates/reviews/period_analysis_v2.html", "{% endblock %}", "</div>\n{% endblock %}", "block extra"),
        ("templates/unified_recommendations/index.html", "{% endblock %}", "</div>\n{% endblock %}", "block scripts"),
        ("templates/settings/index.html", "{% endblock %}", "</div>\n{% endblock %}", None),
        ("templates/two_factor/verify.html", "{% endblock %}", "</div>\n{% endblock %}", None),
    ]

    print("Opening patches...")
    for path, old, new in patches:
        p = ROOT / path
        t = p.read_text(encoding="utf-8")
        if "im-desktop-only" in t:
            print(f"  already dual: {path}")
            continue
        if old not in t:
            print(f"  SKIP: {path}")
            continue
        p.write_text(t.replace(old, new, 1), encoding="utf-8")
        print(f"  PATCHED: {path}")

    print("Closing patches...")
    for path, marker, close, before in close_patches:
        p = ROOT / path
        t = p.read_text(encoding="utf-8")
        if "im-desktop-only" not in t:
            continue
        if t.count("im-desktop-only") > 0 and "</div>\n{% endblock %}\n\n{% block" in t:
            print(f"  already closed: {path}")
            continue
        if before:
            needle = f"</div>\n{{% endblock %}}\n\n{{% {before}"
        else:
            needle = f"</div>\n{{% endblock %}}"
        # Find last content endblock before scripts
        idx = t.rfind("{% endblock %}")
        if idx < 0:
            continue
        # Insert closing div before last endblock of content - fragile
        if "im-mobile-only" in t and t.rfind("</div>\n{% endblock %}") < t.find("im-desktop-only"):
            # insert before first {% block scripts %} or last endblock in content
            if before and f"{{% {before}" in t:
                t = t.replace(f"\n{{% {before}", f"\n</div>\n{{% {before}", 1)
            elif "{% block scripts %}" in t:
                t = t.replace("\n{% block scripts %}", "\n</div>\n{% block scripts %}", 1)
            else:
                t = t.replace("\n{% endblock %}", "\n</div>\n{% endblock %}", 1)
            p.write_text(t, encoding="utf-8")
            print(f"  CLOSED: {path}")


if __name__ == "__main__":
    main()
