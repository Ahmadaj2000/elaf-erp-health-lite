import json

from odoo import api, fields, models
from odoo.exceptions import UserError

from .health_check import SCOPES, SEVERITIES


class ElafHealthFinding(models.Model):
    _name = 'elaf.health.finding'
    _description = 'ERP Health Check Result'
    _order = 'scan_id desc, result, severity_rank, penalty desc, id'

    scan_id = fields.Many2one('elaf.health.scan', required=True, ondelete='cascade', index=True,
                              readonly=True)
    company_id = fields.Many2one(related='scan_id.company_id', store=True, index=True)
    scan_date = fields.Datetime(related='scan_id.date', store=True, string='Scan Date')
    check_id = fields.Many2one('elaf.health.check', string='Check', ondelete='set null',
                               readonly=True)
    check_code = fields.Char(readonly=True)
    name = fields.Char(string='Check Name', readonly=True, required=True)
    scope = fields.Selection(SCOPES, readonly=True)
    severity = fields.Selection(SEVERITIES, readonly=True)
    severity_rank = fields.Integer(compute='_compute_severity_rank', store=True)
    weight = fields.Float(readonly=True)
    count_threshold = fields.Integer(string='Saturation Count', readonly=True)
    result = fields.Selection([
        ('fail', 'Problem found'),
        ('pass', 'Passed'),
        ('na', 'Not applicable'),
    ], readonly=True, required=True, index=True)
    count = fields.Integer(string='Affected Records', readonly=True)
    penalty = fields.Float(readonly=True, digits=(8, 4),
                           help="weight x severity factor x min(1, count / saturation count)")
    max_penalty = fields.Float(readonly=True, digits=(8, 4))
    res_model = fields.Char(string='Affected Model', readonly=True)
    res_model_name = fields.Char(compute='_compute_res_model_name', string='Record Type')
    res_ids = fields.Text(string='Affected Record IDs', readonly=True, default='[]')
    currency_id = fields.Many2one(related='scan_id.company_id.currency_id')
    exposure = fields.Monetary(
        string='Estimated Exposure', readonly=True, currency_field='currency_id',
        help="ESTIMATE in company currency. See the formula of the check.")
    exposure_formula = fields.Char(readonly=True, string='Exposure Formula')
    recommendation = fields.Text(readonly=True)
    description = fields.Text(readonly=True)
    na_reason = fields.Char(string='Why not applicable', readonly=True)
    detail = fields.Char(readonly=True)

    # Shown in the UI: the check texts in the reader's language (the stored
    # name/recommendation keep the text of the scan time as history).
    label = fields.Char(compute='_compute_texts', string='Check Title')
    advice = fields.Text(compute='_compute_texts', string='Corrective Action')
    about = fields.Text(compute='_compute_texts', string='Check Description')
    formula_label = fields.Char(compute='_compute_texts', string='Formula')

    @api.depends_context('lang')
    @api.depends('check_id', 'name', 'recommendation', 'result')
    def _compute_texts(self):
        for f in self:
            check = f.check_id.sudo()
            f.label = check.name if check else f.name
            f.advice = ((check.recommendation if check else f.recommendation)
                        if f.result == 'fail' else False)
            f.about = check.description if check else f.description
            f.formula_label = (check.exposure_formula or f.exposure_formula) if check else f.exposure_formula

    @api.depends('severity')
    def _compute_severity_rank(self):
        rank = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        for f in self:
            f.severity_rank = rank.get(f.severity, 9)

    def _compute_res_model_name(self):
        IrModel = self.env['ir.model'].sudo()
        for f in self:
            f.res_model_name = IrModel._get(f.res_model).name if f.res_model and f.res_model in self.env else f.res_model

    def _get_res_ids(self):
        self.ensure_one()
        try:
            ids = json.loads(self.res_ids or '[]')
        except ValueError:
            ids = []
        return [int(i) for i in ids if isinstance(i, int) or str(i).isdigit()]

    def action_view_records(self):
        """Open the real records behind the finding, with the user's own rights."""
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            raise UserError(self.env._("The records of this finding belong to an application "
                                       "that is not installed anymore."))
        ids = self._get_res_ids()
        if not ids:
            raise UserError(self.env._("This result has no affected records."))
        Model = self.env[self.res_model]
        Model.check_access('read')
        ctx = {'create': False}
        if 'active' in Model._fields:
            ctx['active_test'] = False
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("%(check)s: affected records", check=self.name),
            'res_model': self.res_model,
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', ids)],
            'context': ctx,
            'target': 'current',
        }
