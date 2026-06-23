export const CONFIG = {
  WS_URL: `ws://${location.hostname || "localhost"}:8080`,

  //http://192.168.2.2:8080/?action=stream
  CAMERA_URL: "",

  // sumber kamera tambahan untuk halaman Camera (label + url)
  CAMERAS: [
    { id: "CAM 1", url: "" },
    { id: "CAM 2", url: "" },
    { id: "CAM 3", url: "" },
    { id: "CAM 4", url: "" },
  ],

  // "models/rov.glb" or "models/rov.fbx".
  MODEL_URL: "models/rov.fbx",

  // kedalaman kolam uji (meter) — dipakai halaman Setup & Test Pool
  POOL_DEPTH: 3.0,

  DEMO_ON_START: false,
};
