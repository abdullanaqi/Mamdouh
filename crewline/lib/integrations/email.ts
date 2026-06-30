import 'server-only';

/**
 * Transactional email (spec §7.3). Driver selected by EMAIL_DRIVER:
 *  - "log" (default): does NOT send; records the email to the server log and
 *    returns ok. Used in dev/tests so quote/invoice "send" flows are exercisable
 *    without a provider. Sending real email to real people is a HANDBACK item.
 *  - "resend": sends via Resend (prod). Requires RESEND_API_KEY + EMAIL_FROM.
 */
export type EmailMessage = {
  to: string;
  subject: string;
  html: string;
  text?: string;
};

export type EmailResult = { ok: boolean; id?: string; driver: string; error?: string };

export async function sendEmail(msg: EmailMessage): Promise<EmailResult> {
  const driver = process.env.EMAIL_DRIVER ?? 'log';

  if (driver === 'log') {
    console.log(
      `[email:log] to=${msg.to} subject=${JSON.stringify(msg.subject)} (not actually sent)`,
    );
    return { ok: true, id: `log_${Date.now()}`, driver };
  }

  if (driver === 'resend') {
    const key = process.env.RESEND_API_KEY;
    const from = process.env.EMAIL_FROM;
    if (!key || !from) {
      return { ok: false, driver, error: 'RESEND_API_KEY / EMAIL_FROM not set' };
    }
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from,
        to: msg.to,
        subject: msg.subject,
        html: msg.html,
        text: msg.text,
      }),
    });
    if (!res.ok) {
      return { ok: false, driver, error: `resend ${res.status}` };
    }
    const json = (await res.json()) as { id?: string };
    return { ok: true, id: json.id, driver };
  }

  return { ok: false, driver, error: `Unknown EMAIL_DRIVER: ${driver}` };
}
