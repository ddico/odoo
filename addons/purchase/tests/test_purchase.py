# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import fields
from odoo.tests.common import SavepointCase
from odoo.tests import Form


class TestPurchase(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super(TestPurchase, cls).setUpClass()
        cls.product_consu = cls.env['product.product'].create({
            'name': 'Product A',
            'type': 'consu',
        })
        cls.product_consu2 = cls.env['product.product'].create({
            'name': 'Product B',
            'type': 'consu',
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'vendor1', 'email': 'vendor1@test.com'})
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')

    def test_date_planned(self):
        """Set a date planned on 2 PO lines. Check that the PO date_planned is the earliest PO line date
        planned. Change one of the dates so it is even earlier and check that the date_planned is set to
        this earlier date.
        """
        po = Form(self.env['purchase.order'])
        po.partner_id = self.vendor
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu
            po_line.product_qty = 1
            po_line.price_unit = 100
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu2
            po_line.product_qty = 10
            po_line.price_unit = 200
        po = po.save()

        # Check that the same date is planned on both PO lines.
        self.assertNotEqual(po.order_line[0].date_planned, False)
        self.assertAlmostEqual(po.order_line[0].date_planned, po.order_line[1].date_planned, delta=timedelta(seconds=10))
        self.assertAlmostEqual(po.order_line[0].date_planned, po.date_planned, delta=timedelta(seconds=10))

        orig_date_planned = po.order_line[0].date_planned

        # Set an earlier date planned on a PO line and check that the PO expected date matches it.
        new_date_planned = orig_date_planned - timedelta(hours=1)
        po.order_line[0].date_planned = new_date_planned
        self.assertAlmostEqual(po.order_line[0].date_planned, po.date_planned, delta=timedelta(seconds=10))

        # Set an even earlier date planned on the other PO line and check that the PO expected date matches it.
        new_date_planned = orig_date_planned - timedelta(hours=72)
        po.order_line[1].date_planned = new_date_planned
        self.assertAlmostEqual(po.order_line[1].date_planned, po.date_planned, delta=timedelta(seconds=10))

    def test_purchase_order_sequence(self):
        PurchaseOrder = self.env['purchase.order'].with_context(tracking_disable=True)
        company = self.env.user.company_id
        self.env['ir.sequence'].search([
            ('code', '=', 'purchase.order'),
        ]).write({
            'use_date_range': True, 'prefix': 'PO/%(range_year)s/',
        })
        vals = {
            'partner_id': self.vendor.id,
            'company_id': company.id,
            'currency_id': company.currency_id.id,
            'date_order': '2019-01-01',
        }
        purchase_order = PurchaseOrder.create(vals.copy())
        self.assertTrue(purchase_order.name.startswith('PO/2019/'))
        vals['date_order'] = '2020-01-01'
        purchase_order = PurchaseOrder.create(vals.copy())
        self.assertTrue(purchase_order.name.startswith('PO/2020/'))
        # In EU/BXL tz, this is actually already 01/01/2020
        vals['date_order'] = '2019-12-31 23:30:00'
        purchase_order = PurchaseOrder.with_context(tz='Europe/Brussels').create(vals.copy())
        self.assertTrue(purchase_order.name.startswith('PO/2020/'))

    def test_reminder_1(self):
        """Set to send reminder today, check if a reminder can be send to the
        partner.
        """
        po = Form(self.env['purchase.order'])
        po.partner_id = self.vendor
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu
            po_line.product_qty = 1
            po_line.price_unit = 100
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu2
            po_line.product_qty = 10
            po_line.price_unit = 200
        # set to send reminder today
        po.date_planned = fields.Datetime.now() + timedelta(days=1)
        po.receipt_reminder_email = True
        po.reminder_date_before_receipt = 1
        po = po.save()
        po.button_confirm()

        # check vendor is a message recipient
        self.assertTrue(po.partner_id in po.message_partner_ids)

        old_messages = po.message_ids
        po._send_reminder_mail()
        messages_send = po.message_ids - old_messages
        # check reminder send
        self.assertTrue(messages_send)
        self.assertTrue(po.partner_id in messages_send.mapped('partner_ids'))

        # check confirm button
        po.confirm_reminder_mail()
        self.assertTrue(po.mail_reminder_confirmed)

    def test_reminder_2(self):
        """Set to send reminder tomorrow, check if no reminder can be send.
        """
        po = Form(self.env['purchase.order'])
        po.partner_id = self.vendor
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu
            po_line.product_qty = 1
            po_line.price_unit = 100
        with po.order_line.new() as po_line:
            po_line.product_id = self.product_consu2
            po_line.product_qty = 10
            po_line.price_unit = 200
        # set to send reminder tomorrow
        po.date_planned = fields.Datetime.now() + timedelta(days=2)
        po.receipt_reminder_email = True
        po.reminder_date_before_receipt = 1
        po = po.save()
        po.button_confirm()

        # check vendor is a message recipient
        self.assertTrue(po.partner_id in po.message_partner_ids)

        old_messages = po.message_ids
        po._send_reminder_mail()
        messages_send = po.message_ids - old_messages
        # check no reminder send
        self.assertFalse(messages_send)
