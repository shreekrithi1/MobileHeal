# MobileHeal for iOS

SwiftUI app (iOS 17+) with the same behaviour as the Android app: dynamic profile form driven by live
business rules, DataWatchdog alerts, after-save navigation, crash reporting to MobileHeal.

```
ios/
├── project.yml              # XcodeGen spec → MobileHeal.xcodeproj
├── MobileHealKit/           # Swift package: models, mapping, validation, REST + WebSocket (pure Foundation)
├── MobileHeal/              # app: App/, Features/Profile, Features/Screens, Features/Alerts, Platform/, Generated/
└── MobileHealTests/         # view-model unit tests
```

Architecture: MVVM with unidirectional data flow — `ProfileViewModel` (@Observable, @MainActor) exposes one
`ProfileState` (loading / content / error) and intents; views are stateless and previewed in every state.
`Generated/` is written by the MobileHeal workflow (RulesDefaults.swift, dedicated after-save screens).

## Run
1. Start MobileHeal (`./start.command --server`).
2. `./start.command --ios` — generates the Xcode project with [XcodeGen](https://github.com/yonaskolb/XcodeGen)
   (`brew install xcodegen`) and opens it in Xcode.
3. Pick an iPhone simulator and press **⌘R**. The simulator reaches the server at `http://localhost:8000`.
   On a physical iPhone, set `MHBaseURL` in `project.yml` to your Mac's LAN address and start the server with
   `MOBILEHEAL_HOST=0.0.0.0 MOBILEHEAL_ALLOWED_HOSTS=<mac-ip> MOBILEHEAL_API_TOKEN=…`.

Unit tests: `cd ios/MobileHealKit && swift test` (no simulator needed), or **⌘U** in Xcode.
