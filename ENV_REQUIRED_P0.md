# Required environment variables (P0 notifications + Razorpay webhook)

Add these to `/root/rockmartialarts-be/.env`, then:
`pm2 restart rockmartialarts-be --update-env`

Do **not** commit real secrets.

## Razorpay webhook recovery (M06)

```
RAZORPAY_WEBHOOK_SECRET=whsec_xxxxxxxx
```

Create the webhook in Razorpay Dashboard → Webhooks:
- URL: `https://<your-api-host>/api/payments/razorpay/webhook`
- Events: `payment.captured`, `payment.failed` (and order paid if used)
- Copy the webhook secret into `RAZORPAY_WEBHOOK_SECRET`

Already present (keep as-is if working):
- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET` (or `RAZORPAY_SECRET`)

## Email / Resend (M18)

Resend is primary. Domain must be verified in https://resend.com/domains.

```
RESEND_API_KEY=re_xxxxxxxx
RESEND_FROM_EMAIL=noreply@YOUR_VERIFIED_DOMAIN
RESEND_FROM_NAME=Rock Martial Arts Academy
```

**Important:** If `RESEND_FROM_EMAIL` uses an unverified domain (e.g. `techantum.com`), Resend returns HTTP 403 and mail fails.

Optional webhook fallback (disabled unless set — previous hardcoded URL returned 404):

```
EMAIL_WEBHOOK_URL=
```

Leave empty / unset to skip broken fallback. Only set if you have a working webhook that accepts:
`{ "to_email", "subject", "message", "html_message" }`

## WhatsApp (M18 / invoice WA)

Pick **one** provider mode:

### Option A — Meta Cloud API (recommended)

```
WHATSAPP_PROVIDER=meta
WHATSAPP_TOKEN=EAAxxxx
WHATSAPP_PHONE_NUMBER_ID=1234567890
WHATSAPP_API_VERSION=v19.0
WHATSAPP_SEND_MODE=text
WHATSAPP_TEMPLATE_LANG=en
```

For approved template sends:

```
WHATSAPP_SEND_MODE=template
```

(and configure invoice template name via existing invoice WhatsApp settings / env used by `invoice_whatsapp_service`)

### Option B — Custom HTTP webhook

```
WHATSAPP_PROVIDER=webhook
WHATSAPP_API_URL=https://your-wa-gateway.example/send
WHATSAPP_API_KEY=optional_bearer
```

### Do not enable stubs in production

```
ALLOW_SMS_WHATSAPP_STUB=false
```

If WhatsApp is unset, the app **refuses fake success** (correct). Invoice WA resend will show a clear failure until configured.

## After updating

1. `pm2 restart rockmartialarts-be --update-env`
2. Smoke: send a test invoice WhatsApp resend from Admin → Invoices
3. Smoke: Razorpay test payment → confirm webhook hits BE logs without signature errors
