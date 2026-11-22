import logging
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.elaf_erp_health_lite.models.compat import _
from odoo.exceptions import AccessError

from .limits import LITE_CHECK_CODES

_logger = logging.getLogger(__name__)

SCOPES = [
    ('sales', 'Sales'),
    ('purchase', 'Purchase'),
    ('inventory', 'Inventory'),
    ('accounting', 'Accounting'),
    ('master_data', 'Master Data'),
]

SEVERITIES = [
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('high', 'High'),
    ('critical', 'Critical'),
]

# Severity factor used by the Health Score formula.
SEVERITY_FACTOR = {'low': 1.0, 'medium': 2.0, 'high': 3.0, 'critical': 4.0}

# Default thresholds (days) stored as ir.config_parameter, editable in Settings.
PARAM_PREFIX = 'elaf_erp_health.'
PARAM_DEFAULTS = {
    'stale_quote_days': 30,
    'stale_rfq_days': 30,
    'dead_stock_days': 180,
    'overdue_days': 30,
}

# Fields that define what a built-in check is (its implementation, business
# area and data). They come from the module data only; nobody may rewrite them
# through the UI or RPC (HC-03: re-scoping checks would bypass "one scope per
# scan" and mix areas). Managers keep tuning weight, severity, saturation
# count, thresholds and active.
LOCKED_CHECK_FIELDS = {'code', 'scope', 'res_model', 'param_key'}

# Access needed to run a check and to see its findings (HC-04 / HC-21).
# Checks read data in sudo; these requirements bound that sudo to what the
# person running the scan (or reading the result) may read in Odoo itself.
# Tokens are either a model name (the user needs ACL read access on it) or one
# of the group tokens of ACCESS_GROUPS.
ACCESS_ACCOUNTING = 'accounting'
ACCESS_GROUPS = {
    # "Accounting read access": Accounting read-only, Invoicing (billing), or
    # any accounting group implying one of them (Bookkeeper, Accountant,
    # Billing Administrator).
    ACCESS_ACCOUNTING: 'account.group_account_readonly,account.group_account_invoice',
}
CHECK_ACCESS = {
    'sale_stale_quotation': ('sale.order',),
    'sale_below_cost': ('sale.order.line',),
    'purchase_late_receipt': ('purchase.order',),
    'purchase_stale_rfq': ('purchase.order',),
    'stock_negative_quant': ('stock.quant',),
    'stock_dead_stock': ('stock.quant', 'stock.move'),
    'acc_overdue_customer_invoices': (ACCESS_ACCOUNTING, 'account.move'),
    'acc_overdue_vendor_bills': (ACCESS_ACCOUNTING, 'account.move'),
    'md_duplicate_partners': ('res.partner',),
    'md_partner_no_contact': ('res.partner',),
}


