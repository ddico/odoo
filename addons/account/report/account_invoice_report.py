# -*- coding: utf-8 -*-

from odoo import tools
from odoo import models, fields, api

from functools import lru_cache


class AccountInvoiceReport(models.Model):
    _name = "account.invoice.report"
    _description = "Invoices Statistics"
    _auto = False
    _rec_name = 'invoice_date'
    _order = 'invoice_date desc'

    # ==== Invoice fields ====
    move_id = fields.Many2one('account.move', readonly=True)
    journal_id = fields.Many2one('account.journal', string='Journal', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Company Currency', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Partner', readonly=True)
    commercial_partner_id = fields.Many2one('res.partner', string='Partner Company', help="Commercial Entity")
    country_id = fields.Many2one('res.country', string="Country")
    invoice_user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    move_type = fields.Selection([
        ('out_invoice', 'Customer Invoice'),
        ('in_invoice', 'Vendor Bill'),
        ('out_refund', 'Customer Credit Note'),
        ('in_refund', 'Vendor Credit Note'),
        ], readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Open'),
        ('cancel', 'Cancelled')
        ], string='Invoice Status', readonly=True)
    payment_state = fields.Selection(selection=[
        ('not_paid', 'Not Paid'),
        ('in_payment', 'In Payment'),
        ('paid', 'paid')
    ], string='Payment Status', readonly=True)
    fiscal_position_id = fields.Many2one('account.fiscal.position', string='Fiscal Position', readonly=True)
    invoice_date = fields.Date(readonly=True, string="Invoice Date")

    # ==== Invoice line fields ====
    quantity = fields.Float(string='Product Quantity', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    product_uom_id = fields.Many2one('uom.uom', string='Unit of Measure', readonly=True)
    product_categ_id = fields.Many2one('product.category', string='Product Category', readonly=True)
    invoice_date_due = fields.Date(string='Due Date', readonly=True)
    account_id = fields.Many2one('account.account', string='Revenue/Expense Account', readonly=True, domain=[('deprecated', '=', False)])
    analytic_account_id = fields.Many2one('account.analytic.account', string='Analytic Account', groups="analytic.group_analytic_accounting")
    price_subtotal = fields.Float(string='Untaxed Total', readonly=True)
    price_average = fields.Float(string='Average Price', readonly=True, group_operator="avg")

    _depends = {
        'account.move': [
            'name', 'state', 'move_type', 'partner_id', 'invoice_user_id', 'fiscal_position_id',
            'invoice_date', 'invoice_date_due', 'invoice_payment_term_id', 'partner_bank_id',
        ],
        'account.move.line': [
            'quantity', 'price_subtotal', 'amount_residual', 'balance', 'amount_currency',
            'move_id', 'product_id', 'product_uom_id', 'account_id', 'analytic_account_id',
            'journal_id', 'company_id', 'currency_id', 'partner_id',
        ],
        'product.product': ['product_tmpl_id'],
        'product.template': ['categ_id'],
        'uom.uom': ['category_id', 'factor', 'name', 'uom_type'],
        'res.currency.rate': ['currency_id', 'name'],
        'res.partner': ['country_id'],
    }

    @api.model
    def _select(self):
        return '''
            SELECT
                line.id,
                line.move_id,
                line.product_id,
                line.account_id,
                line.analytic_account_id,
                line.journal_id,
                line.company_id,
                line.company_currency_id,
                line.partner_id AS commercial_partner_id,
                move.state,
                move.move_type,
                move.partner_id,
                move.invoice_user_id,
                move.fiscal_position_id,
                move.payment_state,
                move.invoice_date,
                move.invoice_date_due,
                uom_template.id                                             AS product_uom_id,
                template.categ_id                                           AS product_categ_id,
                line.quantity / NULLIF(COALESCE(uom_line.factor, 1) / COALESCE(uom_template.factor, 1), 0.0)
                                                                            AS quantity,
                -line.balance                                               AS price_subtotal,
                -line.balance / NULLIF(COALESCE(uom_line.factor, 1) / COALESCE(uom_template.factor, 1), 0.0)
                                                                            AS price_average,
                COALESCE(partner.country_id, commercial_partner.country_id) AS country_id
        '''

    @api.model
    def _from(self):
        return '''
            FROM account_move_line line
                LEFT JOIN res_partner partner ON partner.id = line.partner_id
                LEFT JOIN product_product product ON product.id = line.product_id
                LEFT JOIN account_account account ON account.id = line.account_id
                LEFT JOIN account_account_type user_type ON user_type.id = account.user_type_id
                LEFT JOIN product_template template ON template.id = product.product_tmpl_id
                LEFT JOIN uom_uom uom_line ON uom_line.id = line.product_uom_id
                LEFT JOIN uom_uom uom_template ON uom_template.id = template.uom_id
                INNER JOIN account_move move ON move.id = line.move_id
                LEFT JOIN res_partner commercial_partner ON commercial_partner.id = move.commercial_partner_id
        '''

    @api.model
    def _where(self):
        return '''
            WHERE move.move_type IN ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt')
                AND line.account_id IS NOT NULL
                AND NOT line.exclude_from_invoice_tab
        '''

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute('''
            CREATE OR REPLACE VIEW %s AS (
                %s %s %s
            )
        ''' % (
            self._table, self._select(), self._from(), self._where()
        ))

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        @lru_cache(maxsize=32)  # cache to prevent a SQL query for each data point
        def get_rate(currency_id):
            return self.env['res.currency']._get_conversion_rate(
                self.env['res.currency'].browse(currency_id),
                self.env.company.currency_id,
                self.env.company,
                self._fields['invoice_date'].today()
            )

        # First we get the structure of the results. The results won't be correct in multi-currency,
        # but we need this result structure.
        # By adding 'ids:array_agg(id)' to the fields, we will be able to map the results of the
        # second step in the structure of the first step.
        result_ref = super(AccountInvoiceReport, self).read_group(
            domain, fields + ['ids:array_agg(id)'], groupby, offset, limit, orderby, False
        )

        # In mono-currency, the results are correct, so we don't need the second step.
        if len(self.env.companies.mapped('currency_id')) <= 1:
            return result_ref

        # Reset all fields needing recomputation.
        for res_ref in result_ref:
            for field in {'price_average', 'price_subtotal'} & set(res_ref):
                res_ref[field] = 0.0

        # Then we perform another read_group, but this time we group by 'currency_id'. This way, we
        # are able to convert in batch in the current company currency.
        # During the process, we fill in the result structure we got in the previous step. To make
        # the mapping, we use the aggregated ids.
        result = super(AccountInvoiceReport, self).read_group(
            domain, fields + ['ids:array_agg(id)'], set(groupby) | {'company_currency_id'}, offset, limit, orderby, False
        )
        for res in result:
            if res.get('company_currency_id') and self.env.company.currency_id.id != res['company_currency_id'][0]:
                for field in {'price_average', 'price_subtotal'} & set(res):
                    res[field] = self.env.company.currency_id.round((res[field] or 0.0) * get_rate(res['company_currency_id'][0]))
            # Since the size of result_ref should be resonable, it should be fine to loop inside a
            # loop.
            for res_ref in result_ref:
                if res.get('ids') and res_ref.get('ids') and set(res['ids']) <= set(res_ref['ids']):
                    for field in {'price_subtotal'} & set(res_ref):
                        res_ref[field] += res[field]
                    for field in {'price_average'} & set(res_ref):
                        res_ref[field] = (res_ref[field] + res[field]) / 2 if res_ref[field] else res[field]

        return result_ref


class ReportInvoiceWithoutPayment(models.AbstractModel):
    _name = 'report.account.report_invoice'
    _description = 'Account report without payment lines'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['account.move'].browse(docids)

        qr_code_urls = {}
        for invoice in docs:
            if invoice.display_qr_code:
                new_code_url = invoice.generate_qr_code()
                if new_code_url:
                    qr_code_urls[invoice.id] = new_code_url

        return {
            'doc_ids': docids,
            'doc_model': 'account.move',
            'docs': docs,
            'qr_code_urls': qr_code_urls,
        }

class ReportInvoiceWithPayment(models.AbstractModel):
    _name = 'report.account.report_invoice_with_payments'
    _description = 'Account report with payment lines'
    _inherit = 'report.account.report_invoice'

    @api.model
    def _get_report_values(self, docids, data=None):
        rslt = super()._get_report_values(docids, data)
        rslt['report_type'] = data.get('report_type') if data else ''
        return rslt
