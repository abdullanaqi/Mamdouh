import { formatCents } from '@/lib/utils';

/**
 * Transactional email templates (spec T3.3).
 *
 * Implemented as typed HTML-string builders rather than React Email JSX — a
 * documented, low-risk deviation that avoids adding a render dependency while
 * producing the same transactional HTML. They are sent through lib/integrations
 * /email (log driver in dev, Resend in prod) and are unit-tested for content.
 */
function layout(title: string, bodyHtml: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${escapeHtml(title)}</title></head>
<body style="margin:0;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0a0a0a">
<div style="max-width:600px;margin:0 auto;padding:24px">
  <div style="font-weight:600;font-size:18px;color:#0f766e;margin-bottom:16px">Crewline</div>
  <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px">
    ${bodyHtml}
  </div>
  <div style="color:#64748b;font-size:12px;margin-top:16px">Sent by Crewline on behalf of your cleaning provider.</div>
</div></body></html>`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function paragraphs(text: string): string {
  return text
    .split(/\n{2,}/)
    .map((p) => `<p style="margin:0 0 12px;line-height:1.5">${escapeHtml(p).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

export type QuoteEmailData = {
  companyName: string;
  clientName: string;
  proposalText: string;
  lineItems: Array<{ description: string; amountCents: number }>;
  totalCents: number;
  frequency: string;
};

export function quoteEmail(d: QuoteEmailData): { subject: string; html: string; text: string } {
  const rows = d.lineItems
    .map(
      (li) =>
        `<tr><td style="padding:6px 0;border-bottom:1px solid #eef2f7">${escapeHtml(li.description)}</td>
         <td style="padding:6px 0;border-bottom:1px solid #eef2f7;text-align:right">${formatCents(li.amountCents)}</td></tr>`,
    )
    .join('');
  const html = layout(
    `Proposal from ${d.companyName}`,
    `${paragraphs(d.proposalText)}
     <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:14px">
       ${rows}
       <tr><td style="padding:8px 0;font-weight:700">Total (${escapeHtml(d.frequency)})</td>
       <td style="padding:8px 0;text-align:right;font-weight:700">${formatCents(d.totalCents)}</td></tr>
     </table>`,
  );
  const text = `${d.proposalText}\n\n${d.lineItems
    .map((li) => `- ${li.description}: ${formatCents(li.amountCents)}`)
    .join('\n')}\nTotal (${d.frequency}): ${formatCents(d.totalCents)}`;
  return { subject: `Cleaning proposal from ${d.companyName}`, html, text };
}

export type InvoiceEmailData = {
  companyName: string;
  clientName: string;
  number: string;
  lineItems: Array<{ description: string; quantity: number; unitPriceCents: number; amountCents: number }>;
  subtotalCents: number;
  taxCents: number;
  totalCents: number;
  dueDate: string | null;
  reminder?: boolean;
};

export function invoiceEmail(d: InvoiceEmailData): { subject: string; html: string; text: string } {
  const rows = d.lineItems
    .map(
      (li) =>
        `<tr><td style="padding:6px 0;border-bottom:1px solid #eef2f7">${escapeHtml(li.description)}</td>
         <td style="padding:6px 0;border-bottom:1px solid #eef2f7;text-align:right">${formatCents(li.amountCents)}</td></tr>`,
    )
    .join('');
  const intro = d.reminder
    ? `<p style="margin:0 0 12px">This is a friendly reminder that invoice <strong>${escapeHtml(d.number)}</strong> is awaiting payment.</p>`
    : `<p style="margin:0 0 12px">Please find invoice <strong>${escapeHtml(d.number)}</strong> below.</p>`;
  const html = layout(
    `Invoice ${d.number} from ${d.companyName}`,
    `<h2 style="margin:0 0 12px;font-size:18px">Invoice ${escapeHtml(d.number)}</h2>
     ${intro}
     <table style="width:100%;border-collapse:collapse;font-size:14px">${rows}
       <tr><td style="padding:6px 0;text-align:right;color:#64748b">Subtotal</td><td style="padding:6px 0;text-align:right">${formatCents(d.subtotalCents)}</td></tr>
       <tr><td style="padding:6px 0;text-align:right;color:#64748b">Tax</td><td style="padding:6px 0;text-align:right">${formatCents(d.taxCents)}</td></tr>
       <tr><td style="padding:8px 0;text-align:right;font-weight:700">Total</td><td style="padding:8px 0;text-align:right;font-weight:700">${formatCents(d.totalCents)}</td></tr>
     </table>
     ${d.dueDate ? `<p style="margin:12px 0 0;color:#64748b">Due by ${escapeHtml(d.dueDate)}</p>` : ''}`,
  );
  const text = `Invoice ${d.number} from ${d.companyName}\n${d.lineItems
    .map((li) => `- ${li.description}: ${formatCents(li.amountCents)}`)
    .join('\n')}\nTotal: ${formatCents(d.totalCents)}${d.dueDate ? `\nDue by ${d.dueDate}` : ''}`;
  return {
    subject: d.reminder ? `Reminder: Invoice ${d.number}` : `Invoice ${d.number} from ${d.companyName}`,
    html,
    text,
  };
}
