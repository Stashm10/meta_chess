// KnightSight — © 2026 KnightSight. All rights reserved.

import AVFoundation
import Observation
import SwiftUI
import UIKit
import WebKit

/// Hosts the KnightSight play page and feeds it glasses frames. The page does all the work
/// (board reading, move tracking, Stockfish) on the phone; phrases come back to be spoken here,
/// so they play in the glasses.
@Observable
@MainActor
final class WebLink: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
  static let defaultAddress = "https://stashm10.github.io/meta_chess/docs/play.html"

  /// The play page. Changeable under Details, e.g. to a Mac running the site locally.
  var address: String {
    didSet { UserDefaults.standard.set(address, forKey: "siteAddress") }
  }
  var pageReady = false
  var status = "idle"
  var headline = ""
  var bestMove: String?
  var lastSpoken: String?
  var error: String?
  var framesSent = 0

  @ObservationIgnored let webView: WKWebView
  @ObservationIgnored private let voice = Voice()
  @ObservationIgnored private var inFlight = false

  override init() {
    address = UserDefaults.standard.string(forKey: "siteAddress") ?? Self.defaultAddress
    let config = WKWebViewConfiguration()
    config.allowsInlineMediaPlayback = true
    config.mediaTypesRequiringUserActionForPlayback = []
    webView = WKWebView(frame: .zero, configuration: config)
    super.init()
    config.userContentController.add(WeakHandler(self), name: "knightsight")
    webView.navigationDelegate = self
    webView.isInspectable = true
    webView.scrollView.contentInsetAdjustmentBehavior = .never
    load()
  }

  func load() {
    pageReady = false
    error = nil
    guard var parts = URLComponents(string: address) else {
      error = "That isn't a web address"
      return
    }
    var items = parts.queryItems ?? []
    items.removeAll { $0.name == "source" }
    items.append(URLQueryItem(name: "source", value: "glasses"))
    parts.queryItems = items
    guard let url = parts.url else { return }
    webView.load(URLRequest(url: url))
  }

  /// One glasses frame. Skipped while the page is still busy with the previous one.
  func push(_ image: UIImage) {
    guard pageReady, !inFlight, let jpeg = image.jpegData(compressionQuality: 0.7) else { return }
    inFlight = true
    let frame = "data:image/jpeg;base64," + jpeg.base64EncodedString()
    Task {
      defer { inFlight = false }
      // Waits for the page to finish reading the frame, so frames never queue up.
      _ = try? await webView.callAsyncJavaScript(
        "return await window.KnightSight.pushFrame(frame)", arguments: ["frame": frame], contentWorld: .page)
      framesSent += 1
    }
  }

  func command(_ name: String) {
    webView.evaluateJavaScript("window.KnightSight.command('\(name)')")
  }

  func testVoice() { speak("Knight to f3") }

  private func speak(_ text: String) {
    voice.speak(text)
    lastSpoken = text
  }

  // MARK: - Messages from the page

  func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
    guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
    switch type {
    case "ready": pageReady = true
    case "speak": if let text = body["text"] as? String { speak(text) }
    case "state":
      status = body["status"] as? String ?? status
      headline = body["headline"] as? String ?? headline
      bestMove = body["best"] as? String
    default: break
    }
  }

  func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
    self.error = "Couldn't load KnightSight: \(error.localizedDescription)"
  }

  func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
    self.error = "Couldn't load KnightSight: \(error.localizedDescription)"
  }
}

/// WKUserContentController keeps its handlers alive; this breaks the cycle with WebLink.
private final class WeakHandler: NSObject, WKScriptMessageHandler {
  weak var target: WKScriptMessageHandler?

  init(_ target: WKScriptMessageHandler) { self.target = target }

  func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
    target?.userContentController(controller, didReceive: message)
  }
}

/// The play page, full size.
struct PlayPage: UIViewRepresentable {
  let web: WebLink

  func makeUIView(context: Context) -> WKWebView { web.webView }
  func updateUIView(_ view: WKWebView, context: Context) {}
}

/// Speaks through whatever audio output is active: the glasses when they are connected.
@MainActor
final class Voice {
  private let synth = AVSpeechSynthesizer()

  func speak(_ text: String) {
    let audio = AVAudioSession.sharedInstance()
    try? audio.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
    try? audio.setActive(true)
    let utterance = AVSpeechUtterance(string: text)
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate
    synth.speak(utterance)
  }
}
