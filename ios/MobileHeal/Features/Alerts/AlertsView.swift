import MobileHealKit
import SwiftUI

struct AlertsView: View {
    let notifier: HealNotifier

    var body: some View {
        Group {
            if notifier.history.isEmpty {
                ContentUnavailableView("No alerts", systemImage: "bell.slash",
                                       description: Text("You'll be notified here when your profile needs attention."))
            } else {
                List(notifier.history) { entry in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.title).font(.headline)
                        Text(entry.body).font(.subheadline).foregroundStyle(.secondary)
                        Text(entry.date, style: .relative).font(.caption).foregroundStyle(.tertiary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .navigationTitle("Alerts")
    }
}

struct AboutView: View {
    let baseURL: URL

    var body: some View {
        List {
            Section("Server") {
                LabeledContent("MobileHeal", value: baseURL.absoluteString)
                LabeledContent("Rules version", value: RulesDefaults.specVersion)
            }
            Section("How it works") {
                Text("Business rules are pushed live from MobileHeal. When your profile is missing required data, you'll get a notification and the fields are highlighted.")
                    .font(.callout)
            }
        }
        .navigationTitle("About")
    }
}
