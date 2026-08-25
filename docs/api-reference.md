# API Reference

Example responses from the Anthropic OAuth API endpoints used by the app. These serve as implementation reference - field names, data types, and structure.

> [!NOTE]
> These are real-world examples with anonymized data, captured in March 2026. Fields may change without notice as these are undocumented internal endpoints. If your API response contains fields not listed here, please open an issue with an anonymized example so we can keep this reference up to date.

## /api/oauth/usage

```
https://api.anthropic.com/api/oauth/usage
```

```json
{
  "five_hour": {
    "utilization": 48.0,
    "resets_at": "2026-03-02T11:00:00.521744+00:00"
  },
  "seven_day": {
    "utilization": 64.0,
    "resets_at": "2026-03-06T06:00:00.521764+00:00"
  },
  "seven_day_oauth_apps": null,
  "seven_day_opus": null,
  "seven_day_sonnet": {
    "utilization": 2.0,
    "resets_at": "2026-03-06T07:00:00.521773+00:00"
  },
  "seven_day_cowork": null,
  "iguana_necktie": null,
  "extra_usage": {
    "is_enabled": true,
    "monthly_limit": 1000,
    "used_credits": 0.0,
    "utilization": null
  }
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

`{org_uuid}` is the `organization.uuid` from the profile response. Amounts are in minor units, so `5597` with `"exponent": 2` means 55.97. The money objects nested in the tranches are `null` on this endpoint, unlike the one behind the web app.

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
