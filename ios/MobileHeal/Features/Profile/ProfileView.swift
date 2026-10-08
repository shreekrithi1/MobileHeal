import MobileHealKit
import SwiftUI

struct ProfileView: View {
    @State var model: ProfileViewModel

    var body: some View {
        content
            .navigationTitle(title)
            .task { await model.onAppear() }
            .navigationDestination(item: $model.destination) { id in
                DestinationScreen(spec: model.screen(id))
            }
    }

    private var title: String {
        model.currentRules.ui["app_title"] ?? "Profile"
    }

    @ViewBuilder private var content: some View {
        switch model.state {
        case .loading:
            ProgressView("Loading profile…").frame(maxWidth: .infinity, maxHeight: .infinity)
        case .error(let message):
            ContentUnavailableView {
                Label("Can't load your profile", systemImage: "wifi.exclamationmark")
            } description: {
                Text(message)
            } actions: {
                Button("Try again") { Task { await model.load() } }.buttonStyle(.borderedProminent)
            }
        case .content(let c):
            ProfileForm(content: c,
                        onChange: { model.update($0, $1) },
                        onSave: { Task { await model.save() } })
        }
    }
}

/// Stateless form so it can be previewed in every state.
struct ProfileForm: View {
    let content: ProfileContent
    let onChange: (String, String) -> Void
    let onSave: () -> Void

    var body: some View {
        Form {
            if let banner = content.banner ?? (content.missing.isEmpty ? nil : "Please add: " + content.missing.map(\.titleCased).joined(separator: ", ")) {
                Section {
                    Label(banner, systemImage: content.missing.isEmpty && content.reasons.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(content.missing.isEmpty && content.reasons.isEmpty ? .green : .orange)
                        .listRowBackground(Color(hex: content.rules.ui["banner_color"]) ?? Color(.secondarySystemGroupedBackground))
                }
            }
            Section {
                ForEach(content.fields, id: \.self) { field in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 2) {
                            Text(field.titleCased).font(.footnote).foregroundStyle(.secondary)
                            if content.isRequired(field) { Text("*").font(.footnote).foregroundStyle(.red) }
                        }
                        TextField(field.titleCased, text: Binding(get: { content.values[field] ?? "" }, set: { onChange(field, $0) }))
                            .textContentType(field.contentType)
                            .keyboardType(field.keyboard)
                            .textInputAutocapitalization(field == "email" ? .never : .words)
                        if let reason = content.reasons[field] {
                            Text(reason).font(.caption).foregroundStyle(.red)
                        } else if content.missing.contains(field) {
                            Text("Required").font(.caption).foregroundStyle(.orange)
                        }
                    }
                    .padding(.vertical, 2)
                }
            } footer: {
                Label(content.connected ? "Live" : "Offline", systemImage: content.connected ? "dot.radiowaves.left.and.right" : "wifi.slash")
                    .font(.caption)
            }
            Section {
                Button(action: onSave) {
                    HStack {
                        Spacer()
                        if content.isSaving { ProgressView().tint(.white) } else { Text(content.rules.ui["button_label"] ?? "Save").bold() }
                        Spacer()
                    }
                }
                .foregroundStyle(Color(hex: content.rules.ui["button_text_color"]) ?? .white)
                .listRowBackground(Color(hex: content.rules.ui["button_color"]) ?? .accentColor)
                .disabled(content.isSaving)
                .accessibilityIdentifier("save")
            }
        }
    }
}

private extension String {
    var contentType: UITextContentType? {
        switch self {
        case "email": return .emailAddress
        case "phone_number": return .telephoneNumber
        case "name": return .name
        case "postal_code", "zip": return .postalCode
        default: return nil
        }
    }
    var keyboard: UIKeyboardType {
        switch self {
        case "email": return .emailAddress
        case "phone_number": return .phonePad
        default: return .default
        }
    }
}

extension Color {
    /// "#RRGGBB" → Color; nil for anything else.
    init?(hex: String?) {
        guard let hex, hex.count == 7, hex.hasPrefix("#"), let v = UInt32(hex.dropFirst(), radix: 16) else { return nil }
        self.init(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }
}

// MARK: - Previews (loading, success, empty, error)
private let previewRules = AppRules(fields: [FieldRule(field: "phone_number", required: true)],
                                    ui: ["button_color": "#079455", "button_label": "Save changes"])

#Preview("Success") {
    NavigationStack {
        ProfileForm(content: ProfileContent(profileID: 1, values: ["name": "Jane Doe", "email": "jane@example.com", "phone_number": "+1 415 555 0100"],
                                            rules: previewRules, banner: "Changes saved successfully", connected: true),
                    onChange: { _, _ in }, onSave: {})
            .navigationTitle("Profile")
    }
}

#Preview("Empty / missing data") {
    NavigationStack {
        ProfileForm(content: ProfileContent(profileID: 1, values: ["name": "Jane Doe", "email": "jane@example"], rules: previewRules,
                                            missing: ["phone_number"], reasons: ["email": "“jane@example” isn't a valid email address"]),
                    onChange: { _, _ in }, onSave: {})
            .navigationTitle("Profile")
    }
}

#Preview("Loading") { ProgressView("Loading profile…") }

#Preview("Error") {
    ContentUnavailableView("Can't load your profile", systemImage: "wifi.exclamationmark",
                           description: Text("Can't reach the MobileHeal server. Is it running?"))
}
