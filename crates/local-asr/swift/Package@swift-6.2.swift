// swift-tools-version: 6.2
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
        // Disable optional TTS/Diarizer/NemoTextProcessing traits on Swift 6.2+ so only
        // CoreML/ANE ASR code is compiled and linked into PulseScribe.
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.18.4", traits: []),
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
