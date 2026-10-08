# INC-22: [DEV] KeyError in contact_card (contact.py:5)

- **First seen:** 2026-10-08T23:27:55+00:00
- **Occurrences:** 1
- **Source:** backend · `backend/app/features/contact.py:5` in `contact_card()`

## Error

```
KeyError: 'city'
```

## Root cause

The key `city` may be absent, so `["city"]` raised KeyError. Read it with `.get()` instead.

## Fix

1. The key `city` may be absent, so `["city"]` raised KeyError. Read it with `.get()` instead.

   ```diff
   - city = profile["city"]
   + city = profile.get("city")
   ```

## Verification

- Reproduced with the input captured in production.
- Regression test added so the crash can't come back unnoticed.
