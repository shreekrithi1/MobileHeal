# INC-37: [PROD] Fatal error in save (ProfileViewModel.swift:88)

- **First seen:** 2026-10-10T05:33:17+00:00
- **Occurrences:** 1
- **Source:** ios · `ios/MobileHeal/Features/Profile/ProfileViewModel.swift:88` in `save()`

## Error

```
Fatal error: Unexpectedly found nil while unwrapping an Optional value
```

## Root cause

`c.values["phone_number"]` was nil, so the force unwrap crashed. Fall back to an empty string with `?? ""`.

## Fix

1. `c.values["phone_number"]` was nil, so the force unwrap crashed. Fall back to an empty string with `?? ""`.

   ```diff
   - let phone = c.values["phone_number"]!.trimmingCharacters(in: .whitespaces)  // MH-DEMO-BUG
   + let phone = (c.values["phone_number"] ?? "").trimmingCharacters(in: .whitespaces)  // MH-DEMO-BUG
   ```

## Verification

- Replay wasn't possible (iOS code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
