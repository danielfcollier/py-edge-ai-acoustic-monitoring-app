import onnxruntime as ort

s = ort.InferenceSession('src/yamnet/yamnet.onnx')

for o in s.get_outputs():
  print(o.name, o.shape)
