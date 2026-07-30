export const CONFIG = {
  WS_URL: `ws://${location.hostname || "localhost"}:8080`,

  // identitas tim (tampil di header) — isi sesuai tim & kampus
  TEAM_NAME: "HYDROSHIP MRC",
  UNIVERSITY: "Politeknik Perkapalan Negeri Surabaya",

  //http://192.168.2.2:8080/?action=stream
  CAMERA_URL: "",

  // sumber kamera untuk halaman Camera (label + peran + url)
  // KKI 2026: camera 1 = bottom (lantai/QR), camera 2 = wall (dinding)
  CAMERAS: [
    { id: "CAM 1", role: "BOTTOM", url: "" },
    { id: "CAM 2", role: "WALL", url: "" },
  ],

  // "models/rov.glb" or "models/rov.fbx".
  MODEL_URL: "models/rov.fbx",

  // kedalaman kolam uji (meter) — dipakai halaman Setup & altitude
  // Arena KKI 2026: kedalaman air 0.7–0.9 m (pakai 0.9 m).
  POOL_DEPTH: 0.9,

  // ambang kedalaman berbahaya (meter) untuk alarm audio
  // Kolam dangkal 0.9 m: alarm hanya saat sangat dekat dasar (margin ~0.05 m).
  DANGER_DEPTH: 0.85,

  // konfigurasi thruster (ArduSub mixer) — KKI 2026 maksimal 6 thruster
  THRUSTER: {
    frame: "bluerov",                                   // Vectored | Vectored_6DOF | Custom
    pwmMin: 1100, pwmNeutral: 1500, pwmMax: 1900,        // mikrodetik
    gain: 100,                                           // % daya keluaran
    reversed: [false, false, false, false, false, false], // T1..T6
  },

  // gain kontrol hold (PID)
  PID: {
    yaw:   { p: 2.0,  i: 0.0, d: 0.5 },
    depth: { p: 10.0, i: 0.5, d: 2.0 },
  },

  DEMO_ON_START: false,
};
