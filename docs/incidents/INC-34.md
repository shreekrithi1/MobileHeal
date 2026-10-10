# INC-34: [PROD] RuntimeException in onCreate (MobileHealApp.kt:18)

- **First seen:** 2026-10-10T04:52:09+00:00
- **Occurrences:** 2
- **Source:** android · `android/app/src/main/java/com/mobileheal/app/MobileHealApp.kt:18` in `onCreate()`

## Error

```
RuntimeException: MobileHeal Crashlytics Integration Verified
```

## Root cause

Unhandled RuntimeException in onCreate().

## Fix

1. Claude: Guard the intentional crash report so it only runs in debug builds, preventing the runtime exception in production.

   ```diff
   - CrashReporter.report(BuildConfig.BASE_URL, RuntimeException("MobileHeal Crashlytics Integration Verified"))
   + (see diff)
   ```

## Verification

- Replay wasn't possible (Android code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
