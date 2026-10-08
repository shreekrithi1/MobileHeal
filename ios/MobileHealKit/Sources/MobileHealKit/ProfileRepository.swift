import Foundation

/// Abstraction so view models stay unit-testable.
public protocol ProfileRepository: Sendable {
    func load(id: Int) async throws -> SaveOutcome
    func save(id: Int?, fields: [String: String]) async throws -> SaveOutcome
}

/// REST implementation against the MobileHeal backend.
public struct RemoteProfileRepository: ProfileRepository {
    private let baseURL: URL
    private let session: URLSession

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    public func load(id: Int) async throws -> SaveOutcome {
        let outcome = try await send(URLRequest(url: baseURL.appendingPathComponent("api/profiles/\(id)")))
        // Contact card is non-critical: a 5xx is reported to MobileHeal (see `ApiFailureReporter`) and healed server-side.
        _ = try? await raw(URLRequest(url: baseURL.appendingPathComponent("api/profiles/\(id)/contact")))
        return outcome
    }

    public func save(id: Int?, fields: [String: String]) async throws -> SaveOutcome {
        var req = URLRequest(url: baseURL.appendingPathComponent(id.map { "api/profiles/\($0)" } ?? "api/profiles"))
        req.httpMethod = id == nil ? "POST" : "PUT"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: fields)
        return try await send(req)
    }

    private func send(_ request: URLRequest) async throws -> SaveOutcome {
        try Mapping.saveOutcome(from: try await raw(request))
    }

    private func raw(_ request: URLRequest) async throws -> Data {
        var request = request
        request.setValue("ios", forHTTPHeaderField: "X-MobileHeal-Client")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw MobileHealError.offline
        }
        guard let http = response as? HTTPURLResponse else { throw MobileHealError.badResponse("not HTTP") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            if http.statusCode >= 500 {
                await ApiFailureReporter(baseURL: baseURL, session: session).report(request: request, status: http.statusCode, body: body)
            }
            throw MobileHealError.http(http.statusCode, body)
        }
        return data
    }
}

/// Reports API failures (HTTP 5xx) to MobileHeal (`POST /api/client-errors`). The app's report starts the
/// auto-heal: MobileHeal links it to the server-side incident (key from the 500 body) and opens the defect.
public struct ApiFailureReporter: Sendable {
    let baseURL: URL
    let session: URLSession

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    public static func payload(request: URLRequest, status: Int, body: String, device: String) -> [String: Any] {
        let incident = (try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any])?["incident"] as? String
        var endpoint = request.url?.path ?? ""
        if let q = request.url?.query { endpoint += "?" + q }
        var p: [String: Any] = ["platform": "ios", "method": request.httpMethod ?? "GET", "endpoint": endpoint,
                                "status": status, "body": String(body.prefix(2000)), "device": device, "screen": "Profile"]
        if let incident { p["incident"] = incident }
        return p
    }

    public func report(request: URLRequest, status: Int, body: String) async {
        guard request.url?.path != "/api/client-errors" else { return }
        var req = URLRequest(url: baseURL.appendingPathComponent("api/client-errors"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("ios", forHTTPHeaderField: "X-MobileHeal-Client")
        req.httpBody = try? JSONSerialization.data(withJSONObject: Self.payload(
            request: request, status: status, body: body, device: ProcessInfo.processInfo.hostName))
        _ = try? await session.data(for: req)
    }
}

/// Live updates over `/ws/notifications`, reconnecting with back-off. Finishes when the task is cancelled.
public struct LiveUpdates: Sendable {
    private let url: URL

    public init(baseURL: URL, profileID: Int) {
        var c = URLComponents(url: baseURL.appendingPathComponent("ws/notifications"), resolvingAgainstBaseURL: false)!
        c.scheme = baseURL.scheme == "https" ? "wss" : "ws"
        c.queryItems = [URLQueryItem(name: "profile_id", value: String(profileID))]
        url = c.url!
    }

    public func events() -> AsyncStream<LiveEvent> {
        AsyncStream { continuation in
            let task = Task {
                var delay: UInt64 = 1
                while !Task.isCancelled {
                    let socket = URLSession.shared.webSocketTask(with: url)
                    socket.resume()
                    var announced = false
                    do {
                        while !Task.isCancelled {
                            let message = try await socket.receive()
                            if !announced { continuation.yield(.connectionChanged(true)); announced = true; delay = 1 }
                            if case .string(let text) = message, let event = Mapping.liveEvent(from: text) {
                                continuation.yield(event)
                            }
                        }
                    } catch {
                        socket.cancel(with: .goingAway, reason: nil)
                        continuation.yield(.connectionChanged(false))
                    }
                    try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                    delay = min(delay * 2, 15)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
