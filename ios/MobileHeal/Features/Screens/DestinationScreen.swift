import MobileHealKit
import SwiftUI

/// Screen shown after a save (`ui.after_save`). Generated screens take precedence; otherwise a generic info screen.
struct DestinationScreen: View {
    let spec: ScreenSpec

    var body: some View {
        if let generated = GeneratedDestinations.view(for: spec) {
            generated
        } else {
            InfoScreen(spec: spec)
        }
    }
}

struct InfoScreen: View {
    let spec: ScreenSpec
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.seal.fill").font(.system(size: 56)).foregroundStyle(.green)
            Text(spec.title).font(.title).bold().multilineTextAlignment(.center)
            if !spec.message.isEmpty {
                Text(spec.message).font(.body).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            Button("Done") { dismiss() }.buttonStyle(.borderedProminent).padding(.top, 8)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle(spec.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview { NavigationStack { InfoScreen(spec: ScreenSpec(id: "success", title: "Profile saved", message: "Thanks — your details are up to date.")) } }
