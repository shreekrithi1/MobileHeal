import XCTest
@testable import MobileHealKit

final class AppRulesTests: XCTestCase {
    func testDisplayFieldsAlwaysStartWithNameAndEmail() {
        let rules = AppRules(fields: [FieldRule(field: "phone_number", required: true), FieldRule(field: "email", required: true)], ui: [:])
        XCTAssertEqual(rules.displayFields, ["name", "email", "phone_number"])
        XCTAssertEqual(rules.requiredFields, ["phone_number", "email"])
    }

    func testAfterSaveNavigation() {
        XCTAssertEqual(AppRules(fields: [], ui: [:]).afterSave, .stay)
        XCTAssertEqual(AppRules(fields: [], ui: ["after_save": "success_screen"]).afterSave, .navigate(screenID: AppRules.success))
        let rules = AppRules(fields: [], ui: ["after_save": "welcome_back", "screen.welcome_back.title": "Welcome back"])
        XCTAssertEqual(rules.afterSave, .navigate(screenID: "welcome_back"))
        XCTAssertEqual(rules.screen("welcome_back").title, "Welcome back")
        XCTAssertEqual(AppRules.empty.screen("order_summary").title, "Order Summary")
    }
}

final class MappingTests: XCTestCase {
    func testProfileJSON() throws {
        let json = #"{"id":1,"name":"Jane","email":"j@x.io","phone_number":null,"updated_at":"t","missing":["phone_number"]}"#
        let out = try Mapping.saveOutcome(from: Data(json.utf8))
        XCTAssertEqual(out.profile, Profile(id: 1, fields: ["name": "Jane", "email": "j@x.io"]))
        XCTAssertEqual(out.missing, ["phone_number"])
    }

    func testLiveEvents() {
        XCTAssertEqual(Mapping.liveEvent(from: #"{"type":"HEAL_REQUIRED","missing":["email"],"issues":{"email":"not valid"}}"#),
                       .healRequired(missing: ["email"], reasons: ["email": "not valid"]))
        XCTAssertEqual(Mapping.liveEvent(from: #"{"type":"HEAL_RESOLVED"}"#), .healResolved)
        let config = #"{"type":"CONFIG_UPDATED","rules":[{"field":"phone_number","constraint":"required"}],"ui":{"button_color":"#079455"}}"#
        XCTAssertEqual(Mapping.liveEvent(from: config),
                       .rulesUpdated(AppRules(fields: [FieldRule(field: "phone_number", required: true)], ui: ["button_color": "#079455"])))
        XCTAssertNil(Mapping.liveEvent(from: #"{"type":"SOMETHING_ELSE"}"#))
        XCTAssertNil(Mapping.liveEvent(from: "not json"))
    }
}

final class ValidatorTests: XCTestCase {
    func testMissingAndFormat() {
        let rules = AppRules(fields: [FieldRule(field: "phone_number", required: true)], ui: [:])
        let v = ProfileValidator()
        XCTAssertEqual(v.missing(["phone_number": "  "], rules: rules), ["phone_number"])
        let issues = v.formatIssues(["email": "dee-at-x", "phone_number": "12"])
        XCTAssertEqual(Set(issues.keys), ["email", "phone_number"])
        XCTAssertTrue(v.formatIssues(["email": "a@b.co", "phone_number": "+1 415 555 0100"]).isEmpty)
    }
}

final class ApiFailureReporterTests: XCTestCase {
    func testPayloadCarriesIncidentKeyFromThe500Body() throws {
        let req = URLRequest(url: URL(string: "http://localhost:8000/api/profiles/3/contact")!)
        let p = ApiFailureReporter.payload(request: req, status: 500,
                                           body: #"{"detail":"Internal error","incident":"INC-7"}"#, device: "iPhone")
        XCTAssertEqual(p["platform"] as? String, "ios")
        XCTAssertEqual(p["endpoint"] as? String, "/api/profiles/3/contact")
        XCTAssertEqual(p["status"] as? Int, 500)
        XCTAssertEqual(p["incident"] as? String, "INC-7")
    }
}
