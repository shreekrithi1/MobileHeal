# INC-35: [PROD] RuntimeException in onCreate (MobileHealApp.kt:19)

- **First seen:** 2026-10-10T04:53:58+00:00
- **Occurrences:** 1
- **Source:** android · `android/app/src/main/java/com/mobileheal/app/MobileHealApp.kt:19` in `onCreate()`

## Error

```
RuntimeException: MobileHeal Crashlytics Integration Verified
```

## Root cause

Unhandled RuntimeException in onCreate().

## Fix

1. Claude: The app intentionally threw a RuntimeException to verify Crashlytics, causing a crash. Wrapping the debug verification report in runCatching prevents the exception from crashing the app while keeping the verification call.

   ```diff
   - CrashReporter.report(BuildConfig.BASE_URL, RuntimeException("MobileHeal Crashlytics Integration Verified"))
   + (see diff)
   ```

## Verification

- Replay wasn't possible (Android code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
