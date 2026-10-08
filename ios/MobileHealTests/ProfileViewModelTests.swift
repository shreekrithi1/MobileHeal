import MobileHealKit
import XCTest
@testable import MobileHeal

private struct FakeRepository: ProfileRepository {
    var stored = Profile(id: 1, fields: ["name": "Jane", "email": "jane@example.com"])
    var missingAfterSave: [String] = []
    func load(id: Int) async throws -> SaveOutcome { SaveOutcome(profile: stored, missing: []) }
    func save(id: Int?, fields: [String: String]) async throws -> SaveOutcome {
        SaveOutcome(profile: Profile(id: 1, fields: fields), missing: missingAfterSave)
    }
}

@MainActor
private final class FakeAlerts: HealAlerts {
    var shown: [String] = []
    func show(missing: [String], reasons: [String: String]) { shown = missing }
    func clear() { shown = [] }
}

@MainActor
final class ProfileViewModelTests: XCTestCase {
    private func model(_ rules: AppRules, alerts: FakeAlerts = FakeAlerts()) -> ProfileViewModel {
        ProfileViewModel(profileID: 1, repository: FakeRepository(), live: nil, alerts: alerts, rules: rules)
    }

    func testRequiredFieldBlocksSave() async {
        let vm = model(AppRules(fields: [FieldRule(field: "phone_number", required: true)], ui: [:]))
        await vm.load()
        await vm.save()
        guard case .content(let c) = vm.state else { return XCTFail("expected content") }
        XCTAssertEqual(c.missing, ["phone_number"])
        XCTAssertNil(vm.destination)
    }

    func testSaveNavigatesToAfterSaveScreen() async {
        let vm = model(AppRules(fields: [], ui: ["after_save": "welcome_back"]))
        await vm.load()
        await vm.save()
        XCTAssertEqual(vm.destination, "welcome_back")
    }

    func testHealRequiredHighlightsFieldsAndNotifies() async {
        let alerts = FakeAlerts()
        let vm = model(.empty, alerts: alerts)
        await vm.load()
        vm.handle(.healRequired(missing: ["phone_number"], reasons: ["phone_number": "“12” isn't a valid phone number"]))
        guard case .content(let c) = vm.state else { return XCTFail("expected content") }
        XCTAssertEqual(c.values["phone_number"], "")
        XCTAssertEqual(c.reasons["phone_number"], "“12” isn't a valid phone number")
        XCTAssertEqual(alerts.shown, ["phone_number"])
    }

    func testRulesUpdatedLive() async {
        let vm = model(.empty)
        await vm.load()
        vm.handle(.rulesUpdated(AppRules(fields: [FieldRule(field: "city", required: true)], ui: [:])))
        guard case .content(let c) = vm.state else { return XCTFail("expected content") }
        XCTAssertTrue(c.fields.contains("city"))
    }
}
