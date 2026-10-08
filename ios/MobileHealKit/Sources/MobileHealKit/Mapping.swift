import Foundation

/// JSON ⇄ domain mapping for the MobileHeal REST and WebSocket payloads.
public enum Mapping {
    private static let metaKeys: Set<String> = ["id", "updated_at", "missing"]

    public static func saveOutcome(from data: Data) throws -> SaveOutcome {
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = obj["id"] as? Int else { throw MobileHealError.badResponse("profile JSON without an id") }
        var fields: [String: String] = [:]
        for (k, v) in obj where !metaKeys.contains(k) && !(v is NSNull) {
            fields[k] = (v as? String) ?? "\(v)"
        }
        return SaveOutcome(profile: Profile(id: id, fields: fields), missing: obj["missing"] as? [String] ?? [])
    }

    public static func rules(from obj: [String: Any]) -> AppRules {
        let fields = (obj["rules"] as? [[String: Any]] ?? []).compactMap { r -> FieldRule? in
            guard let f = r["field"] as? String else { return nil }
            return FieldRule(field: f, required: (r["constraint"] as? String) == "required")
        }
        let ui = (obj["ui"] as? [String: Any] ?? [:]).compactMapValues { $0 as? String }
        return AppRules(fields: fields, ui: ui)
    }

    /// One WebSocket message; nil for message types this app doesn't handle.
    public static func liveEvent(from text: String) -> LiveEvent? {
        guard let data = text.data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        switch obj["type"] as? String {
        case "HEAL_REQUIRED":
            let reasons = (obj["issues"] as? [String: Any] ?? [:]).compactMapValues { $0 as? String }
            return .healRequired(missing: obj["missing"] as? [String] ?? [], reasons: reasons)
        case "HEAL_RESOLVED":
            return .healResolved
        case "CONFIG_UPDATED":
            return .rulesUpdated(rules(from: obj))
        default:
            return nil
        }
    }
}

public enum MobileHealError: LocalizedError, Equatable {
    case http(Int, String)
    case badResponse(String)
    case offline

    public var errorDescription: String? {
        switch self {
        case .http(let code, let body): return "Server error \(code): \(body.prefix(120))"
        case .badResponse(let why): return "Unexpected response: \(why)"
        case .offline: return "Can't reach the MobileHeal server. Is it running?"
        }
    }
}