class ElafHealthCheck(models.Model):
    _name = 'elaf.health.check'
    _description = 'ERP Health Check Definition'
    _order = 'scope, sequence, id'

    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, readonly=True, index=True,
                       help="Technical code linking this check to its Python implementation.")
    name = fields.Char(required=True, translate=True)
    scope = fields.Selection(SCOPES, required=True, readonly=True)
    severity = fields.Selection(SEVERITIES, required=True, default='medium')
    weight = fields.Float(default=1.0, required=True,
                          help="Relative importance of the check in the Health Score.")
    count_threshold = fields.Integer(
        string='Saturation Count', default=10, required=True,
        help="Number of affected records at which this check applies its full penalty "
             "to the Health Score (penalty grows linearly up to this count).")
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True)
    recommendation = fields.Text(translate=True)
    required_modules = fields.Char(
        help="Comma-separated technical names of the Odoo modules this check needs. "
             "When one of them is not installed, the check is reported as not applicable.")
    res_model = fields.Char(string='Affected Model', readonly=True)
    param_key = fields.Char(string='Threshold Parameter', readonly=True,
                            help="Settings parameter (in days or %) used by this check, if any.")
    exposure_formula = fields.Char(
        translate=True,
        help="How the estimated financial exposure of this check is computed. "
             "Exposure figures are estimates.")
    is_applicable = fields.Boolean(compute='_compute_is_applicable',
                                   string='Applicable on this database')
    missing_modules = fields.Char(compute='_compute_is_applicable')

    _sql_constraints = [('code_uniq', 'unique(code)', 'The check code must be unique.')]

    def write(self, vals):
        # Only module data (loaded as superuser) defines a check (HC-03).
        if not self.env.su and LOCKED_CHECK_FIELDS.intersection(vals):
            raise AccessError(_(
                "The code, scope, model and threshold parameter of a check are defined by the "
                "application and cannot be changed."))
        return super().write(vals)

    @api.depends('required_modules')
    def _compute_is_applicable(self):
        installed = self._installed_modules()
        for check in self:
            missing = [m for m in check._required_module_list() if m not in installed]
            check.missing_modules = ', '.join(missing)
            check.is_applicable = not missing

    def _required_module_list(self):
        self.ensure_one()
        return [m.strip() for m in (self.required_modules or '').split(',') if m.strip()]

    @api.model
    def _installed_modules(self):
        self.env.cr.execute("SELECT name FROM ir_module_module WHERE state = 'installed'")
        return {r[0] for r in self.env.cr.fetchall()}

    # ------------------------------------------------------------------
    # Engine helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_param(self, key):
        """Integer threshold from Settings (ir.config_parameter)."""
        raw = self.env['ir.config_parameter'].sudo().get_param(PARAM_PREFIX + key)
        try:
            value = int(float(raw)) if raw not in (None, False, '') else None
        except ValueError:
            value = None
        if value is None or value < 0:
            value = self._param_defaults().get(key, 0)
        return value

    @api.model
    def _param_defaults(self):
        return dict(PARAM_DEFAULTS)

    def _param_for_check(self, key):
        """Threshold used by this check. Hook: Pro allows per-check overrides."""
        self.ensure_one()
        return self._get_param(key)

    # ------------------------------------------------------------------
    # Access of the person running a scan or reading its findings
    # ------------------------------------------------------------------
    @api.model
    def _hc_access_requirements(self):
        """Access tokens per check code. Hook: Pro adds its checks."""
        return dict(CHECK_ACCESS)

    @api.model
    def _hc_access_groups(self):
        """Group token -> comma-separated group xmlids (any of them). Hook for Pro."""
        return dict(ACCESS_GROUPS)

    @api.model
    def _hc_access_group_label(self, token):
        if token == ACCESS_ACCOUNTING:
            return _("Requires accounting access")
        return _("Requires additional access rights")

    @api.model
    def _hc_denied_checks(self, user):
        """Return {check code: reason} for the checks ``user`` may not run
        or read, because they reveal data the user cannot read in Odoo.

        A check needs every token of its requirement: read access (ACL) on
        each listed model, and membership of the listed access groups.
        Models that are not installed are skipped here: such checks are
        reported as not applicable by the engine anyway.
        """
        user = user.sudo()
        requirements = self._hc_access_requirements()
        groups = self._hc_access_groups()
        IrModel = self.env['ir.model'].sudo()
        cache, denied = {}, {}
        for check in self.sudo().with_context(active_test=False).search([]):
            tokens = requirements.get(check.code)
            if tokens is None:
                tokens = (check.res_model,) if check.res_model else ()
            reasons = []
            for token in tokens:
                if token not in cache:
                    if token in groups:
                        # Odoo 17: no res.users.has_groups(); any listed group grants access
                        ok = any(user.has_group(g.strip()) for g in groups[token].split(','))
                        cache[token] = None if ok else self._hc_access_group_label(token)
                    elif token not in self.env:
                        cache[token] = None
                    else:
                        ok = self.env[token].with_user(user).check_access_rights('read', raise_exception=False)
                        cache[token] = None if ok else _(
                            "Requires read access to %(model)s", model=IrModel._get(token).name or token)
                reason = cache[token]
                if reason and reason not in reasons:
                    reasons.append(reason)
            if reasons:
                denied[check.code] = '; '.join(reasons)
        return denied

    def _hc_missing_modules(self, installed):
        self.ensure_one()
        return [m for m in self._required_module_list() if m not in installed]

    def _hc_unavailable_reason(self, installed):
        """Why this check cannot run on this database at all (a required
        application is not installed, or no implementation), else False.
        Independent of the user: access rights are handled separately."""
        self.ensure_one()
        missing = self._hc_missing_modules(installed)
        if missing:
            return _("Requires the module(s): %(mods)s", mods=', '.join(missing))
        if getattr(self, '_hc_check_%s' % self.code, None) is None:
            return _("No implementation available in this edition.")
        if self.res_model and self.res_model not in self.env:
            return _("Model %(model)s is not available.", model=self.res_model)
        return False

    @api.model
    def _hc_allowed_codes(self):
        """Codes that may run in this edition (None = no restriction)."""
        if self.env['elaf.health.edition']._elaf_health_is_pro():
            return None
        return set(LITE_CHECK_CODES)

    def _hc_evaluate(self, scan, installed):
        """Run one check for ``scan``.

        Returns a dict with ``result`` ('fail', 'pass' or 'na') and the details.
        The implementation method is ``_hc_check_<code>`` and returns
        ``{'ids': [...], 'model': str, 'exposure': float}`` or ``{'na': reason}``.
        """
        self.ensure_one()
        reason = self._hc_unavailable_reason(installed)
        if reason:
            return {'result': 'na', 'na_reason': reason}
        res = getattr(self, '_hc_check_%s' % self.code)(scan) or {}
        if res.get('na'):
            return {'result': 'na', 'na_reason': res['na']}
        ids = list(dict.fromkeys(res.get('ids') or []))
        count = res.get('count', len(ids))
        return {
            'result': 'fail' if count else 'pass',
            'res_model': res.get('model') or self.res_model,
            'ids': ids,
            'count': count,
            'exposure': round(float(res.get('exposure') or 0.0), 2) if count else 0.0,
            'detail': res.get('detail') or False,
        }

    # Small shared helpers for implementations -------------------------
    @api.model
    def _hc_cutoff(self, days):
        return fields.Datetime.now() - timedelta(days=days)

    @api.model
    def _hc_product_costs(self, company, product_ids):
        """Cost (standard_price) per product for ``company``, read in batch."""
        if not product_ids:
            return {}
        products = self.env['product.product'].sudo().with_company(company).with_context(
            active_test=False).browse(list(product_ids))
        return {p.id: p.standard_price or 0.0 for p in products}
