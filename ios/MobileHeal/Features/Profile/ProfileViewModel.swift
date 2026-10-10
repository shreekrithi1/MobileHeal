import Foundation
import MobileHealKit
import Observation

/// UI state for the profile screen (unidirectional data flow: the view only reads `state` and calls intents).
enum ProfileState: Equatable {
    case loading
    case content(ProfileContent)
    case error(String)
}

struct ProfileContent: Equatable {
    var profileID: Int?
    var values: [String: String]
    var rules: AppRules
    var missing: [String] = []
    var reasons: [String: String] = [:]
    var isSaving = false
    var banner: String?
    var connected = false

    var fields: [String] { rules.displayFields }
    func isRequired(_ field: String) -> Bool { rules.requiredFields.contains(field) }
}

@MainActor
@Observable
final class ProfileViewModel {
    private(set) var state: ProfileState = .loading
    /// Screen to push after a successful save (navigation side effect, consumed by the view).
    var destination: String?

    private let repository: ProfileRepository
    private let live: LiveUpdates?
    private let alerts: HealAlerts
    private let validator = ProfileValidator()
    private let profileID: Int
    private var rules: AppRules
    private var liveTask: Task<Void, Never>?

    init(profileID: Int, repository: ProfileRepository, live: LiveUpdates?, alerts: HealAlerts, rules: AppRules = RulesDefaults.rules) {
        self.profileID = profileID
        self.repository = repository
        self.live = live
        self.alerts = alerts
        self.rules = rules
    }

    // MARK: intents
    func onAppear() async {
        await load()
        observeLiveUpdates()
    }

    func load() async {
        state = .loading
        do {
            let outcome = try await repository.load(id: profileID)
            state = .content(ProfileContent(profileID: outcome.profile.id, values: outcome.profile.fields, rules: rules, missing: outcome.missing))
        } catch MobileHealError.http(404, _) {
            state = .content(ProfileContent(profileID: nil, values: ["name": "Jane Doe", "email": "jane@example.com"], rules: rules))
        } catch {
            state = .error(error.localizedDescription)
        }
    }

    func update(_ field: String, _ value: String) {
        guard case .content(var c) = state else { return }
        c.values[field] = value
        c.reasons[field] = nil
        c.banner = nil
        state = .content(c)
    }

    func save() async {
        guard case .content(var c) = state else { return }
        let missing = validator.missing(c.values, rules: c.rules)
        let issues = validator.formatIssues(c.values)
        guard missing.isEmpty, issues.isEmpty else {
            c.missing = missing
            c.reasons = issues
            // ui.banner_message is the custom *alert* copy; always say which fields are missing
            c.banner = (c.rules.ui["banner_message"].map { $0 + " " } ?? "")
                + "Please complete: " + (missing + issues.keys.sorted()).map(\.titleCased).joined(separator: ", ")
            state = .content(c)
            return
        }
        let phone = (c.values["phone_number"] ?? "").trimmingCharacters(in: .whitespaces)  // MH-DEMO-BUG
        if !phone.isEmpty { c.values["phone_number"] = phone }
        c.isSaving = true
        state = .content(c)
        do {
            let outcome = try await repository.save(id: c.profileID, fields: c.values.filter { !$0.value.isEmpty })
            c.profileID = outcome.profile.id
            c.values = outcome.profile.fields
            c.missing = outcome.missing
            c.isSaving = false
            c.banner = outcome.missing.isEmpty ? "Saved" : nil
            state = .content(c)
            if outcome.missing.isEmpty, case .navigate(let screen) = c.rules.afterSave {
                destination = screen
            }
        } catch {
            c.isSaving = false
            c.banner = error.localizedDescription
            state = .content(c)
        }
    }

    func screen(_ id: String) -> ScreenSpec { rules.screen(id) }
    var currentRules: AppRules { rules }

    // MARK: live updates
    private func observeLiveUpdates() {
        guard liveTask == nil, let live else { return }
        liveTask = Task { [weak self] in
            for await event in live.events() {
                self?.handle(event)
            }
        }
    }

    func handle(_ event: LiveEvent) {
        switch event {
        case .rulesUpdated(let newRules):
            rules = newRules
            if case .content(var c) = state { c.rules = newRules; state = .content(c) }
        case .healRequired(let missing, let reasons):
            if case .content(var c) = state {
                for f in missing where c.values[f] == nil { c.values[f] = "" }
                c.missing = missing
                c.reasons = reasons
                state = .content(c)
            }
            alerts.show(missing: missing, reasons: reasons)
        case .healResolved:
            if case .content(var c) = state { c.missing = []; c.reasons = [:]; state = .content(c) }
            alerts.clear()
        case .connectionChanged(let up):
            if case .content(var c) = state { c.connected = up; state = .content(c) }
        }
    }

    func stopLiveUpdates() {
        liveTask?.cancel()
        liveTask = nil
    }
}
