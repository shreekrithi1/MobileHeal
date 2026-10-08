import MobileHealKit
import SwiftUI

@main
struct MobileHealApp: App {
    /// Simulator shares the Mac's network, so localhost reaches the MobileHeal server.
    /// For a physical iPhone set MHBaseURL in Info.plist to your Mac's LAN address.
    private let baseURL = URL(string: Bundle.main.object(forInfoDictionaryKey: "MHBaseURL") as? String ?? "http://localhost:8000")!
    @State private var notifier = HealNotifier()

    init() {
        CrashReporter.install(baseURL: baseURL)
    }

    var body: some Scene {
        WindowGroup {
            RootView(baseURL: baseURL, notifier: notifier)
                .onAppear { notifier.requestPermission() }
        }
    }
}

/// Tab bar: Profile · Alerts · About.
struct RootView: View {
    let baseURL: URL
    let notifier: HealNotifier
    @State private var tab = 0

    var body: some View {
        TabView(selection: $tab) {
            NavigationStack {
                ProfileView(model: ProfileViewModel(profileID: 1,
                                                    repository: RemoteProfileRepository(baseURL: baseURL),
                                                    live: LiveUpdates(baseURL: baseURL, profileID: 1),
                                                    alerts: notifier))
            }
            .tabItem { Label("Profile", systemImage: "person.crop.circle") }
            .tag(0)

            NavigationStack { AlertsView(notifier: notifier) }
                .tabItem { Label("Alerts", systemImage: "bell") }
                .badge(notifier.history.count)
                .tag(1)

            NavigationStack { AboutView(baseURL: baseURL) }
                .tabItem { Label("About", systemImage: "info.circle") }
                .tag(2)
        }
    }
}
