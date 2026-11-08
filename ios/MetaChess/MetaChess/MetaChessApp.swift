import MWDATCore
import SwiftUI

@main
struct MetaChessApp: App {
  @State private var glasses: GlassesLink
  @State private var mac = MacLink()

  init() {
    do {
      try Wearables.configure()
    } catch {
      NSLog("[MetaChess] Wearables.configure failed: \(error)")
    }
    _glasses = State(wrappedValue: GlassesLink(wearables: Wearables.shared))
  }

  var body: some Scene {
    WindowGroup {
      ContentView(glasses: glasses, mac: mac)
        .onAppear { glasses.onFrame = { image in mac.offer(image) } }
        .onOpenURL { url in
          // Meta AI calls back here after registration and permission prompts.
          guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
          else { return }
          Task {
            do {
              _ = try await Wearables.shared.handleUrl(url)
            } catch {
              glasses.message = "Meta AI callback failed: \(error.localizedDescription)"
            }
          }
        }
    }
  }
}
