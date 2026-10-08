# INC-5: AttributeError in make_greeting (greeting.py:5)

- **First seen:** 2026-10-08T05:37:25+00:00
- **Occurrences:** 1
- **Source:** backend · `backend/app/features/greeting.py:5` in `make_greeting()`

## Error

```
AttributeError: 'NoneType' object has no attribute 'split'
```

## Root cause

`profile["name"]` can be None, so calling `.split()` on it raised AttributeError. Fall back to an empty string when the value is missing.

## Fix

1. `profile["name"]` can be None, so calling `.split()` on it raised AttributeError. Fall back to an empty string when the value is missing.

   ```diff
   - first = profile["name"].split()[0]
   + first = (profile["name"] or "").split()[0]
   ```

2. `(profile["name"] or "").split()` can be empty, so indexing `[0]` raised IndexError. Use a safe default element when it is empty.

   ```diff
   - first = (profile["name"] or "").split()[0]
   + first = ((profile["name"] or "").split() or [""])[0]
   ```

## Verification

- Reproduced with the input captured in production.
- Regression test added so the crash can't come back unnoticed.
