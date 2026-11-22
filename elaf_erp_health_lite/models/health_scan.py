import json
import logging
import time
from collections import defaultdict

import psycopg2

from odoo import api, fields, models

from odoo.addons.elaf_erp_health_lite.models.compat import _
from odoo.exceptions import AccessError, UserError

from .health_check import SCOPES, SEVERITY_FACTOR
from .limits import USAGE_KEY_SCANS

_logger = logging.getLogger(__name__)

BANDS = [('healthy', 'Healthy'), ('attention', 'Attention'), ('at_risk', 'At risk')]

# Fields only the scan engine may write (users have write access on scans so
# they can create and run them, but must not tamper with results). The totals
# (score, counts) are computed per reader and never written; they stay listed
# so that any attempt to write them is refused with the same message.
ENGINE_FIELDS = {
    'state', 'score', 'score_available', 'band', 'problem_count', 'check_count',
    'pass_count', 'na_count', 'affected_count', 'error_message',
    'previous_scan_id', 'score_delta', 'duration', 'date', 'finding_ids', 'user_id',
}
# Context key marking the scan engine. It is honoured ONLY together with a
# superuser environment (HC-01): the engine always runs in sudo(), and an RPC
# caller can set context keys but can never obtain env.su.
ENGINE_CTX = 'elaf_hc_engine'


def compute_score(findings):
    """Health Score over ``findings`` (dicts with result, penalty and
    max_penalty): 100 x (1 - sum(penalty) / sum(max penalty)) over the
    applicable ones. Returns (score, available)."""
    total_max = sum(f['max_penalty'] for f in findings if f['result'] != 'na')
    if not total_max:
        return 0.0, False
    total_penalty = sum(f['penalty'] for f in findings if f['result'] == 'fail')
    return round(100.0 * (1.0 - total_penalty / total_max), 1), True


def score_band(score):
    if score >= 85:
        return 'healthy'
    if score >= 65:
        return 'attention'
    return 'at_risk'


