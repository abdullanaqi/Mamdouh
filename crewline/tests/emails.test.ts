import { describe, expect, it, beforeEach } from 'vitest';
import { quoteEmail, invoiceEmail } from '@/emails/templates';
import { sendEmail } from '@/lib/integrations/email';

describe('email templates', () => {
  it('renders a quote email with line items and total', () => {
    const e = quoteEmail({
      companyName: 'Sparkle Pro',
      clientName: 'Acme Dental',
      proposalText: 'We would love to clean for you.\n\nReliable, verified service.',
      lineItems: [
        { description: 'Restrooms', amountCents: 6000 },
        { description: 'Floors', amountCents: 4000 },
      ],
      totalCents: 10000,
      frequency: 'weekly',
    });
    expect(e.subject).toContain('Sparkle Pro');
    expect(e.html).toContain('Restrooms');
    expect(e.html).toContain('$100.00');
    expect(e.text).toContain('Total (weekly): $100.00');
  });

  it('escapes HTML in untrusted fields', () => {
    const e = quoteEmail({
      companyName: 'A',
      clientName: 'B',
      proposalText: 'Hello <script>alert(1)</script>',
      lineItems: [{ description: '<b>x</b>', amountCents: 100 }],
      totalCents: 100,
      frequency: 'monthly',
    });
    expect(e.html).not.toContain('<script>');
    expect(e.html).toContain('&lt;script&gt;');
    expect(e.html).toContain('&lt;b&gt;x&lt;/b&gt;');
  });

  it('renders an invoice email and a reminder variant', () => {
    const base = {
      companyName: 'Sparkle Pro',
      clientName: 'Acme',
      number: 'INV-0007',
      lineItems: [{ description: 'svc', quantity: 2, unitPriceCents: 5000, amountCents: 10000 }],
      subtotalCents: 10000,
      taxCents: 500,
      totalCents: 10500,
      dueDate: '2026-07-15',
    };
    const normal = invoiceEmail(base);
    expect(normal.subject).toContain('INV-0007');
    expect(normal.html).toContain('$105.00');
    const reminder = invoiceEmail({ ...base, reminder: true });
    expect(reminder.subject).toContain('Reminder');
    expect(reminder.html).toContain('reminder');
  });
});

describe('email delivery (log driver)', () => {
  beforeEach(() => {
    process.env.EMAIL_DRIVER = 'log';
  });
  it('returns ok with the log driver and does not throw', async () => {
    const res = await sendEmail({ to: 'x@example.com', subject: 's', html: '<p>hi</p>' });
    expect(res.ok).toBe(true);
    expect(res.driver).toBe('log');
  });
});
