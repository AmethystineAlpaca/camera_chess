import AVFoundation
import AppKit
import CoreImage
let directory = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.currentDirectoryPath + "/validation/frames/lite-live"
try FileManager.default.createDirectory(atPath:directory,withIntermediateDirectories:true)
let sem=DispatchSemaphore(value:0)
class Receiver:NSObject,AVCaptureVideoDataOutputSampleBufferDelegate{
 let context=CIContext();var index=0;var done=false;var lastEncoded=0.0
 func captureOutput(_ output:AVCaptureOutput,didOutput sampleBuffer:CMSampleBuffer,from connection:AVCaptureConnection){
  if done{return};if FileManager.default.fileExists(atPath:directory+"/STOP"){done=true;sem.signal();return}
  let captured=Date().timeIntervalSince1970
  // Inference consumes about 2-4 frames/sec. Avoid JPEG-encoding 30 discarded frames/sec.
  if captured-lastEncoded < 0.125{return};lastEncoded=captured
  guard let buffer=CMSampleBufferGetImageBuffer(sampleBuffer) else{return}
  let ci=CIImage(cvPixelBuffer:buffer);guard let cg=context.createCGImage(ci,from:ci.extent) else{return}
  let bitmap=NSBitmapImageRep(cgImage:cg)
  if let jpeg=bitmap.representation(using:.jpeg,properties:[.compressionFactor:0.85]){
   var data="\(index),\(captured)\n".data(using:.utf8)!;data.append(jpeg);try? data.write(to:URL(fileURLWithPath:directory+"/latest.bin"),options:.atomic)
  }
  if index%300==0{print("CAPTURE",index,captured);fflush(stdout)};index+=1
 }
}
let authorization=AVCaptureDevice.authorizationStatus(for:.video)
if authorization == .notDetermined {
 let permission=DispatchSemaphore(value:0)
 AVCaptureDevice.requestAccess(for:.video){_ in permission.signal()}
 permission.wait()
}
guard AVCaptureDevice.authorizationStatus(for:.video) == .authorized else {
 fputs("Camera access denied. Enable camera access for the launching application in macOS Settings.\n",stderr)
 exit(1)
}
let discovery=AVCaptureDevice.DiscoverySession(deviceTypes:[.builtInWideAngleCamera],mediaType:.video,position:.unspecified)
let session=AVCaptureSession();session.sessionPreset = .high
guard let device=discovery.devices.first else {fputs("No built-in camera is available.\n",stderr);exit(1)}
let input:AVCaptureDeviceInput
do {input=try AVCaptureDeviceInput(device:device)} catch {fputs("Cannot open camera: \(error)\n",stderr);exit(1)}
guard session.canAddInput(input) else {fputs("Cannot attach camera input.\n",stderr);exit(1)}
session.addInput(input)
let output=AVCaptureVideoDataOutput();output.alwaysDiscardsLateVideoFrames=true;output.videoSettings=[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA]
let receiver=Receiver();output.setSampleBufferDelegate(receiver,queue:DispatchQueue(label:"stream-capture"));guard session.canAddOutput(output) else {fputs("Cannot attach camera output.\n",stderr);exit(1)};session.addOutput(output)
session.startRunning();sem.wait();session.stopRunning()
