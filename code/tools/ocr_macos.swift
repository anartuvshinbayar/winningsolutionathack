// Local OCR for evidence screenshots using the macOS Vision framework.
// Usage: swift code/tools/ocr_macos.swift <image.png>  -> prints recognized lines (top-to-bottom, left-to-right)
import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
    exit(1)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
var rows: [(Double, Double, String)] = []
for obs in request.results ?? [] {
    guard let text = obs.topCandidates(1).first?.string else { continue }
    let box = obs.boundingBox
    rows.append((Double(1 - box.midY), Double(box.minX), text))
}
rows.sort { abs($0.0 - $1.0) > 0.008 ? $0.0 < $1.0 : $0.1 < $1.1 }
var line = ""
var lastY = -1.0
for (y, x, text) in rows {
    if lastY >= 0 && abs(y - lastY) > 0.008 { print(line); line = "" }
    line += (line.isEmpty ? "" : " | ") + text
    lastY = y
    _ = x
}
if !line.isEmpty { print(line) }
