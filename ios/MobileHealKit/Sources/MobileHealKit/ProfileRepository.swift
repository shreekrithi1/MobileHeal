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
        try await send(URLRequest(url: baseURL.appendingPathComponent("api/profiles/\(id)")))
    }

    public func save(id: Int?, fields: [String: String]) async throws -> SaveOutcome {
        var req = URLRequest(url: baseURL.appendingPathComponent(id.map { "api/profiles/\($0)" } ?? "api/profiles"))
        req.httpMethod = id == nil ? "POST" : "PUT"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: fields)
        return try await send(req)
    }

    private func send(_ request: URLRequest) async throws -> SaveOutcome {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw MobileHealError.offline
        }
        guard let http = response as? HTTPURLResponse else { throw MobileHealError.badResponse("not HTTP") }
        guard (200..<300).contains(http.statusCode) else {
            throw MobileHealError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        return try Mapping.saveOutcome(from: data)
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
