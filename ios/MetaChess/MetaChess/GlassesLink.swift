import MWDATCamera
import MWDATCore
import Observation
import UIKit
import os

/// Registration with Meta AI, the device session, and the glasses camera stream.
@Observable
@MainActor
final class GlassesLink {
  var registration: RegistrationState
  var deviceCount = 0
  var needsFirmwareUpdate = false
  var sessionState: DeviceSessionState = .idle
  var streamState: StreamState = .stopped
  var frames = 0
  var fps = 0.0
  var frameSize: CGSize?
  var preview: UIImage?
  var message: String?
  /// True from Start until Stop, or until the glasses end the session or the stream.
  private(set) var isRunning = false

  /// Called on the main actor with at most a few frames per second.
  @ObservationIgnored var onFrame: ((UIImage) -> Void)?

  @ObservationIgnored private let wearables: WearablesInterface
  @ObservationIgnored private let selector: AutoDeviceSelector
  @ObservationIgnored private var session: DeviceSession?
  @ObservationIgnored private var camera: MWDATCamera.Camera?
  @ObservationIgnored private let sessionTokens = ListenerTokenBag()
  @ObservationIgnored private let streamTokens = ListenerTokenBag()
  @ObservationIgnored private var compatibilityTokens: [AnyListenerToken] = []
  @ObservationIgnored private var tasks: [Task<Void, Never>] = []
  @ObservationIgnored private var lastFrameAt: Date?
  @ObservationIgnored private var hasStreamed = false
  /// Frames arrive off the main actor at the stream rate; only every ~1/3 s one is decoded and forwarded.
  @ObservationIgnored private nonisolated let gate = OSAllocatedUnfairLock(initialState: Date.distantPast)
  nonisolated static let forwardInterval: TimeInterval = 1.0 / 3.0

  init(wearables: WearablesInterface) {
    self.wearables = wearables
    self.selector = AutoDeviceSelector(wearables: wearables)
    self.registration = wearables.registrationState
    tasks.append(Task { [weak self] in
      for await state in wearables.registrationStateStream() { self?.registration = state }
    })
    tasks.append(Task { [weak self] in
      for await devices in wearables.devicesStream() { self?.devicesChanged(devices) }
    })
  }

  var isRegistered: Bool { registration == .registered }

  // MARK: - Registration

  func connect() {
    Task {
      do {
        try await wearables.startRegistration()
      } catch {
        message = "Connect failed: \(error.localizedDescription)"
      }
    }
  }

  func disconnect() {
    stop()
    Task {
      do {
        try await wearables.startUnregistration()
      } catch {
        message = "Disconnect failed: \(error.localizedDescription)"
      }
    }
  }

  func openFirmwareUpdate() {
    Task { try? await wearables.openFirmwareUpdate() }
  }

  private func devicesChanged(_ devices: [DeviceIdentifier]) {
    deviceCount = devices.count
    compatibilityTokens.removeAll()
    needsFirmwareUpdate = false
    for id in devices {
      guard let device = wearables.deviceForIdentifier(id) else { continue }
      if device.compatibility() == .deviceUpdateRequired { needsFirmwareUpdate = true }
      compatibilityTokens.append(device.addCompatibilityListener { [weak self] compatibility in
        Task { @MainActor in
          if compatibility == .deviceUpdateRequired { self?.needsFirmwareUpdate = true }
        }
      })
    }
  }

  // MARK: - Session and camera

  /// Start the device session, ask for camera permission if needed, then stream.
  func start() async {
    guard session == nil else { return }
    message = nil
    do throws(DeviceSessionError) {
      let newSession = try wearables.createSession(deviceSelector: selector)
      session = newSession
      isRunning = true
      newSession.statePublisher.listen { [weak self] state in
        Task { @MainActor in self?.sessionChanged(state) }
      }.store(in: sessionTokens)
      newSession.errorPublisher.listen { [weak self] error in
        Task { @MainActor in self?.sessionFailed(error) }
      }.store(in: sessionTokens)
      sessionState = .starting
      try newSession.start()
    } catch {
      sessionFailed(error)
      cleanupSession()
    }
  }

  /// Stops at once, without waiting for the glasses to confirm, so Start is available again right away.
  func stop() {
    camera?.stop()
    session?.stop()
    cleanupStream()
    cleanupSession()
    sessionState = .stopped
  }

  private func sessionChanged(_ state: DeviceSessionState) {
    sessionState = state
    switch state {
    case .started:
      Task { await startCamera() }
    case .stopped:
      cleanupStream()
      cleanupSession()
    default:
      break
    }
  }

  private func sessionFailed(_ error: DeviceSessionError) {
    if case .datAppOnTheGlassesUpdateRequired = error {
      message = "The glasses need an app update: opening Meta AI"
      Task { try? await wearables.openDATGlassesAppUpdate() }
      return
    }
    message = "Session: \(error.localizedDescription)"
  }

  private func startCamera() async {
    guard let session, camera == nil, session.state == .started else { return }
    do {
      if try await wearables.checkPermissionStatus(.camera) != .granted {
        message = "Allow camera access in Meta AI, then come back"
        guard try await wearables.requestPermission(.camera) == .granted else {
          message = "Camera permission was not granted"
          return
        }
        message = nil
      }
      // Lower frame rates give sharper frames over Bluetooth; we only need ~3 per second.
      let config = StreamConfiguration(videoCodec: .raw, resolution: .high, frameRate: 7)
      guard let newCamera = try session.addCamera(config: config) else {
        message = "Couldn't create the camera"
        return
      }
      camera = newCamera
      listen(to: newCamera.stream)
      streamState = .starting
      newCamera.stream.start()
    } catch {
      message = "Camera: \(error.localizedDescription)"
    }
  }

  private func listen(to stream: MWDATCamera.Stream) {
    stream.statePublisher.listen { [weak self] state in
      Task { @MainActor in self?.streamChanged(state) }
    }.store(in: streamTokens)

    stream.errorPublisher.listen { [weak self] error in
      Task { @MainActor in self?.message = "Stream: \(error.localizedDescription)" }
    }.store(in: streamTokens)

    let gate = self.gate
    stream.videoFramePublisher.listen { [weak self] frame in
      let now = Date()
      let due = gate.withLock { last -> Bool in
        guard now.timeIntervalSince(last) >= Self.forwardInterval else { return false }
        last = now
        return true
      }
      let image = due ? frame.makeUIImage() : nil
      Task { @MainActor in self?.frameArrived(image, at: now) }
    }.store(in: streamTokens)
  }

  private func streamChanged(_ state: StreamState) {
    streamState = state
    if state == .streaming { hasStreamed = true }
    // The stream ended on the glasses' side (taken off, or out of range): stop fully so Start works again.
    // Only after it has streamed, so a .stopped reported while starting up doesn't cancel the start.
    if state == .stopped && hasStreamed { stop() }
  }

  private func frameArrived(_ image: UIImage?, at time: Date) {
    frames += 1
    if let last = lastFrameAt {
      let dt = time.timeIntervalSince(last)
      if dt > 0 { fps = fps == 0 ? 1 / dt : 0.9 * fps + 0.1 / dt }
    }
    lastFrameAt = time
    guard let image else { return }
    preview = image
    frameSize = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
    onFrame?(image)
  }

  private func cleanupStream() {
    streamTokens.clear()
    camera = nil
    hasStreamed = false
    streamState = .stopped
    preview = nil
    fps = 0
    lastFrameAt = nil
  }

  private func cleanupSession() {
    sessionTokens.clear()
    session = nil
    isRunning = false
    if sessionState != .stopped { sessionState = .idle }
  }
}
