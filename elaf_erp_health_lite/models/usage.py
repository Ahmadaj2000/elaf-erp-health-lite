import logging

import odoo.modules.module
from odoo import SUPERUSER_ID, api, fields, models

from odoo.addons.elaf_erp_health_lite.models.compat import _
from odoo.exceptions import AccessError, UserError

from .health_check import SCOPES
from .limits import (
    ELAF_USAGE_CAPS_ENABLED,
    LITE_MAX_COMPLETED_SCANS,
    LITE_SCOPES,
    USAGE_KEY_SCANS,
)

_logger = logging.getLogger(__name__)

# Context flag that only the internal methods below set. Any write/unlink on
# usage rows without it is refused, whatever the caller's groups (sudo included).
_INTERNAL = 'elaf_hc_usage_internal'

USAGE_LIMITS = {
    USAGE_KEY_SCANS: LITE_MAX_COMPLETED_SCANS,
}


class ElafHealthEdition(models.AbstractModel):
    """Edition hook. Lite returns False; the Pro module overrides it."""
    _name = 'elaf.health.edition'
    _description = 'ERP Health Check Edition'

    @api.model
    def _elaf_health_is_pro(self):
        return False

    @api.model
    def _lite_scope_label(self, scope):
        labels = dict(self.env['elaf.health.scan']._fields['scope']._description_selection(self.env))
        return labels.get(scope, scope)

    @api.model
    def _lite_bound_message(self, bound):
        return _(
            "ERP Health Check Lite audits one business module: %(module)s. It was chosen by the "
            "first completed scan (or in Settings) and cannot be changed in Lite. Nothing was run "
            "or counted. ERP Health Check Pro audits every module.",
            module=self._lite_scope_label(bound))

    @api.model
    def _lite_enforce_scope(self, scope):
        """Lite audits ONE business module (S5-H3): the module chosen in
        Settings or, failing that, the scope of the first completed scan."""
        if self._elaf_health_is_pro():
            return
        if scope not in LITE_SCOPES:
            raise UserError(_(
                "The Lite edition scans one business area at a time (Sales, Purchase, "
                "Inventory, Accounting or Master Data). Cross-module and security scans "
                "are part of ERP Health Check Pro."))
        bound = self.env['elaf.health.usage']._get_row(USAGE_KEY_SCANS).bound_scope
        if bound and scope != bound:
            raise UserError(self._lite_bound_message(bound))

    @api.model
    def _lite_enforce_company(self, company):
        """Lite works with one company: the first company that completed a scan."""
        if self._elaf_health_is_pro():
            return
        usage = self.env['elaf.health.usage']._get_row(USAGE_KEY_SCANS)
        bound = usage.bound_company_id
        if bound and bound != company:
            raise UserError(_(
                "The Lite edition works with one company: %(company)s. "
                "ERP Health Check Pro supports multi-company audits.",
                company=bound.display_name))


