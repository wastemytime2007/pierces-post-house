import Foundation
import Vision
import ImageIO

// Face boxes for each image, from macOS's own detector (Vision): no model to download.
//   swift find_faces.swift image.jpg [more.jpg ...]
// One JSON line per image: {"image": path, "faces": [{"x": centre, "y": centre, "w": width, "h": height}]}.
// Every number is a fraction of the image with the origin at the top left (Vision's origin is the bottom left; it is flipped here).
for path in CommandLine.arguments.dropFirst() {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        print("{\"image\": \"\(path)\", \"error\": \"could not read\"}")
        continue
    }
    let req = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    do { try handler.perform([req]) } catch {
        print("{\"image\": \"\(path)\", \"error\": \"\(error)\"}")
        continue
    }
    var out: [String] = []
    for f in (req.results ?? []) {
        let b = f.boundingBox
        out.append(String(format: "{\"x\": %.4f, \"y\": %.4f, \"w\": %.4f, \"h\": %.4f}", b.midX, 1.0 - b.midY, b.width, b.height))
    }
    print("{\"image\": \"\(path)\", \"faces\": [\(out.joined(separator: ", "))]}")
}
