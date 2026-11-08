// KnightSight — © 2026 KnightSight. All rights reserved.

import MWDATCore
import SwiftUI

@main
struct MetaChessApp: App {
  @State private var glasses: GlassesLink
  @State private var web = WebLink()

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
      ContentView(glasses: glasses, web: web)
        .onAppear { glasses.onFrame = { image in web.push(image) } }
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