class ElafHealthUsage(models.Model):
    _name = 'elaf.health.usage'
    _description = 'ERP Health Check Lite Usage Counter'
    _rec_name = 'key'

    key = fields.Char(required=True, readonly=True)
    used = fields.Integer(readonly=True, default=0)
    usage_limit = fields.Integer(string='Limit', readonly=True)
    bound_company_id = fields.Many2one('res.company', string='Bound Company', readonly=True)
    bound_scope = fields.Selection(
        SCOPES, string='Audited Module', readonly=True,
        help="Business module audited by the Lite edition: chosen by a manager in Settings or "
             "set by the first completed scan. It cannot be changed in Lite.")
    last_used_at = fields.Datetime(string='Last Used', readonly=True)

    _sql_constraints = [('key_uniq', 'unique(key)', 'One usage counter per key.')]

    # ---- protection -------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_INTERNAL):
            raise AccessError(_("Usage counters are managed by the application only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get(_INTERNAL):
            raise AccessError(_("Usage counters are managed by the application only."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(_INTERNAL):
            raise AccessError(_("Usage counters cannot be deleted."))
        return super().unlink()

    # ---- business API -------------------------------------------------------
    @api.model
    def _get_row(self, key):
        """Return the counter row for ``key``, creating it on first use.

        sudo(): counters are protected from every group (no ACL write), only
        these internal methods may create or change them.
        """
        row = self.sudo().search([('key', '=', key)], limit=1)
        if not row:
            row = self.sudo().with_context(**{_INTERNAL: True}).create({
                'key': key, 'used': 0, 'usage_limit': USAGE_LIMITS.get(key, 0),
            })
        return row

    @api.model
    def _get_status(self, key=USAGE_KEY_SCANS):
        row = self._get_row(key)
        limit = USAGE_LIMITS.get(key, 0)
        used = row.used
        caps = ELAF_USAGE_CAPS_ENABLED
        return {
            'key': key,
            'used': used,
            'limit': limit,
            'remaining': max(limit - used, 0) if caps else None,
            'pct': round(min(100.0, 100.0 * used / limit), 1) if (caps and limit) else 0.0,
            'exhausted': bool(caps and used >= limit),
            'caps_enabled': caps,
            'bound_company': row.bound_company_id.display_name or False,
            'bound_scope': row.bound_scope or False,
            'bound_scope_label': self.env['elaf.health.edition']._lite_scope_label(row.bound_scope)
            if row.bound_scope else False,
        }

    @api.model
    def _check_available(self, key, record=None):
        """Friendly pre-check before starting a counted operation."""
        if not ELAF_USAGE_CAPS_ENABLED:
            return True
        row = self._get_row(key)
        limit = USAGE_LIMITS.get(key, 0)
        if row.used >= limit:
            self._log_denied(key, record)
            raise UserError(self._limit_message(key))
        return True

    @api.model
    def _limit_message(self, key):
        limit = USAGE_LIMITS.get(key, 0)
        return _(
            "Lite scan allowance used (%(used)s/%(limit)s). All your scans and findings "
            "remain available. ERP Health Check Pro runs unlimited scans.",
            used=limit, limit=limit)

    @api.model
    def _bind_scope(self, scope):
        """Choose the business module audited by Lite (managers only, S5-H3).

        Allowed once: when Lite is already bound to another module the
        request is refused. Pro has no binding."""
        Edition = self.env['elaf.health.edition']
        if Edition._elaf_health_is_pro():
            raise UserError(_("ERP Health Check Pro audits every module: there is nothing to choose."))
        if not (self.env.su or self.env.user.has_group('elaf_erp_health_lite.group_health_manager')):
            raise AccessError(_("Only ERP Health Check managers can choose the audited module."))
        if scope not in LITE_SCOPES:
            raise UserError(_("Select a valid business module."))
        row = self._get_row(USAGE_KEY_SCANS)
        self.env.cr.execute("""
            UPDATE elaf_health_usage SET bound_scope = COALESCE(bound_scope, %s)
             WHERE id = %s RETURNING bound_scope
        """, (scope, row.id))
        bound = self.env.cr.fetchone()[0]
        row.invalidate_recordset()
        if bound != scope:
            raise UserError(Edition._lite_bound_message(bound))
        return True

    @api.model
    def _consume(self, key, record, company, scope=False):
        """Atomically consume one unit in the current transaction.

        The UPDATE takes a row lock: concurrent transactions wait, then
        re-evaluate ``used < limit``. If the surrounding operation fails later,
        the transaction rolls back and nothing is consumed. The first consumed
        scan also binds Lite to its company and its business module (S5-H3).
        Returns the new ``used`` value.
        """
        row = self._get_row(key)
        limit = USAGE_LIMITS.get(key, 0)
        cr = self.env.cr
        if ELAF_USAGE_CAPS_ENABLED:
            cr.execute("""
                UPDATE elaf_health_usage
                   SET used = used + 1,
                       last_used_at = (now() at time zone 'UTC'),
                       bound_company_id = COALESCE(bound_company_id, %s),
                       bound_scope = COALESCE(bound_scope, %s)
                 WHERE id = %s AND used < %s
             RETURNING used, bound_company_id, bound_scope
            """, (company.id, scope or None, row.id, limit))
        else:
            cr.execute("""
                UPDATE elaf_health_usage
                   SET used = used + 1,
                       last_used_at = (now() at time zone 'UTC'),
                       bound_company_id = COALESCE(bound_company_id, %s),
                       bound_scope = COALESCE(bound_scope, %s)
                 WHERE id = %s
             RETURNING used, bound_company_id, bound_scope
            """, (company.id, scope or None, row.id))
        res = cr.fetchone()
        row.invalidate_recordset()
        if not res:
            self._log_denied(key, record)
            raise UserError(self._limit_message(key))
        used, bound_id, bound_scope = res
        if bound_id and bound_id != company.id:
            # Bound to another company meanwhile (concurrent first scans).
            raise UserError(_(
                "The Lite edition works with one company: %(company)s. "
                "ERP Health Check Pro supports multi-company audits.",
                company=self.env['res.company'].browse(bound_id).display_name))
        if scope and bound_scope and bound_scope != scope:
            # Bound to another module meanwhile (concurrent first scans).
            raise UserError(self.env['elaf.health.edition']._lite_bound_message(bound_scope))
        self.env['elaf.health.usage.log'].sudo().with_context(**{_INTERNAL: True}).create({
            'key': key, 'event': 'consumed', 'user_id': self.env.uid,
            'res_model': record._name if record else False,
            'res_id': record.id if record else False,
        })
        return used

    @api.model
    def _log_denied(self, key, record=None):
        """Record a denial in an independent transaction so it survives the
        rollback caused by the UserError that follows.

        While tests run, the denial is written on the test cursor instead: a
        second connection cannot see the uncommitted test users and records,
        and test data must not be committed. Outside tests, the second
        transaction runs as superuser (it must not depend on the caller's
        user being visible) and only links the user when that row is
        committed. Any other failure is still logged with its traceback.
        """
        vals = {
            'key': key, 'event': 'denied',
            'res_model': record._name if record else False,
            'res_id': record.id if (record and isinstance(record.id, int)) else False,
        }
        if odoo.modules.module.current_test:
            with self.env.cr.savepoint():
                self.env['elaf.health.usage.log'].sudo().with_context(**{_INTERNAL: True}).create(
                    dict(vals, user_id=self.env.uid))
            return
        try:
            with self.env.registry.cursor() as cr2:
                cr2.execute("SELECT 1 FROM res_users WHERE id = %s", (self.env.uid,))
                vals['user_id'] = self.env.uid if cr2.fetchone() else False
                env2 = api.Environment(cr2, SUPERUSER_ID, {_INTERNAL: True})
                env2['elaf.health.usage.log'].create(vals)
        except Exception:  # never let logging hide the real message
            _logger.warning("Could not log usage denial for %s", key, exc_info=True)


class ElafHealthUsageLog(models.Model):
    _name = 'elaf.health.usage.log'
    _description = 'ERP Health Check Lite Usage Log'
    _order = 'id desc'

    key = fields.Char(required=True, readonly=True)
    event = fields.Selection([('consumed', 'Consumed'), ('denied', 'Denied')],
                             required=True, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True)
    res_model = fields.Char(string='Record Model', readonly=True)
    res_id = fields.Integer(string='Record ID', readonly=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(_INTERNAL):
            raise AccessError(_("The usage log is append-only and managed by the application."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("The usage log is append-only."))

    def unlink(self):
        raise AccessError(_("The usage log is append-only."))