class ElafHealthScan(models.Model):
    _name = 'elaf.health.scan'
    _description = 'ERP Health Scan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False, default='/')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company,
                                 index=True)
    user_id = fields.Many2one('res.users', string='Run by', readonly=True)
    date = fields.Datetime(string='Scan Date', readonly=True)
    scope = fields.Selection(SCOPES, string='Scope', default='sales', tracking=True,
                             help="Business area audited by this scan.")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Completed'),
        ('failed', 'Failed'),
        ('cancel', 'Cancelled'),
    ], default='draft', readonly=True, required=True, tracking=True)
    # S5-H2: every total below is computed for the person reading it, over the
    # findings that person may see (finding record rule). Nothing is stored,
    # so a total can never include checks hidden from the reader.
    score = fields.Float(string='Health Score', compute='_compute_reader_totals', digits=(5, 1),
                         help="Computed for you over the checks whose results you may see. "
                              "Checks that need access rights you do not have are excluded.")
    score_available = fields.Boolean(compute='_compute_reader_totals')
    band = fields.Selection(BANDS, string='Status', compute='_compute_reader_totals',
                            search='_search_band')
    finding_ids = fields.One2many('elaf.health.finding', 'scan_id', string='Results', readonly=True)
    problem_count = fields.Integer(string='Problems', compute='_compute_reader_totals',
                                   help="Number of checks that found at least one problem.")
    check_count = fields.Integer(string='Checks Run', compute='_compute_reader_totals')
    pass_count = fields.Integer(string='Checks Passed', compute='_compute_reader_totals')
    na_count = fields.Integer(string='Not Applicable', compute='_compute_reader_totals')
    affected_count = fields.Integer(string='Affected Records', compute='_compute_reader_totals')
    hidden_count = fields.Integer(
        string='Hidden Checks', compute='_compute_reader_totals',
        help="Checks of this scan whose results you may not see (they need access rights you "
             "do not have). They are excluded from your score and totals.")
    request_key = fields.Char(readonly=True, copy=False, index='btree_not_null',
                              help="Idempotency key of the dashboard request that created the scan.")
    currency_id = fields.Many2one(related='company_id.currency_id')
    total_exposure = fields.Monetary(
        string='Estimated Exposure', compute='_compute_total_exposure', currency_field='currency_id',
        help="ESTIMATE. Sum of the estimated exposure of the findings you can see; each check "
             "states its own formula. Findings of checks that need access rights you do not "
             "have (for example accounting) are not included.")
    error_message = fields.Text(readonly=True)
    previous_scan_id = fields.Many2one('elaf.health.scan', string='Previous Scan', readonly=True,
                                       index='btree_not_null', ondelete='set null',
                                       help="Previous completed scan of the same scope and company.")
    score_delta = fields.Float(string='Score Change', compute='_compute_reader_totals', digits=(5, 1),
                               help="Health Score of this scan minus the previous scan of the same scope.")
    duration = fields.Float(string='Duration (s)', readonly=True, digits=(6, 2))

    _sql_constraints = [('request_key_uniq', 'unique(request_key)', 'This scan request was already received.')]

    @api.depends('state', 'previous_scan_id', 'finding_ids.result', 'finding_ids.penalty',
                 'finding_ids.max_penalty', 'finding_ids.count')
    @api.depends_context('uid')
    def _compute_reader_totals(self):
        """Totals of each scan as seen by the current user (S5-H2).

        Findings are searched with the user's own rights (``sudo(False)``,
        also when called from a superuser environment such as the PDF report),
        so the record rule hides the checks the reader may not see; the score
        and every count are then computed over the visible findings only."""
        done = self.filtered(lambda s: isinstance(s.id, int) and s.state == 'done')
        ids = set(done.ids) | set(done.previous_scan_id.ids)
        visible, total = defaultdict(list), {}
        if ids:
            Finding = self.env['elaf.health.finding']
            for f in Finding.sudo(False).search([('scan_id', 'in', list(ids))]):
                visible[f.scan_id.id].append({
                    'result': f.result, 'penalty': f.penalty,
                    'max_penalty': f.max_penalty, 'count': f.count})
            total = {scan.id: n for scan, n in Finding.sudo()._read_group(
                [('scan_id', 'in', list(ids))], ['scan_id'], ['__count'])}
        for scan in self:
            rows = visible.get(scan.id, []) if scan in done else []
            fails = [f for f in rows if f['result'] == 'fail']
            score, available = compute_score(rows)
            prev = scan.previous_scan_id
            delta = 0.0
            if available and prev and prev.id in ids:
                prev_score, prev_available = compute_score(visible.get(prev.id, []))
                if prev_available:
                    delta = round(score - prev_score, 1)
            scan.score = score
            scan.score_available = available
            scan.band = score_band(score) if available else False
            scan.check_count = len([f for f in rows if f['result'] != 'na'])
            scan.pass_count = len([f for f in rows if f['result'] == 'pass'])
            scan.na_count = len([f for f in rows if f['result'] == 'na'])
            scan.problem_count = len(fails)
            scan.affected_count = sum(f['count'] for f in fails)
            scan.hidden_count = max(total.get(scan.id, 0) - len(rows), 0) if scan in done else 0
            scan.score_delta = delta

    def _search_band(self, operator, value):
        """The band is computed per reader, so the search is evaluated on the
        scans the reader can see, with the reader's own band."""
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(_("Unsupported search on the Health Score status."))
        values = {value} if isinstance(value, (str, bool)) or value is None else set(value)
        scans = self.search([('state', '=', 'done')])
        ids = scans.filtered(lambda s: s.band in values).ids
        if operator in ('=', 'in'):
            return [('id', 'in', ids)]
        return [('id', 'not in', ids)]

    @api.depends('finding_ids.exposure', 'finding_ids.result')
    @api.depends_context('uid')
    def _compute_total_exposure(self):
        """Computed for the reader: the finding record rule hides the findings
        of checks the reader may not see (HC-04 / HC-21), so their exposure is
        not summed either."""
        Finding = self.env['elaf.health.finding'].sudo(False)
        sums = {}
        if self.ids:
            for scan, amount in Finding._read_group(
                    [('scan_id', 'in', self.ids), ('result', '=', 'fail')], ['scan_id'], ['exposure:sum']):
                sums[scan.id] = amount
        for scan in self:
            scan.total_exposure = sums.get(scan.id, 0.0)

    def _hc_visible_findings(self):
        """Findings of these scans that the current user may see.

        Always searched with the user's own rights (``sudo(False)``), even
        when called from a superuser environment such as the PDF report
        rendering, so the finding record rule applies (HC-04 / HC-21)."""
        return self.env['elaf.health.finding'].sudo(False).search([('scan_id', 'in', self.ids)])

    # ------------------------------------------------------------------ ORM
    def _hc_is_engine(self):
        """True only inside the scan engine (superuser + engine key), never
        for an RPC call, whatever context it sends."""
        return bool(self.env.su and self.env.context.get(ENGINE_CTX))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self._hc_is_engine():
                bad = ENGINE_FIELDS.intersection(vals) - {'state'}
                if bad or vals.get('state', 'draft') != 'draft':
                    raise AccessError(_("Scan results are computed by the scan engine."))
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('elaf.health.scan') or '/'
        return super().create(vals_list)

    def write(self, vals):
        if not self._hc_is_engine() and ENGINE_FIELDS.intersection(vals):
            raise AccessError(_("Scan results are computed by the scan engine."))
        if 'scope' in vals or 'company_id' in vals:
            if any(s.state != 'draft' for s in self) and not self._hc_is_engine():
                raise UserError(_("The scope of a completed scan cannot be changed."))
        return super().write(vals)

    # ------------------------------------------------------------ Access
    def _hc_check_run_access(self):
        if not (self.env.su or self.env.user.has_group('elaf_erp_health_lite.group_health_user')):
            raise AccessError(_("You need the ERP Health Check User group to run scans."))
        self.check_access_rights('write')
        self.check_access_rule('write')

    # ------------------------------------------------------------ Engine
    @api.model
    def _hc_scope_domain(self, scope):
        """Checks to run for a scope. Hook: Pro adds the 'all' scope."""
        return [('scope', '=', scope)]

    def _hc_checks_to_run(self):
        self.ensure_one()
        Check = self.env['elaf.health.check'].sudo()
        checks = Check.search(self._hc_scope_domain(self.scope))
        allowed = Check._hc_allowed_codes()
        if allowed is not None:
            checks = checks.filtered(lambda c: c.code in allowed)
        return checks

    def _hc_run_plan(self):
        """What this scan would execute for the current user (S5-H1).

        Returns a dict with ``runnable`` (checks that can run), ``denied``
        ({check: reason}: the user lacks the access rights to the data of the
        check) and ``unavailable`` ({check: reason}: a required application is
        not installed). A superuser run (scheduled audit) is never denied.
        """
        self.ensure_one()
        Check = self.env['elaf.health.check'].sudo().with_company(self.company_id)
        installed = Check._installed_modules()
        denied_codes = {} if self.env.su else Check._hc_denied_checks(self.env.user)
        plan = {'runnable': Check.browse(), 'denied': {}, 'unavailable': {}}
        for check in self._hc_checks_to_run():
            reason = check._hc_unavailable_reason(installed)
            if reason:
                plan['unavailable'][check] = reason
            elif check.code in denied_codes:
                plan['denied'][check] = denied_codes[check.code]
            else:
                plan['runnable'] |= check
        return plan

    def _hc_check_plan(self, plan, is_pro):
        """Refuse up front, before anything runs or is counted (S5-H1):

        * no check can run for this user and scope (all denied or not
          applicable): refused in Lite and Pro;
        * Lite, partial access (rule R2-H1): refused when ANY check of the
          scope is denied for this user, naming the missing access, so that a
          consumed Lite scan is always a complete scan of the bound module.
          Pro runs the checks the user may run and lists the others as not
          applicable (no allowance is involved in Pro).
        """
        self.ensure_one()
        scope_label = dict(self._fields['scope']._description_selection(self.env)).get(self.scope, self.scope)

        def details(items):
            return '; '.join('%s (%s)' % (check.name, reason) for check, reason in items.items())

        if not plan['runnable']:
            if plan['denied']:
                raise UserError(_(
                    "No check of %(scope)s can run for you, so nothing was run or counted. "
                    "Missing access: %(details)s. Ask an administrator for these access rights, "
                    "or ask a user who has them to run the scan.",
                    scope=scope_label, details=details(plan['denied'])))
            raise UserError(_(
                "No check of %(scope)s is applicable on this database, so nothing was run or "
                "counted: %(details)s.",
                scope=scope_label, details=details(plan['unavailable']) or _("no active check")))
        if plan['denied'] and not is_pro:
            raise UserError(_(
                "A Lite scan always audits the whole module. %(n)s check(s) of %(scope)s need access "
                "rights you do not have, so nothing was run or counted. Missing access: %(details)s. "
                "Ask an administrator for these access rights, or ask a user who has them to run "
                "the scan.",
                n=len(plan['denied']), scope=scope_label, details=details(plan['denied'])))

    def action_run(self):
        """Run the scan (idempotent: a completed scan is returned as is).

        S5-H1: a scan that is refused, cancelled, crashes or is a duplicate
        never consumes a Lite allowance. Refusals happen before any check
        runs; a crash is recorded as 'failed' without consumption; the scan
        row lock plus the 'done' test make duplicate clicks no-ops."""
        self.ensure_one()
        self._hc_check_run_access()
        # Lock the scan row: a duplicate click waits here, then sees 'done'.
        self.env.cr.execute("SELECT state FROM elaf_health_scan WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset(['state'])
        if self.state == 'done':
            return self._hc_action_open()
        if self.state == 'cancel':
            raise UserError(_(
                "This scan was cancelled and nothing was counted. Create a new scan to audit again."))
        Edition = self.env['elaf.health.edition']
        is_pro = Edition._elaf_health_is_pro()
        if not self.scope:
            raise UserError(_("Select the scope (business area) to scan."))
        if not is_pro:
            Edition._lite_enforce_scope(self.scope)
            Edition._lite_enforce_company(self.company_id)
        # Checks the runner may not read are not run (HC-04 / HC-21): the
        # engine's sudo is bounded by the runner's own rights. A superuser run
        # (scheduled audit) evaluates every check; the finding record rule
        # then shows each reader only what they may see.
        plan = self._hc_run_plan()
        self._hc_check_plan(plan, is_pro)
        if not is_pro:
            self.env['elaf.health.usage']._check_available(USAGE_KEY_SCANS, self)
        denied = {check.code: reason for check, reason in plan['denied'].items()}
        engine = self.sudo().with_context(**{ENGINE_CTX: True})
        started = time.time()
        try:
            with self.env.cr.savepoint():
                results = engine._hc_execute_checks(denied)
        except Exception as e:  # noqa: BLE001 - a crashed scan is recorded, not consumed
            _logger.exception("ERP Health scan %s failed", self.name)
            self.env.invalidate_all()
            engine.write({
                'state': 'failed', 'date': fields.Datetime.now(), 'user_id': self.env.uid,
                'error_message': str(e) or repr(e),
            })
            return self._hc_action_open()
        engine._hc_store_results(results, time.time() - started)
        skipped = [check for check, res in results if res.get('access_denied')]
        if skipped:
            engine.message_post(body=_(
                "%(n)s check(s) were not run because they need access rights you do not have "
                "(for example accounting): %(checks)s. They are listed as not applicable and are "
                "excluded from the Health Score.",
                n=len(skipped), checks=', '.join(c.name for c in skipped)))
        if not is_pro:
            # Same transaction as the stored results: a later failure rolls both back.
            self.env['elaf.health.usage']._consume(USAGE_KEY_SCANS, self, self.company_id, self.scope)
        engine._hc_after_done()
        return self._hc_action_open()

    def _hc_execute_checks(self, denied=None):
        """Evaluate every check of the scope. Returns a list of (check, result).

        ``denied`` maps check codes to the reason why the person running the
        scan may not run them (see ``elaf.health.check._hc_denied_checks``);
        such checks are reported as not applicable without reading any data.
        """
        self.ensure_one()
        denied = denied or {}
        Check = self.env['elaf.health.check'].sudo().with_company(self.company_id)
        installed = Check._installed_modules()
        results = []
        for check in self._hc_checks_to_run():
            if check.code in denied and not check._hc_missing_modules(installed):
                res = {'result': 'na', 'na_reason': denied[check.code], 'access_denied': True}
            else:
                res = check.with_company(self.company_id)._hc_evaluate(self, installed)
            results.append((check, res))
        return results

    def _hc_store_results(self, results, duration=0.0):
        """Create findings and close the scan.

        Each finding stores its penalty; the Health Score is computed from
        them for each reader (``_compute_reader_totals``):
        Health Score = 100 x (1 - sum(penalty) / sum(max penalty)) over the
        applicable checks the reader may see, where
            penalty     = weight x severity factor x min(1, count / saturation count)
            max penalty = weight x severity factor
        and the severity factor is low 1, medium 2, high 3, critical 4.
        Not-applicable checks are excluded. Without any applicable check no
        score is given (score_available = False).
        """
        self.ensure_one()
        Finding = self.env['elaf.health.finding'].sudo()
        vals_list = []
        for check, res in results:
            factor = SEVERITY_FACTOR.get(check.severity, 1.0)
            max_penalty = (check.weight or 0.0) * factor
            penalty = 0.0
            if res['result'] == 'fail':
                threshold = max(check.count_threshold or 1, 1)
                penalty = max_penalty * min(1.0, res['count'] / threshold)
            vals_list.append({
                'scan_id': self.id,
                'check_id': check.id,
                'check_code': check.code,
                'name': check.name,
                'scope': check.scope,
                'severity': check.severity,
                'weight': check.weight,
                'count_threshold': check.count_threshold,
                'result': res['result'],
                'count': res.get('count', 0),
                'penalty': round(penalty, 4),
                'max_penalty': round(max_penalty, 4) if res['result'] != 'na' else 0.0,
                'res_model': res.get('res_model') or check.res_model,
                'res_ids': json.dumps(res.get('ids') or []),
                'exposure': res.get('exposure', 0.0),
                'exposure_formula': check.exposure_formula,
                'recommendation': check.recommendation if res['result'] == 'fail' else False,
                'description': check.description,
                'na_reason': res.get('na_reason') or False,
                'detail': res.get('detail') or False,
            })
        Finding.create(vals_list)
        previous = self.search([
            ('company_id', '=', self.company_id.id), ('scope', '=', self.scope),
            ('state', '=', 'done'), ('id', '!=', self.id),
        ], order='date desc, id desc', limit=1)
        self.write({
            'state': 'done',
            'date': fields.Datetime.now(),
            'user_id': self.env.uid,  # the real runner, never taken from the context (HC-02)
            'previous_scan_id': previous.id if previous else False,
            'duration': round(duration, 2),
            'error_message': False,
        })

    def _hc_after_done(self):
        """Hook called after a scan completed (Pro: notifications).

        The chatter is shared by every reader of the scan, so the message
        carries no score or count (S5-H2): each reader sees their own totals
        on the scan."""
        self.ensure_one()
        self.message_post(body=_(
            "Scan completed. Open the results: the Health Score and totals are computed over the "
            "checks you may see."))

    def _hc_action_open(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'elaf.health.scan',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    # ------------------------------------------------------------ Actions
    def action_cancel(self):
        """Cancel draft or failed scans. A cancelled scan never runs and never
        consumes a Lite allowance (S5-H1)."""
        self._hc_check_run_access()
        if self.ids:
            self.env.cr.execute("SELECT id FROM elaf_health_scan WHERE id IN %s FOR UPDATE", (tuple(self.ids),))
            self.invalidate_recordset(['state'])
        if any(scan.state == 'done' for scan in self):
            raise UserError(_("A completed scan cannot be cancelled."))
        self.sudo().with_context(**{ENGINE_CTX: True}).write({'state': 'cancel'})
        return True

    def action_view_problems(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Findings"),
            'res_model': 'elaf.health.finding',
            'view_mode': 'tree,form',
            'views': [(False, 'tree'), (False, 'form')],
            'domain': [('scan_id', '=', self.id), ('result', '=', 'fail')],
            'context': {'create': False},
        }

    def action_open_previous(self):
        self.ensure_one()
        if not self.previous_scan_id:
            raise UserError(_("There is no previous scan of this scope."))
        return self.previous_scan_id._hc_action_open()

    # ------------------------------------------------------------ Dashboard
    @api.model
    def _hc_scope_options(self):
        """Scopes a user can choose for a new scan (Pro adds more)."""
        return [{'key': k, 'label': v} for k, v in self._fields['scope']._description_selection(self.env)
                if k in dict(SCOPES)]

    @api.model
    def action_dashboard_run(self, scope, request_key=None):
        """Create and run a scan from the dashboard. Returns a JSON summary.

        ``request_key`` is generated by the dashboard once per click; a
        repeated request with the same key (double click, network retry)
        returns the scan of the first request instead of running and
        counting a second one (S5-H1)."""
        if not (self.env.su or self.env.user.has_group('elaf_erp_health_lite.group_health_user')):
            raise AccessError(_("You need the ERP Health Check User group to run scans."))
        key = request_key if isinstance(request_key, str) else False
        key = (key or '').strip()[:64] or False
        if key:
            existing = self.search([('request_key', '=', key)], limit=1)
            if existing:
                return dict(existing._hc_dashboard_result(), duplicate=True)
        Edition = self.env['elaf.health.edition']
        if not Edition._elaf_health_is_pro():
            status = self.env['elaf.health.usage']._get_status()
            if status['exhausted']:
                return {'redirect': 'lite_vs_pro', 'message': self.env['elaf.health.usage']._limit_message(USAGE_KEY_SCANS)}
        valid = [o['key'] for o in self._hc_scope_options()]
        if scope not in valid:
            raise UserError(_("Select a valid scope."))
        try:
            with self.env.cr.savepoint():
                scan = self.create({'scope': scope, 'company_id': self.env.company.id, 'request_key': key})
        except psycopg2.IntegrityError:
            # the same request is being processed by a concurrent transaction
            raise UserError(_(
                "This scan request is already being processed. Refresh the dashboard in a moment."))
        scan.action_run()
        return scan._hc_dashboard_result()

    def _hc_dashboard_result(self):
        self.ensure_one()
        res = {'scan_id': self.id, 'state': self.state,
               'score': self.score if self.score_available else None,
               'error': self.error_message or False}
        if not self.env['elaf.health.edition']._elaf_health_is_pro() and self.state == 'done':
            status = self.env['elaf.health.usage']._get_status()
            res['usage'] = status
            res['first_success'] = status['used'] == 1
        return res

    @api.model
    def action_bind_lite_scope(self, scope):
        """Dashboard: a manager chooses the business module audited by Lite."""
        self.env['elaf.health.usage']._bind_scope(scope)
        return self.get_dashboard_data({})

    @api.model
    def get_dashboard_data(self, filters=None):
        """Plain JSON for the dashboard (current company only)."""
        filters = filters or {}
        company = self.env.company
        Edition = self.env['elaf.health.edition']
        is_pro = Edition._elaf_health_is_pro()
        scope_labels = dict(self._fields['scope']._description_selection(self.env))
        band_labels = dict(self._fields['band']._description_selection(self.env))
        domain = [('company_id', '=', company.id), ('state', '=', 'done')]
        scope = filters.get('scope')
        latest_domain = domain + ([('scope', '=', scope)] if scope else [])
        latest = self.search(latest_domain, order='date desc, id desc', limit=1)
        Finding = self.env['elaf.health.finding']
        sev_labels = dict(Finding._fields['severity']._description_selection(self.env))
        data = {
            'edition': 'pro' if is_pro else 'lite',
            'company': {'id': company.id, 'name': company.name},
            'currency': {'symbol': company.currency_id.symbol,
                         'position': company.currency_id.position,
                         'digits': company.currency_id.decimal_places},
            'scopes': self._hc_scope_options(),
            'is_manager': self.env.user.has_group('elaf_erp_health_lite.group_health_manager'),
            'can_run': self.env.user.has_group('elaf_erp_health_lite.group_health_user'),
            'latest': False,
            'severity': [],
            'top_findings': [],
            'recent_scans': [],
            'scan_total': self.search_count([('company_id', '=', company.id)]),
        }
        if latest:
            # explicit search: the finding record rule hides the findings the
            # reader may not see (e.g. accounting for non-accounting users)
            fails = latest._hc_visible_findings().filtered(lambda f: f.result == 'fail')
            data['latest'] = {
                'id': latest.id, 'name': latest.name,
                'scope': latest.scope, 'scope_label': scope_labels.get(latest.scope, latest.scope),
                'date': fields.Datetime.to_string(latest.date) if latest.date else False,
                'score': latest.score, 'score_available': latest.score_available,
                'band': latest.band, 'band_label': band_labels.get(latest.band, ''),
                'delta': latest.score_delta if latest.previous_scan_id else None,
                'previous_name': latest.previous_scan_id.name or False,
                'problem_count': latest.problem_count, 'check_count': latest.check_count,
                'pass_count': latest.pass_count, 'na_count': latest.na_count,
                'affected_count': latest.affected_count, 'hidden_count': latest.hidden_count,
            }
            for key, label in Finding._fields['severity']._description_selection(self.env):
                sev = fails.filtered(lambda f, k=key: f.severity == k)
                data['severity'].append({'key': key, 'label': label, 'checks': len(sev),
                                         'records': sum(sev.mapped('count'))})
            order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
            for f in fails.sorted(lambda f: (order.get(f.severity, 9), -f.penalty, -f.count)):
                data['top_findings'].append({
                    'id': f.id, 'name': f.label, 'severity': f.severity,
                    'severity_label': sev_labels.get(f.severity, ''),
                    'count': f.count, 'recommendation': f.advice or '',
                    'scope_label': scope_labels.get(f.scope, f.scope),
                })
        for s in self.search([('company_id', '=', company.id), ('state', 'in', ('done', 'failed'))],
                             order='date desc, id desc', limit=8):
            data['recent_scans'].append({
                'id': s.id, 'name': s.name, 'state': s.state,
                'scope_label': scope_labels.get(s.scope, s.scope or ''),
                'date': fields.Datetime.to_string(s.date) if s.date else False,
                'score': s.score if s.score_available else None,
                'band': s.band, 'band_label': band_labels.get(s.band, ''),
                'delta': s.score_delta if s.previous_scan_id else None,
                'problem_count': s.problem_count,
            })
        if not is_pro:
            data['usage'] = self.env['elaf.health.usage']._get_status()
            row = self.env['elaf.health.usage']._get_row(USAGE_KEY_SCANS)
            bound = row.bound_company_id
            data['company_blocked'] = bool(bound and bound != company)
            data['bound_company'] = bound.name if bound else False
            # S5-H3: Lite audits one business module
            data['bound_scope'] = row.bound_scope or False
            data['bound_scope_label'] = data['usage']['bound_scope_label']
            data['can_bind'] = bool(data['is_manager'] and not row.bound_scope)
            if row.bound_scope:
                data['scopes'] = [o for o in data['scopes'] if o['key'] == row.bound_scope]
        return data

    @api.model
    def get_lite_vs_pro_data(self):
        """Comparison page data, computed from the real scans of this company."""
        company = self.env.company
        is_pro = self.env['elaf.health.edition']._elaf_health_is_pro()
        scans = self.search([('company_id', '=', company.id), ('state', '=', 'done')])
        Finding = self.env['elaf.health.finding']
        fails = Finding.search([('scan_id', 'in', scans.ids), ('result', '=', 'fail')])
        translated = dict(self._fields['scope']._description_selection(self.env))
        scope_labels = {key: translated.get(key, label) for key, label in SCOPES}
        audited = set(scans.mapped('scope'))
        latest_per_scope = {}
        for s in scans.sorted(lambda s: (s.date or fields.Datetime.now(), s.id)):
            latest_per_scope[s.scope] = s
        open_records = sum(s.affected_count for s in latest_per_scope.values())
        open_problems = sum(s.problem_count for s in latest_per_scope.values())
        Check = self.env['elaf.health.check']
        usage = False if is_pro else self.env['elaf.health.usage']._get_status()
        return {
            'edition': 'pro' if is_pro else 'lite',
            'usage': usage,
            'bound_scope_label': usage['bound_scope_label'] if usage else False,
            'pro_url': 'https://apps.odoo.com/apps/modules/17.0/elaf_erp_health',
            'support_email': 'info@elaf-development.com',
            'value': {
                'scans': len(scans),
                'problems_found': len(fails),
                'open_problems': open_problems,
                'open_records': open_records,
                'audited_scopes': [scope_labels[s] for s in scope_labels if s in audited],
                'not_audited_scopes': [scope_labels[s] for s in scope_labels if s not in audited],
                'lite_checks': Check.search_count([]),
            },
        }
