import Darwin
import FluidAudio
import Foundation

/// Minimal thread-safe result box for bridging Swift `async throws` calls back to
/// synchronous Rust FFI callers running on dedicated blocking OS threads.
private final class LockedResult<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Result<T, Error>?

    func store(_ value: Result<T, Error>) {
        lock.lock()
        result = value
        lock.unlock()
    }

    func take() -> Result<T, Error> {
        lock.lock()
        defer { lock.unlock() }
        return result ?? .failure(NSError(domain: "PulseScribeFluidBridge", code: -1))
    }
}

private func runBlocking<T: Sendable>(
    _ operation: @escaping @Sendable () async throws -> T
) throws -> T {
    let semaphore = DispatchSemaphore(value: 0)
    let box = LockedResult<T>()
    Task.detached(priority: .userInitiated) {
        do {
            let value = try await operation()
            box.store(.success(value))
        } catch {
            box.store(.failure(error))
        }
        semaphore.signal()
    }
    semaphore.wait()
    return try box.take().get()
}

private func writeError(
    _ errOut: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
    _ message: String
) {
    guard let errOut else { return }
    errOut.pointee = strdup(message)
}

private final class FluidParakeetBox: @unchecked Sendable {
    let manager: AsrManager
    let decoderLayers: Int

    init(
        modelDir: URL,
        version: AsrModelVersion,
        encoderPrecision: ParakeetEncoderPrecision
    ) throws {
        let models = try AsrModels.loadLocal(
            from: modelDir,
            version: version,
            encoderPrecision: encoderPrecision
        )
        let manager = AsrManager(config: .default)
        try runBlocking {
            try await manager.loadModels(models)
        }
        self.manager = manager
        self.decoderLayers = version.decoderLayers
    }

    func transcribe(samples: [Float]) throws -> String {
        if samples.isEmpty {
            return ""
        }
        let mgr = self.manager
        let layers = self.decoderLayers
        return try runBlocking {
            var state = TdtDecoderState.make(decoderLayers: layers)
            let result = try await mgr.transcribe(samples, decoderState: &state)
            return result.text
        }
    }

    func cleanup() {
        let mgr = self.manager
        _ = try? runBlocking {
            await mgr.cleanup()
        }
    }
}

/// Initialize a local CoreML/ANE Parakeet engine from an already-downloaded directory.
///
/// `versionCode`: `1` for `parakeet-ultra` (`.ultra`), `0` for `parakeet-v3` (`.v3` + `.int8V2`).
/// Returns an opaque handle owned by the caller (release via `ps_fluid_destroy`), or `nil` on error.
@_cdecl("ps_fluid_init_parakeet")
public func ps_fluid_init_parakeet(
    _ modelDirPtr: UnsafePointer<CChar>?,
    _ versionCode: Int32,
    _ errOut: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> UnsafeMutableRawPointer? {
    guard let modelDirPtr else {
        writeError(errOut, "model directory pointer is null")
        return nil
    }
    let modelDir = String(cString: modelDirPtr)
    let version: AsrModelVersion
    let precision: ParakeetEncoderPrecision
    switch versionCode {
    case 1:
        version = .ultra
        precision = .int8
    case 0:
        version = .v3
        precision = .int8V2
    default:
        writeError(errOut, "unsupported FluidAudio Parakeet version code: \(versionCode)")
        return nil
    }

    do {
        let box = try FluidParakeetBox(
            modelDir: URL(fileURLWithPath: modelDir, isDirectory: true),
            version: version,
            encoderPrecision: precision
        )
        return Unmanaged.passRetained(box).toOpaque()
    } catch {
        writeError(errOut, "FluidAudio load failed: \(error.localizedDescription)")
        return nil
    }
}

/// Transcribe mono 16 kHz `f32` audio samples with a loaded CoreML/ANE Parakeet handle.
///
/// Returns `0` on success with a `strdup`-allocated UTF-8 string in `textOut` (free via
/// `ps_fluid_free_string`), or non-zero with an error message in `errOut`.
@_cdecl("ps_fluid_transcribe")
public func ps_fluid_transcribe(
    _ handle: UnsafeMutableRawPointer?,
    _ samplesPtr: UnsafePointer<Float>?,
    _ sampleCount: Int32,
    _ textOut: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
    _ errOut: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
    guard let handle, let textOut else {
        writeError(errOut, "invalid null argument to ps_fluid_transcribe")
        return -1
    }
    if sampleCount < 0 {
        writeError(errOut, "negative sample count: \(sampleCount)")
        return -1
    }
    let samples: [Float]
    if sampleCount == 0 {
        samples = []
    } else if let samplesPtr {
        let buffer = UnsafeBufferPointer(start: samplesPtr, count: Int(sampleCount))
        samples = Array(buffer)
    } else {
        writeError(errOut, "samples pointer is null for non-empty buffer")
        return -1
    }

    let box = Unmanaged<FluidParakeetBox>.fromOpaque(handle).takeUnretainedValue()
    do {
        let text = try box.transcribe(samples: samples)
        textOut.pointee = strdup(text)
        return 0
    } catch {
        writeError(errOut, "FluidAudio transcription failed: \(error.localizedDescription)")
        return -2
    }
}

/// Release a handle previously returned by `ps_fluid_init_parakeet`.
@_cdecl("ps_fluid_destroy")
public func ps_fluid_destroy(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else { return }
    let box = Unmanaged<FluidParakeetBox>.fromOpaque(handle).takeRetainedValue()
    box.cleanup()
}

/// Free a C string allocated by this bridge (`textOut` or `errOut`).
@_cdecl("ps_fluid_free_string")
public func ps_fluid_free_string(_ ptr: UnsafeMutablePointer<CChar>?) {
    if let ptr {
        free(ptr)
    }
}
