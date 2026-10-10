# INC-47: [PROD] ArithmeticException in onCreate (MainActivity.kt:7)

- **First seen:** 2026-10-10T23:02:18+00:00
- **Occurrences:** 1
- **Source:** android · `android/app/src/main/java/com/mobileheal/app/MainActivity.kt:7` in `onCreate()`

## Error

```
ArithmeticException: divide by zero
```

## Root cause

This statement divides by the literal 0, so it always throws ArithmeticException. It is debug code — disable it.

## Fix

1. This statement divides by the literal 0, so it always throws ArithmeticException. It is debug code — disable it.

   ```diff
   - System.out.println("Test " + 1/0)  // MH-DEMO-BUG
   + // System.out.println("Test " + 1/0)  // disabled by MobileHeal: divides by zero
   ```

## Verification

- Replay wasn't possible (Android code can't be executed on the server).
- Fix derived from the stack trace and source; verify on a device or in CI.
