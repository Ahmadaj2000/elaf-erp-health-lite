{
    'name': 'ERP Health Check Lite',
    'version': '19.0.1.1.0',
    'category': 'Productivity',
    'summary': 'Scan sales, purchase, inventory, accounting and master data for hidden '
               'risks, with a 0-100 Health Score and corrective recommendations. '
               'Lite: 10 checks, one scope per scan, one company, up to 3 completed scans.',
    'description': """
ERP Health Check Lite
=====================
Discover hidden risks before they become costly problems.

* Run a health scan on one business area (scope) at a time: Sales, Purchase,
  Inventory, Accounting or Master Data.
* 10 built-in checks working on your real Odoo records (stale quotations,
  sales below cost, late receipts, stale RFQs, negative stock, dead stock,
  overdue customer invoices, overdue vendor bills, duplicate partners,
  partners without contact details).
* Checks that need an application you have not installed are shown as
  "not applicable" instead of failing.
* Health Score 0-100 with Healthy / Attention / At risk bands, findings by
  severity, corrective recommendations and one-click access to the affected
  records.
* Score change versus the previous scan of the same scope.
* Thresholds (number of days) configurable in Settings.
* English and Arabic.

Lite edition limits (disclosed)
-------------------------------
* One company.
* One scope per scan, manual scans only.
* 10 checks.
* Up to 3 completed scans in total (a scan that fails does not count).
  After that, all scans and findings remain viewable.

A Pro edition is available.
""",
    'author': 'Elaf Development',
    'website': 'https://www.elaf-development.com/',
    'support': 'info@elaf-development.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'product'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/check_data.xml',
        'views/check_views.xml',
        'views/finding_views.xml',
        'views/scan_views.xml',
        'views/usage_views.xml',
        'views/res_config_settings_views.xml',
        'views/dashboard_actions.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'elaf_erp_health_lite/static/src/scss/elaf_hc.scss',
            'elaf_erp_health_lite/static/src/js/gauge.js',
            'elaf_erp_health_lite/static/src/xml/gauge.xml',
            'elaf_erp_health_lite/static/src/js/dashboard.js',
            'elaf_erp_health_lite/static/src/xml/dashboard.xml',
            'elaf_erp_health_lite/static/src/js/lite_vs_pro.js',
            'elaf_erp_health_lite/static/src/xml/lite_vs_pro.xml',
        ],
    },
    'images': ['static/description/banner.png', 'static/description/main_screenshot.png'],
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
