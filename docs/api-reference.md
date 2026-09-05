# API Reference

Example responses from the Anthropic OAuth API endpoints used by the app. These serve as implementation reference - field names, data types, and structure.

> [!NOTE]
> These are real-world examples with anonymized data, captured in September 2026. Fields may change without notice as these are undocumented internal endpoints. If your API response contains fields not listed here, please open an issue with an anonymized example so we can keep this reference up to date.

## /api/oauth/usage

```
https://api.anthropic.com/api/oauth/usage
```

Quota fields carry `utilization` and `resets_at`; the code-named ones (`nimbus_quill`, `tangelo`, and whichever appear next) are quota types Anthropic has not given a public name yet. Model-scoped weekly limits appear only in the `limits` array via `scope.model`. The `spend` block restates extra usage in money objects, where `balance` is the prepaid credit balance - `null` on every account observed so far, while the prepaid endpoint below returns a value for the same account.

```json
{
  "five_hour": {
    "utilization": 48.0,
    "resets_at": "2026-09-05T12:59:59.966454+00:00",
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "seven_day": {
    "utilization": 64.0,
    "resets_at": "2026-09-11T20:59:59.966479+00:00",
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "seven_day_oauth_apps": null,
  "seven_day_opus": null,
  "seven_day_sonnet": {
    "utilization": 2.0,
    "resets_at": "2026-09-11T20:59:59.966479+00:00",
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "seven_day_cowork": null,
  "seven_day_omelette": null,
  "tangelo": null,
  "iguana_necktie": null,
  "omelette_promotional": null,
  "nimbus_quill": {
    "utilization": 0.0,
    "resets_at": null,
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "cinder_cove": null,
  "copper_kite": null,
  "amber_ladder": null,
  "juniper_tide": null,
  "extra_usage": {
    "is_enabled": true,
    "monthly_limit": null,
    "used_credits": 0.0,
    "utilization": null,
    "currency": "EUR",
    "decimal_places": 2,
    "disabled_reason": null,
    "user_disabled": false,
    "spend_limit_reached": false,
    "credits_ever_enabled": true,
    "daily": null,
    "weekly": null
  },
  "limits": [
    {
      "kind": "session",
      "group": "session",
      "percent": 48,
      "severity": "normal",
      "resets_at": "2026-09-05T12:59:59.966454+00:00",
      "scope": null,
      "is_active": true
    },
    {
      "kind": "weekly_all",
      "group": "weekly",
      "percent": 64,
      "severity": "normal",
      "resets_at": "2026-09-11T20:59:59.966479+00:00",
      "scope": null,
      "is_active": false
    }
  ],
  "spend": {
    "used": { "amount_minor": 0, "currency": "EUR", "exponent": 2 },
    "limit": null,
    "percent": 0,
    "severity": "normal",
    "enabled": true,
    "disabled_reason": null,
    "cap": null,
    "balance": null,
    "auto_reload": null,
    "disclaimer": "Usage credits cover you when you hit your plan limits. [Learn more](https://support.claude.com/articles/12429409)",
    "can_purchase_credits": false,
    "can_toggle": false
  },
  "member_dashboard_available": false
}
```

## /api/oauth/profile

```
https://api.anthropic.com/api/oauth/profile
```

```json
{
  "account": {
    "uuid": "...",
    "full_name": "Max Clau",
    "display_name": "Max",
    "email": "max@clau.de",
    "has_claude_max": true,
    "has_claude_pro": false,
    "created_at": "2024-10-22T07:21:47.099776Z"
  },
  "organization": {
    "uuid": "...",
    "name": "max@clau.de's Organization",
    "organization_type": "claude_max",
    "billing_type": "stripe_subscription",
    "rate_limit_tier": "default_claude_max_5x",
    "has_extra_usage_enabled": true,
    "subscription_status": "active",
    "subscription_created_at": "2026-01-16T18:22:42.826732Z"
  },
  "application": {
    "uuid": "...",
    "name": "Claude Code",
    "slug": "claude-code"
  }
}
```

## /api/oauth/organizations/{org_uuid}/prepaid/credits

```
https://api.anthropic.com/api/oauth/organizations/{org_uuid}/prepaid/credits
```

`{org_uuid}` is the `organization.uuid` from the profile response. Amounts are in minor units, so `5597` with `"exponent": 2` means 55.97. The money objects nested in the tranches are `null` on this endpoint, unlike the one behind the web app. The balance covers promotional credits as well as purchased ones, so `promo_tranches` alone can account for it.

```json
{
  "amount": 5597,
  "currency": "EUR",
  "balance": {
    "money": { "amount_minor": 5597, "currency": "EUR", "exponent": 2 },
    "credits": null
  },
  "balance_credits": null,
  "auto_reload_settings": null,
  "pending_invoice_amount_cents": null,
  "last_paid_purchase_cents": null,
  "expiry_policy_months": null,
  "tranches": [],
  "promo_tranches": [
    {
      "remaining_amount_minor_units": 5596,
      "granted_amount_minor_units": 8500,
      "currency": "EUR",
      "expires_at": "2026-09-19T00:00:00Z",
      "granted_at": null,
      "remaining": null,
      "granted": null,
      "program_id": null
    }
  ],
  "next_expires_at": "2026-09-19T00:00:00Z"
}
```
