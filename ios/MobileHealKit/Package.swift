// swift-tools-version: 5.9
// MobileHealKit — platform-independent core of the iOS app (domain, data, live updates).
// Pure Foundation, so `swift test` runs on any Mac without a simulator.
import PackageDescription

let package = Package(
    name: "MobileHealKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "MobileHealKit", targets: ["MobileHealKit"]),
    ],
    targets: [
        .target(name: "MobileHealKit"),
        .testTarget(name: "MobileHealKitTests", dependencies: ["MobileHealKit"]),
    ]
)
