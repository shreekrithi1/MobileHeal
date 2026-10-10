# INC-36: [PROD] RuntimeException in onCreate (MobileHealApp.kt:22)

- **First seen:** 2026-10-10T05:17:39+00:00
- **Occurrences:** 2
- **Source:** android · `android/app/src/main/java/com/mobileheal/app/MobileHealApp.kt:22` in `onCreate()`

## Error

```
RuntimeException: MobileHeal Crashlytics Integration Verified
```

## Root cause

Unhandled RuntimeException in onCreate().

## Fix

1. Claude: Removed the intentional Crashlytics verification that was causing a runtime exception during app startup.

   ```diff
   - RuntimeException("MobileHeal Crashlytics Integration Verified")
   + (see diff)
   ```

## Verification

- Replay wasn't possible (Android code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
