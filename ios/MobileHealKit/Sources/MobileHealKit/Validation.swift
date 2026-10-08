import Foundation

/// Client-side validation mirroring the backend rules and the DataWatchdog format checks.
public struct ProfileValidator: Sendable {
    public init() {}

    /// Required fields that are empty.
    public func missing(_ values: [String: String], rules: AppRules) -> [String] {
        rules.requiredFields.filter { (values[$0] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// Field → human message for values the app can't use (email / phone / date of birth).
    public func formatIssues(_ values: [String: String]) -> [String: String] {
        var out: [String: String] = [:]
        for (field, raw) in values {
            let v = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !v.isEmpty else { continue }
            if field == "email" || field.hasSuffix("_email"),
               v.range(of: #"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"#, options: .regularExpression) == nil {
                out[field] = "“\(v)” isn't a valid email address"
            }
            if field == "phone_number" || field.hasSuffix("_phone") {
                let digits = v.filter(\.isNumber).count
                if v.range(of: #"^\+?[0-9][0-9 ()\-.]{5,19}$"#, options: .regularExpression) == nil || !(7...15).contains(digits) {
                    out[field] = "“\(v)” isn't a valid phone number"
                }
            }
        }
        return out
    }
}
