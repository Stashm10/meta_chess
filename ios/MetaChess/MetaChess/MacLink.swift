import AVFoundation
import Network
import Observation
import UIKit

/// Finds the Mac backend on the Wi-Fi, uploads frames to it, and speaks the phrases it sends back.
@Observable
@MainActor
final class MacLink {
  /// "host:port". Filled in by Bonjour discovery, or typed by hand.
  var address: String {
    didSet { UserDefaults.standard.set(address, forKey: "macAddress") }
  }
  var discovered = false
  var sending = false
  var connected = false
  var sent = 0
  var game = GameSummary()
  var lastSpoken: String?
  var error: String?

  @ObservationIgnored private var inFlight = false
  @ObservationIgnored private var lastAck = 0
  @ObservationIgnored private var browser: NWBrowser?
  @ObservationIgnored private var resolving: NWConnection?
  @ObservationIgnored private let voice = Voice()
  @ObservationIgnored private let http: URLSession = {
    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 4
    return URLSession(configuration: config)
  }()

  init() {
    address = UserDefaults.standard.string(forKey: "macAddress") ?? ""
    discover()
  }

  private func url(_ path: String) -> URL? {
    guard !address.isEmpty else { return nil }
    let base = address.contains("://") ? address : "http://\(address)"
    return URL(string: base + path)
  }

  // MARK: - Finding the Mac

  /// Browse for `_metachess._tcp` and resolve the first result to an IPv4 address.
  func discover() {
    browser?.cancel()
    let browser = NWBrowser(for: .bonjour(type: "_metachess._tcp", domain: nil), using: .tcp)
    browser.browseResultsChangedHandler = { [weak self] results, _ in
      guard let endpoint = results.first?.endpoint else { return }
      Task { @MainActor in self?.resolve(endpoint) }
    }
    browser.start(queue: .main)
    self.browser = browser
  }

  private func resolve(_ endpoint: NWEndpoint) {
    resolving?.cancel()
    let params = NWParameters.tcp
    if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
      ip.version = .v4
    }
    let connection = NWConnection(to: endpoint, using: params)
    connection.stateUpdateHandler = { [weak self] state in
      guard case .ready = state else { return }
      let remote = connection.currentPath?.remoteEndpoint
      connection.cancel()
      guard case .hostPort(let host, let port) = remote else { return }
      var text = "\(host)"
      if let percent = text.firstIndex(of: "%") { text = String(text[..<percent]) }   // drop interface suffix
      let found = "\(text):\(port.rawValue)"
      Task { @MainActor in
        guard let self else { return }
        self.discovered = true
        if self.address != found {
          self.address = found
          if self.sending { await self.hello() }
        }
      }
    }
    connection.start(queue: .main)
    resolving = connection
  }

  // MARK: - Control

  func start() {
    sending = true
    error = nil
    Task { await hello() }
  }

  func stop() {
    sending = false
    connected = false
    Task { _ = try? await post("/phone/stop") }
  }

  func newGame() {
    Task { await command("/game/new") }
  }

  func repeatLast() {
    Task { await command("/speak/repeat") }
  }

  func undo() {
    Task { await command("/game/undo") }
  }

  func testVoice() {
    voice.speak("Knight to f3")
  }

  private func command(_ path: String) async {
    do {
      _ = try await post(path)
      error = nil
    } catch {
      self.error = "Can't reach the Mac"
    }
  }

  private func post(_ path: String, json: [String: Any]? = nil) async throws -> Data {
    guard let url = url(path) else { throw URLError(.badURL) }
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    if let json {
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONSerialization.data(withJSONObject: json)
    }
    let (data, response) = try await http.data(for: request)
    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
    return data
  }

  private func hello() async {
    guard !address.isEmpty else {
      error = "Looking for the Mac… is the server running on the same Wi-Fi?"
      return
    }
    do {
      let data = try await post("/phone/hello", json: ["device": UIDevice.current.name, "app_version": "0.3"])
      handle(data)
      connected = true
      error = nil
    } catch {
      connected = false
      self.error = "Can't reach the Mac at \(address)"
    }
  }

  // MARK: - Frames

  /// Called with each forwarded glasses frame; skipped while a request is still in flight.
  func offer(_ image: UIImage) {
    guard sending, !inFlight, let url = url("/phone/frame"), let jpeg = Self.jpeg(image) else { return }
    inFlight = true
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
    request.setValue(String(lastAck), forHTTPHeaderField: "X-Ack")
    request.httpBody = jpeg
    Task {
      defer { inFlight = false }
      do {
        let (data, response) = try await http.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 409 {          // the Mac restarted or another source took over
          await hello()
          return
        }
        guard code == 200 else { throw URLError(.badServerResponse) }
        sent += 1
        connected = true
        error = nil
        handle(data)
      } catch {
        connected = false
        self.error = "Can't reach the Mac at \(address)"
      }
    }
  }

  private func handle(_ data: Data) {
    guard let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
    if let game = reply["game"] as? [String: Any] {
      self.game = GameSummary(game)
    }
    for item in reply["speech"] as? [[String: Any]] ?? [] {
      guard let id = item["id"] as? Int, let text = item["text"] as? String, id > lastAck else { continue }
      voice.speak(text)
      lastSpoken = text
      lastAck = id
    }
  }

  private static func jpeg(_ image: UIImage) -> Data? {
    let maxSide: CGFloat = 1280
    let size = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
    let scale = min(1, maxSide / max(size.width, size.height))
    guard scale < 1 else { return image.jpegData(compressionQuality: 0.7) }
    let target = CGSize(width: (size.width * scale).rounded(), height: (size.height * scale).rounded())
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: target))
    }
    return resized.jpegData(compressionQuality: 0.7)
  }
}

/// What the Mac says about the game, for the one status line.
struct GameSummary {
  var status = "unknown"
  var bestMove: String?
  var userColor: String?
  var boardVisible = false
  /// Short advice from vision while calibrating or stuck, e.g. "Sit closer and hold still".
  var hint: String?

  init() {}

  init(_ game: [String: Any]) {
    status = game["status"] as? String ?? "unknown"
    bestMove = game["best_move"] as? String
    userColor = game["user_color"] as? String
    boardVisible = game["board_visible"] as? Bool ?? false
    hint = game["hint"] as? String
  }

  var headline: String {
    switch status {
    case "idle": return "Press New game"
    case "calibrating": return boardVisible ? "Hold still on the starting position" : "Look at the starting position"
    case "your_move": return bestMove.map { "Your move: \($0)" } ?? "Your move"
    case "waiting": return "Opponent's move"
    case "check_board": return "Check the board"
    case "game_over": return "Game over"
    default: return "Not connected"
    }
  }
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
