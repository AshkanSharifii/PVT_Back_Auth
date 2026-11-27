# PVT Back Auth

FastAPI authentication service for TebNote / PVT.

**Stack:** FastAPI · Uvicorn/Gunicorn · PostgreSQL · Redis · Kafka (optional) · PyJWT · Argon2 · phonenumbers · orjson · **Kavenegar SMS**

No email delivery — OTP, password reset, and invite notify are **SMS-only**.

## Features

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/auth/login` | Login → access + refresh + session user |
| `POST /api/v1/auth/logout` | Revoke refresh |
| `POST /api/v1/auth/refresh` | Rotate refresh |
| `GET /api/v1/auth/me` | Current session |
| `POST /api/v1/auth/password/forgot` | SMS OTP for reset (`{ phone }`) |
| `POST /api/v1/auth/password/reset` | Reset with `{ phone, code, password }` |
| `POST /api/v1/auth/invite/accept` | Accept invite + set password |
| `POST /api/v1/auth/invite/sms` | Send invite link by SMS (auth required) |
| `POST /api/v1/auth/membership/switch` | Switch workplace membership |
| `POST /api/v1/auth/email/change` | Change email |
| `POST /api/v1/auth/password/change` | Change password |
| `GET /api/v1/auth/memberships` | Membership options |
| `GET /api/v1/auth/workplaces` | My workplaces |
| `GET /api/v1/auth/departments` | Departments for active workplace |
| `POST /api/v1/auth/otp/send` | Phone OTP via Kavenegar |
| `POST /api/v1/auth/otp/verify` | Verify OTP |
| `GET /api/v1/auth/signup/plans` | Public plan catalog |
| `POST /api/v1/auth/signup/register` | Full signup after OTP (creates admin + workplace) |
| `POST /api/v1/auth/signup/payment/confirm` | Payment stub → activate + session tokens |
| `GET /api/v1/auth/owner/signups` | Owner lists signup requests |
| `GET /api/v1/health` | Health |

JSON uses **camelCase** for frontend compatibility (front not wired yet).

## Kavenegar setup

Put your key in `.env` (left empty on purpose):

```env
KAVENEGAR_API_KEY=
KAVENEGAR_SENDER=
KAVENEGAR_OTP_TEMPLATE=
KAVENEGAR_OTP_MESSAGE_TEMPLATE=TebNote code: {code}
```

- **With empty key:** OTP still works locally; response includes `demoCode` and `smsSent: false`.
- **With key set:** SMS is sent via Kavenegar; `demoCode` is omitted and `smsSent: true`.
- If `KAVENEGAR_OTP_TEMPLATE` is set → uses `verify/lookup.json` (recommended).
- If template is empty → uses plain `sms/send.json` with the message template.

## Quick start

```bash
brew services start postgresql@14
brew services start redis

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000

# Production-style:
# gunicorn -c gunicorn.conf.py app.main:app
```

Docs: http://localhost:8000/docs

## Demo users

| Email | Password | Phone (SMS reset) |
|---|---|---|
| `doctor` | `12345678` | `989121000001` |
| `staff` | `12345678` | `989121000002` |
| `admin` | `12345678` | `989121000003` |
| `owner` | `12345678` | `989121000004` |

### Sign up flow

```bash
# 1) OTP
curl -s http://localhost:8000/api/v1/auth/otp/send -H 'Content-Type: application/json' \
  -d '{"phone":"+989121234567"}'
curl -s http://localhost:8000/api/v1/auth/otp/verify -H 'Content-Type: application/json' \
  -d '{"phone":"+989121234567","code":"123456"}'

# 2) Register (requires verified phone)
curl -s http://localhost:8000/api/v1/auth/signup/register -H 'Content-Type: application/json' \
  -d '{"fullName":"New Admin","email":"admin@newclinic.example","phone":"+989121234567","password":"secret12","organization":"New Clinic","orgType":"clinic","planId":"professional"}'

# 3) Confirm payment stub → login tokens
curl -s http://localhost:8000/api/v1/auth/signup/payment/confirm -H 'Content-Type: application/json' \
  -d '{"signupId":"SIGNUP_ID","checkoutId":"CHECKOUT_ID"}'
```

### SMS OTP example

```bash
curl -s http://localhost:8000/api/v1/auth/otp/send \
  -H 'Content-Type: application/json' \
  -d '{"phone":"+989121234567"}'
```

### Password reset via SMS

```bash
curl -s http://localhost:8000/api/v1/auth/password/forgot \
  -H 'Content-Type: application/json' \
  -d '{"phone":"989121000001"}'

curl -s http://localhost:8000/api/v1/auth/password/reset \
  -H 'Content-Type: application/json' \
  -d '{"phone":"989121000001","code":"123456","password":"newpass123"}'
```

## Security & speed (backend)

- Access JWT default **15 minutes**; HttpOnly refresh cookie (`pvt_refresh`)
- Production refuses weak `JWT_SECRET`, open CORS `*`, and OTP demo codes
- Security headers + TrustedHost; docs disabled when `APP_ENV=production`
- Login/OTP rate limits tightened; `/me` short Redis cache
- Password change/reset **revokes all sessions**
- MFA TOTP for admin/owner: `/auth/mfa/setup` → `/confirm` → login with `mfaCode`
- Logout all devices: `POST /auth/logout-all`
- DB pool tuned; responses use **orjson**; prod run via **gunicorn**

Front mocks are unchanged and not wired.

## Tests

```bash
pytest
```
