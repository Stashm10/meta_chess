import MWDATCamera
import MWDATCore
import SwiftUI

/// The phone screen during a game: one status line, Start/Stop, and a few game controls.
/// Everything else (board view, move list) lives on the Mac dashboard.
struct ContentView: View {
  let glasses: GlassesLink
  @Bindable var mac: MacLink
  @State private var showDetails = false

  private var running: Bool { glasses.isRunning || mac.sending }

  var body: some View {
    NavigationStack {
      VStack(spacing: 20) {
        statusChips

        Spacer()

        VStack(spacing: 8) {
          Text(running ? mac.game.headline : "Ready")
            .font(.system(size: 34, weight: .bold))
            .multilineTextAlignment(.center)
            .minimumScaleFactor(0.6)
          if let hint = mac.game.hint, running {
            Text(hint)
              .font(.title3)
              .foregroundStyle(.orange)
              .multilineTextAlignment(.center)
          }
          if let color = mac.game.userColor, running {
            Text("You are \(color)").foregroundStyle(.secondary)
          }
          if let message = glasses.message ?? mac.error {
            Text(message)
              .font(.callout)
              .foregroundStyle(.red)
              .multilineTextAlignment(.center)
          }
        }
        .padding(.horizontal)

        Spacer()

        mainButton

        HStack(spacing: 12) {
          control("New game", "arrow.counterclockwise") { mac.newGame() }
          control("Repeat", "speaker.wave.2") { mac.repeatLast() }
          control("Undo", "arrow.uturn.backward") { mac.undo() }
        }
        .disabled(!mac.connected)
      }
      .padding()
      .navigationTitle("Meta Chess")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        Button { showDetails = true } label: { Image(systemName: "info.circle") }
      }
      .sheet(isPresented: $showDetails) {
        DetailsView(glasses: glasses, mac: mac)
      }
    }
    // Streaming may stop when the phone locks, so keep the screen on while running.
    .onChange(of: running) { _, on in
      UIApplication.shared.isIdleTimerDisabled = on
    }
  }

  private var statusChips: some View {
    HStack {
      chip("eyeglasses", glassesText, ok: glasses.streamState == .streaming)
      chip("desktopcomputer", macText, ok: mac.connected)
    }
  }

  private var glassesText: String {
    if !glasses.isRegistered { return "Not connected" }
    if glasses.needsFirmwareUpdate { return "Update needed" }
    switch glasses.streamState {
    case .streaming: return "Camera on"
    case .starting, .waitingForDevice: return "Starting…"
    case .paused: return "Paused"
    default: return glasses.deviceCount > 0 ? "Ready" : "Not found"
    }
  }

  private var macText: String {
    if mac.connected { return "Connected" }
    return mac.address.isEmpty ? "Searching…" : "Found"
  }

  @ViewBuilder private var mainButton: some View {
    if !glasses.isRegistered {
      big("Connect glasses", .blue) { glasses.connect() }
    } else if glasses.needsFirmwareUpdate {
      big("Update glasses", .orange) { glasses.openFirmwareUpdate() }
    } else if running {
      big("Stop", .red) {
        glasses.stop()
        mac.stop()
      }
    } else {
      big("Start", .green) {
        mac.start()
        Task { await glasses.start() }
      }
    }
  }

  private func big(_ title: String, _ color: Color, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(title)
        .font(.title2.bold())
        .frame(maxWidth: .infinity, minHeight: 64)
    }
    .buttonStyle(.borderedProminent)
    .tint(color)
  }

  private func control(_ title: String, _ icon: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Label(title, systemImage: icon)
        .labelStyle(.titleAndIcon)
        .font(.subheadline)
        .frame(maxWidth: .infinity, minHeight: 44)
    }
    .buttonStyle(.bordered)
  }

  private func chip(_ icon: String, _ text: String, ok: Bool) -> some View {
    Label(text, systemImage: icon)
      .font(.subheadline)
      .padding(.horizontal, 12)
      .padding(.vertical, 8)
      .frame(maxWidth: .infinity)
      .background((ok ? Color.green : Color.secondary).opacity(0.15), in: Capsule())
      .foregroundStyle(ok ? .green : .secondary)
  }
}

/// Diagnostics: the camera preview, frame counts, the Mac address, and the voice test.
struct DetailsView: View {
  let glasses: GlassesLink
  @Bindable var mac: MacLink
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    NavigationStack {
      Form {
        if let image = glasses.preview {
          Section {
            Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 320)
          }
        }
        Section("Glasses") {
          LabeledContent("Session", value: "\(glasses.sessionState)")
          LabeledContent("Camera", value: "\(glasses.streamState)")
          LabeledContent("Frames", value: "\(glasses.frames) · \(String(format: "%.1f", glasses.fps)) fps")
          if let size = glasses.frameSize {
            LabeledContent("Frame size", value: "\(Int(size.width)) × \(Int(size.height))")
          }
          if glasses.isRegistered {
            Button("Disconnect glasses", role: .destructive) { glasses.disconnect() }
          }
        }
        Section {
          TextField("Mac address (host:port)", text: $mac.address)
            .keyboardType(.URL)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
          LabeledContent("Found automatically", value: mac.discovered ? "yes" : "no")
          LabeledContent("Frames sent", value: "\(mac.sent)")
          Button("Search again") { mac.discover() }
        } header: {
          Text("Mac")
        } footer: {
          Text("Found automatically when the Mac server is running on the same Wi-Fi. Type it only if that fails.")
        }
        Section("Voice") {
          Button("Test voice: \"Knight to f3\"") { mac.testVoice() }
          if let spoken = mac.lastSpoken {
            LabeledContent("Last spoken", value: spoken)
          }
        }
      }
      .navigationTitle("Details")
      .toolbar { Button("Done") { dismiss() } }
    }
  }
}
