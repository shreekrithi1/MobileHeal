# INC-44: [PROD] NullPointerException in save (ProfileViewModel.kt:113)

- **First seen:** 2026-10-10T19:48:46+00:00
- **Occurrences:** 2
- **Source:** android · `android/app/src/main/java/com/mobileheal/app/ui/profile/ProfileViewModel.kt:113` in `save()`

## Error

```
NullPointerException: 
```

## Root cause

`content.fields["phone_number"]` can be null, so the `!!` assertion threw NullPointerException. Use `.orEmpty()` so a missing value becomes an empty string.

## Fix

1. `content.fields["phone_number"]` can be null, so the `!!` assertion threw NullPointerException. Use `.orEmpty()` so a missing value becomes an empty string.

   ```diff
   - val phone = content.fields["phone_number"]!!.trim()  // MH-DEMO-BUG
   + val phone = content.fields["phone_number"].orEmpty().trim()  // MH-DEMO-BUG
   ```

## Verification

- Replay wasn't possible (Android code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
