// KnightSide — © 2026 KnightSide. All rights reserved.

import MWDATCamera
import MWDATCore
import SwiftUI

/// The KnightSide play page (board, best move, moves) with the glasses controls under it.
struct ContentView: View {
  let glasses: GlassesLink
  @Bindable var web: WebLink
  @State private var showDetails = false
  @State private var pocket = false
  @State private var savedBrightness: CGFloat = 0.5

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        PlayPage(web: web)
          .overlay {
            if let error = web.error {
              VStack(spacing: 12) {
                Text(error).multilineTextAlignment(.center)
                Button("Try again") { web.load() }.buttonStyle(.borderedProminent)
              }
              .padding()
              .frame(maxWidth: .infinity, maxHeight: .infinity)
              .background(.background)
            }
          }

        VStack(spacing: 10) {
          if let message = glasses.message {
            Text(message).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
          }
          HStack(spacing: 10) {
            chip("eyeglasses", glassesText, ok: glasses.streamState == .streaming)
            mainButton
          }
          if glasses.isRunning {
            HStack(spacing: 10) {
              control("New game", "arrow.counterclockwise") { web.command("newGame") }
              control("Repeat", "speaker.wave.2") { web.command("repeat") }
              control("Pocket", "moon.fill") { setPocket(true) }
            }
          }
        }
        .padding(12)
        .background(.bar)
      }
      .navigationTitle("KnightSide")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        Button { showDetails = true } label: { Image(systemName: "info.circle") }
      }
      .sheet(isPresented: $showDetails) {
        DetailsView(glasses: glasses, web: web)
      }
    }
    .overlay {
      // Pocket mode: a black screen at minimum brightness while the game runs. Tap to wake.
      if pocket {
        Color.black
          .ignoresSafeArea()
          .overlay(alignment: .bottom) {
            Text("KnightSide is watching. Tap to wake.")
              .font(.footnote)
              .foregroundStyle(.white.opacity(0.25))
              .padding(.bottom, 40)
          }
          .onTapGesture { setPocket(false) }
      }
    }
    .statusBarHidden(pocket)
    // The page stops when the phone locks, so keep the screen on while running.
    .onChange(of: glasses.isRunning) { _, on in
      UIApplication.shared.isIdleTimerDisabled = on
      if !on { setPocket(false) }
    }
  }

  private func setPocket(_ on: Bool) {
    guard on != pocket else { return }
    if on {
      savedBrightness = UIScreen.main.brightness
      UIScreen.main.brightness = 0
    } else {
      UIScreen.main.brightness = savedBrightness
    }
    pocket = on
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

  @ViewBuilder private var mainButton: some View {
    if !glasses.isRegistered {
      big("Connect glasses", .blue) { glasses.connect() }
    } else if glasses.needsFirmwareUpdate {
      big("Update glasses", .orange) { glasses.openFirmwareUpdate() }
    } else if glasses.isRunning {
      big("Stop", .red) { glasses.stop() }
    } else {
      big("Start", .green) { Task { await glasses.start() } }
    }
  }

  private func big(_ title: String, _ color: Color, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(title).font(.headline).frame(maxWidth: .infinity, minHeight: 44)
    }
    .buttonStyle(.borderedProminent)
    .tint(color)
  }

  private func control(_ title: String, _ icon: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Label(title, systemImage: icon).font(.subheadline).frame(maxWidth: .infinity, minHeight: 36)
    }
    .buttonStyle(.bordered)
  }

  private func chip(_ icon: String, _ text: String, ok: Bool) -> some View {
    Label(text, systemImage: icon)
      .font(.subheadline)
      .lineLimit(1)
      .padding(.horizontal, 12)
      .frame(maxWidth: .infinity, minHeight: 44)
      .background((ok ? Color.green : Color.secondary).opacity(0.15), in: Capsule())
      .foregroundStyle(ok ? .green : .secondary)
  }
}

/// Diagnostics: the camera preview, frame counts, the page address, and the voice test.
struct DetailsView: View {
  let glasses: GlassesLink
  @Bindable var web: WebLink
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
          TextField("Page address", text: $web.address)
            .keyboardType(.URL)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
          LabeledContent("Frames read", value: "\(web.framesSent)")
          Button("Reload page") { web.load() }
          Button("Use the default address") {
            web.address = WebLink.defaultAddress
            web.load()
          }
        } header: {
          Text("KnightSide page")
        } footer: {
          Text("The board reading and Stockfish run on this phone, inside the page.")
        }
        Section("Voice") {
          Button("Test voice: \"Knight to f3\"") { web.testVoice() }
          if let spoken = web.lastSpoken {
            LabeledContent("Last spoken", value: spoken)
          }
        }
        Section {
          Text("© 2026 KnightSide. All rights reserved.").font(.footnote).foregroundStyle(.secondary)
        }
      }
      .navigationTitle("Details")
      .toolbar { Button("Done") { dismiss() } }
    }
  }
}
