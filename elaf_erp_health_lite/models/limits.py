# Lite edition limits (PO decision 2026-11-21, docs/FREEMIUM_SPEC.md).
# These are plain Python constants on purpose: they are not editable
# configuration. They are not DRM either: anybody with SQL access can change
# local rows. Installing the Pro module (elaf_erp_health) lifts every limit by
# overriding ``elaf.health.edition._elaf_health_is_pro``.

# Single switch to disable the *cumulative* caps (completed scans) if the Odoo
# Apps store objects to usage-capped free apps. Functional limits (one scope
# per scan, one company, 10 checks) stay in place when this is False.
ELAF_USAGE_CAPS_ENABLED = True

LITE_MAX_COMPLETED_SCANS = 3
LITE_MAX_CHECKS = 10
USAGE_KEY_SCANS = 'completed_scans'

# The 10 checks shipped with Lite (one per line, two per business scope).
LITE_CHECK_CODES = (
    'sale_stale_quotation',
    'sale_below_cost',
    'purchase_late_receipt',
    'purchase_stale_rfq',
    'stock_negative_quant',
    'stock_dead_stock',
    'acc_overdue_customer_invoices',
    'acc_overdue_vendor_bills',
    'md_duplicate_partners',
    'md_partner_no_contact',
)

LITE_SCOPES = ('sales', 'purchase', 'inventory', 'accounting', 'master_data')

PRO_APPS_URL = 'https://apps.odoo.com/apps/modules/18.0/elaf_erp_health'
