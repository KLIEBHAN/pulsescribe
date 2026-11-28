// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PulseScribeFluidBridge",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(
            name: "PulseScribeFluidBridge",
            type: .static,
            targets: ["PulseScribeFluidBridge"]
        ),
    ],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.18.4"),
    ],
    targets: [
        .target(
            name: "PulseScribeFluidBridge",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio"),
            ]
        ),
    ]
)
