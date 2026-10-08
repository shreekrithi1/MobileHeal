import Foundation

/// One field rule from requirements.txt (`field: required|optional`).
public struct FieldRule: Equatable, Hashable, Sendable {
    public let field: String
    public let required: Bool
    public init(field: String, required: Bool) {
        self.field = field
        self.required = required
    }
}

/// What the app does after a successful save (`ui.after_save`).
public enum AfterSave: Equatable, Sendable {
    case stay
    case navigate(screenID: String)
}

/// Content of a destination screen (`screen.<id>.title`, `screen.<id>.message`).
public struct ScreenSpec: Equatable, Sendable {
    public let id: String
    public let title: String
    public let message: String
}

/// Business rules pushed live from the MobileHeal backend. `ui` holds look-and-feel and navigation keys
/// exactly as written in requirements.txt.
public struct AppRules: Equatable, Sendable {
    public static let success = "success"
    public static let empty = AppRules(fields: [], ui: [:])

    public let fields: [FieldRule]
    public let ui: [String: String]

    public init(fields: [FieldRule], ui: [String: String]) {
        self.fields = fields
        self.ui = ui
    }

    public var requiredFields: [String] { fields.filter(\.required).map(\.field) }

    /// Fields to show: name and email always, then every field that has a rule.
    public var displayFields: [String] {
        var seen = Set<String>()
        return (["name", "email"] + fields.map(\.field)).filter { seen.insert($0).inserted }
    }

    public var afterSave: AfterSave {
        let target = (ui["after_save"] ?? "").trimmingCharacters(in: .whitespaces)
        switch target {
        case "", "stay": return .stay
        case "success_screen": return .navigate(screenID: AppRules.success)
        default: return .navigate(screenID: target)
        }
    }

    public func screen(_ id: String) -> ScreenSpec {
        if id == AppRules.success {
            return ScreenSpec(id: id, title: ui["success_title"] ?? "Profile saved",
                              message: ui["success_message"] ?? "Thanks — your details are up to date.")
        }
        return ScreenSpec(id: id, title: ui["screen.\(id).title"] ?? id.titleCased, message: ui["screen.\(id).message"] ?? "")
    }
}

/// A user's profile: dynamic attributes keyed by field id (e.g. "name", "phone_number").
public struct Profile: Equatable, Sendable {
    public let id: Int
    public let fields: [String: String]
    public init(id: Int, fields: [String: String]) {
        self.id = id
        self.fields = fields
    }
    public func value(_ field: String) -> String { fields[field] ?? "" }
}

/// Result of a save: the persisted profile and the required fields the server still considers missing.
public struct SaveOutcome: Equatable, Sendable {
    public let profile: Profile
    public let missing: [String]
}

/// Real-time events from the MobileHeal backend (WebSocket).
public enum LiveEvent: Equatable, Sendable {
    /// `reasons`: field → what's wrong, from the DataWatchdog (e.g. "“12” isn't a valid phone number").
    case healRequired(missing: [String], reasons: [String: String])
    case healResolved
    case rulesUpdated(AppRules)
    case connectionChanged(Bool)
}

public extension String {
    /// "phone_number" → "Phone Number"
    var titleCased: String {
        split(separator: "_").filter { !$0.isEmpty }.map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }
}
