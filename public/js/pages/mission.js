import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadModelOnce, fitAndCenter, orient } from "../model.js";
import { CONFIG } from "../config.js";
import { pilotAxes, log, num } from "../core.js";

const SONAR = 0x14d8ff;
const MAX_POINTS = 3000;
const VEL_SCALE = 0.02;     // unit dunia per (satuan-thrust · detik)
const DEPTH_SCALE = 0.5;    // unit dunia per meter kedalaman
const TICK_MS = 200;        // integrasi posisi @5Hz, lepas dari rAF render (hemat CPU saat halaman lain aktif)
const TICK_DT = TICK_MS / 1000;

// warna & gaya garis per keandalan sumber posisi X/Z: HOOK MAP (vision, akurat) >
// EKF (estimasi ArduSub, tanpa GPS/DVL) > Estimasi (dead-reckoning dari stick, fiksi)
const SRC_STYLE = {
  hook: { color: 0x37d392, label: "hook", dashed: false },
  ekf:  { color: 0xf5a524, label: "ekf", dashed: false },
  est:  { color: 0x8a94a6, label: "est", dashed: true },
};

export const missionPage = {
  three: null,
  follow: false,
  pos: new THREE.Vector3(0, 0, 0),
  heading: 0,
  depth: 0,
  posN: null,
  posE: null,
  hookMapEnabled: false,
  hookPose: null,
  hookStatus: null,
  originN: 0,
  originE: 0,
  attitude: { roll: 0, pitch: 0 },
  points: null,             // Float32Array
  count: 0,
  distance: 0,
  visible: false,
  armed: false,
  raf: null,
  tickTimer: null,
  els: {},

  init(root) {
    root.innerHTML = `
      <div class="mission">
        <div class="mission__head">
          <div>
            <span class="panel__eyebrow">TRAJECTORY MAP</span>
            <h2 class="tele__title">Live ROV Position</h2>
          </div>
          <div class="mission__readout">
            <span>X <b id="msX">0.00</b></span>
            <span>Y <b id="msY">0.00</b></span>
            <span>Depth <b id="msD">0.00</b> m</span>
            <span>Dist <b id="msDist">0.00</b> m</span>
            <span>Sumber <b id="msSrc">—</b></span>
          </div>
        </div>
        <div class="mission__stage" id="missionStage">
          <div class="mission__btns">
            <button class="chip" id="msReset">Reset</button>
            <button class="chip" id="msFollow" aria-pressed="false">Follow</button>
            <button class="chip" id="msSave">Save PNG</button>
          </div>
        </div>
      </div>`;

    this.els.x = root.querySelector("#msX");
    this.els.y = root.querySelector("#msY");
    this.els.d = root.querySelector("#msD");
    this.els.dist = root.querySelector("#msDist");
    this.els.src = root.querySelector("#msSrc");

    root.querySelector("#msReset").onclick = () => this._reset();
    const followBtn = root.querySelector("#msFollow");
    followBtn.onclick = () => {
      this.follow = !this.follow;
      followBtn.setAttribute("aria-pressed", String(this.follow));
    };
    root.querySelector("#msSave").onclick = () => this._savePng();

    this._buildScene(root.querySelector("#missionStage"));
    // rekam otomatis begitu halaman dibuka, terus berjalan (lepas dari rAF) sampai server berhenti
    if (!this.tickTimer) this.tickTimer = setInterval(() => this._tick(), TICK_MS);
  },

  _buildScene(container) {
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x05121a, 0.022);

    const w = container.clientWidth || 600, h = container.clientHeight || 400;
    const camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 500);
    camera.position.set(8, 9, 12);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    // tanpa batas sudut: bebas memutar pandangan termasuk di bawah horizon

    scene.add(new THREE.HemisphereLight(0x9fdfff, 0x06121a, 1.0));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(5, 10, 4);
    scene.add(key);

    // dasar laut: grid radial
    const grid = new THREE.PolarGridHelper(30, 12, 8, 64, 0x1c3a45, 0x12252e);
    scene.add(grid);
    const sq = new THREE.GridHelper(60, 30, 0x16303c, 0x102029);
    sq.position.y = -0.01;
    scene.add(sq);

    // garis lintasan
    this.points = new Float32Array(MAX_POINTS * 3);
    const pathGeo = new THREE.BufferGeometry();
    pathGeo.setAttribute("position", new THREE.BufferAttribute(this.points, 3));
    pathGeo.setDrawRange(0, 0);
    const pathMatSolid = new THREE.LineBasicMaterial({ color: SRC_STYLE.hook.color, transparent: true, opacity: 0.9 });
    const pathMatDashed = new THREE.LineDashedMaterial({ color: SRC_STYLE.est.color, dashSize: 0.3, gapSize: 0.2, transparent: true, opacity: 0.9 });
    const path = new THREE.Line(pathGeo, pathMatSolid);
    scene.add(path);

    // marker ROV
    const rov = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(0.5, 0.28, 0.7),
      new THREE.MeshStandardMaterial({ color: 0xcfd9e0, roughness: 0.5 })
    );
    rov.add(body);
    const bowMat = new THREE.MeshStandardMaterial({ color: SONAR, emissive: SONAR, emissiveIntensity: 0.7 });
    const bow = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.4, 16), bowMat);
    bow.rotation.x = Math.PI / 2;
    bow.position.z = 0.5;
    rov.add(bow);
    rov.rotation.order = "YXZ";
    scene.add(rov);

    // garis tegak penanda kedalaman dari permukaan ke ROV
    const dropGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const drop = new THREE.Line(dropGeo, new THREE.LineDashedMaterial({ color: SONAR, dashSize: 0.2, gapSize: 0.15, transparent: true, opacity: 0.4 }));
    scene.add(drop);

    // marker S / E
    const sMark = this._marker("S", "#37d392");
    const eMark = this._marker("E", "#f5a524");
    sMark.visible = false; eMark.visible = false;
    scene.add(sMark); scene.add(eMark);

    this.three = { scene, camera, renderer, controls, container, path, pathGeo, pathMatSolid, pathMatDashed, rov, bowMat, drop, dropGeo, sMark, eMark };
    this._srcKey = null;
    this._loadRovModel(rov);
    this._resize = () => {
      const w2 = container.clientWidth, h2 = container.clientHeight;
      if (!w2 || !h2) return;
      camera.aspect = w2 / h2; camera.updateProjectionMatrix();
      renderer.setSize(w2, h2);
    };
    window.addEventListener("resize", this._resize);
  },

  _loadRovModel(rovGroup) {
    const url = CONFIG.MODEL_URL;
    if (!url) return;
    loadModelOnce(url).then((base) => {
      const model = base.clone(true);   // berbagi geometry/material dgn scene Control
      orient(model, url, true);          // flip 180° agar menghadap arah maju
      fitAndCenter(model, 1.0);
      while (rovGroup.children.length) rovGroup.remove(rovGroup.children[0]);
      rovGroup.add(model);
      log("Mission: model ROV dimuat", "ok");
    }).catch(() => log("Mission: gagal muat model ROV, menggunakan built-in", "warn"));
  },

  _marker(text, color) {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const x = c.getContext("2d");
    x.fillStyle = color;
    x.beginPath(); x.arc(64, 64, 52, 0, Math.PI * 2); x.fill();
    x.fillStyle = "#04121e";
    x.font = "bold 72px 'Chakra Petch', sans-serif";
    x.textAlign = "center"; x.textBaseline = "middle";
    x.fillText(text, 64, 70);
    const tex = new THREE.CanvasTexture(c);
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
    spr.scale.set(1.1, 1.1, 1.1);
    return spr;
  },

  onShow() {
    this.visible = true;
    if (this._resize) this._resize();
    if (!this.raf) this._loop();
  },
  onHide() {
    this.visible = false;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  },

  onTelemetry(d) {
    this.armed = d.armed === true;
    if (Number.isFinite(d.heading)) this.heading = ((d.heading % 360) + 360) % 360;
    if (Number.isFinite(d.depth)) this.depth = d.depth;
    this.attitude.roll = d.roll || 0;
    this.attitude.pitch = d.pitch || 0;
    this.posN = Number.isFinite(d.pos_n) ? d.pos_n : null;
    this.posE = Number.isFinite(d.pos_e) ? d.pos_e : null;
    const m5 = d.mission5 || {};
    const loc = m5.hook_loc;
    this.hookMapEnabled = m5.hook_map_enabled === true;
    this.hookStatus = loc && loc.status;
    const p = loc && loc.pose_map;
    this.hookPose = loc && loc.status === "ok" && p
      && Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y))
      ? { x: Number(p.x), y: Number(p.y), sigma: Number(loc.sigma_xy_m) }
      : null;
  },

  // integrasi posisi + rekam titik: jalan terus @5Hz lewat setInterval, lepas dari
  // rAF/visibility halaman, jadi trajectory tak berhenti saat pilot pindah tab lain.
  _tick() {
    if (!this.three) return;
    if (!this.armed) return; // disarm = misi berhenti, jangan lanjut rekam lintasan
    const hr = THREE.MathUtils.degToRad(this.heading);
    // heading 0 = utara (-Z); timur = +X

    if (this.count === 0) this._markStart();

    let newX, newZ;
    if (this.hookPose) {
      // hook-map memakai koordinat arena absolut: x panjang, y lebar.
      newX = this.hookPose.x;
      newZ = -this.hookPose.y;
    } else if (this.hookMapEnabled) {
      // Map aktif tetapi observasi belum valid/ambigu: tahan posisi terakhir.
      // Jangan diam-diam mengganti sumber dengan dead-reckoning.
      newX = this.pos.x;
      newZ = this.pos.z;
    } else if (this.posN != null && this.posE != null) {
      // posisi sungguhan dari EKF ArduSub (LOCAL_POSITION_NED), relatif ke titik Start
      newX = this.posE - this.originE;
      newZ = -(this.posN - this.originN);
    } else {
      // EKF belum mengirim data: fallback dead-reckoning dari command
      const surge = pilotAxes.surge, sway = pilotAxes.sway;
      const fwd = new THREE.Vector3(Math.sin(hr), 0, -Math.cos(hr));
      const right = new THREE.Vector3(Math.cos(hr), 0, Math.sin(hr));
      const step = fwd.multiplyScalar(surge * VEL_SCALE).add(right.multiplyScalar(sway * VEL_SCALE)).multiplyScalar(TICK_DT);
      newX = this.pos.x + step.x;
      newZ = this.pos.z + step.z;
    }
    this.distance += Math.hypot(newX - this.pos.x, newZ - this.pos.z);
    this.pos.x = newX;
    this.pos.z = newZ;
    this.pos.y = -this.depth * DEPTH_SCALE;

    const t = this.three;

    // pilih gaya visual sesuai keandalan sumber X/Z (hook-vision > EKF > dead-reckoning)
    const srcKey = this.hookPose ? "hook" : (this.hookMapEnabled || (this.posN != null && this.posE != null)) ? "ekf" : "est";
    if (srcKey !== this._srcKey) {
      this._srcKey = srcKey;
      const style = SRC_STYLE[srcKey];
      t.path.material = style.dashed ? t.pathMatDashed : t.pathMatSolid;
      t.path.material.color.setHex(style.color);
      t.bowMat.color.setHex(style.color);
      t.bowMat.emissive.setHex(style.color);
      this.els.src.parentElement.dataset.src = style.label;
    }

    // ROV marker
    t.rov.position.copy(this.pos);
    t.rov.rotation.y = -hr;
    t.rov.rotation.x = THREE.MathUtils.degToRad(this.attitude.pitch);
    t.rov.rotation.z = THREE.MathUtils.degToRad(-this.attitude.roll);

    // garis kedalaman
    t.dropGeo.setFromPoints([new THREE.Vector3(this.pos.x, 0, this.pos.z), this.pos.clone()]);
    t.drop.computeLineDistances();

    this._maybeAddPoint();

    // marker E mengikuti posisi terakhir
    if (this.count > 0) { t.eMark.visible = true; t.eMark.position.copy(this.pos).setY(this.pos.y + 0.6); }

    // readout
    this.els.x.textContent = num(this.pos.x, 2);
    this.els.y.textContent = num(this.hookMapEnabled ? -this.pos.z : this.pos.z, 2);
    this.els.d.textContent = num(this.depth, 2);
    this.els.dist.textContent = num(this.distance, 2);
    this.els.src.textContent = this.hookPose
      ? `HOOK MAP${Number.isFinite(this.hookPose.sigma) ? ` ±${this.hookPose.sigma.toFixed(2)}m` : ""}`
      : this.hookMapEnabled
        ? `HOOK ${String(this.hookStatus || "WAIT").toUpperCase()}`
        : (this.posN != null && this.posE != null) ? "EKF" : "Estimasi";
  },

  // render-only, rAF: hanya jalan saat halaman Mission terlihat (hemat GPU/latency saat pindah halaman)
  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.visible || !this.three) return;
    const t = this.three;

    if (this.follow) {
      const desired = this.pos.clone().add(new THREE.Vector3(6, 7, 9));
      t.camera.position.lerp(desired, 0.05);
      t.controls.target.lerp(this.pos, 0.1);
    }
    t.controls.update();
    t.renderer.render(t.scene, t.camera);
  },

  _markStart() {
    // titik nol sesi: posisi EKF saat ini jadi origin agar lintasan mulai
    // dari sekitar (0,0), bukan meloncat ke koordinat NED absolut
    if (this.posN != null && this.posE != null) {
      this.originN = this.posN;
      this.originE = this.posE;
    }
    this.three.sMark.visible = true;
    this.three.sMark.position.copy(this.pos).setY(this.pos.y + 0.6);
  },

  _maybeAddPoint() {
    const i = this.count;
    if (i >= MAX_POINTS) return;
    if (i > 0) {
      const dx = this.pos.x - this.points[(i - 1) * 3];
      const dy = this.pos.y - this.points[(i - 1) * 3 + 1];
      const dz = this.pos.z - this.points[(i - 1) * 3 + 2];
      if (dx * dx + dy * dy + dz * dz < 0.0025) return; // < 0.05u, lewati
    }
    this.points[i * 3] = this.pos.x;
    this.points[i * 3 + 1] = this.pos.y;
    this.points[i * 3 + 2] = this.pos.z;
    this.count++;
    this.three.pathGeo.setDrawRange(0, this.count);
    this.three.pathGeo.attributes.position.needsUpdate = true;
    if (this.three.path.material === this.three.pathMatDashed) this.three.path.computeLineDistances();
    this.three.pathGeo.computeBoundingSphere();
  },

  /* ekspor peta trajectory ke PNG (dokumentasi lintasan awal→akhir untuk KKI) */
  _savePng() {
    if (!this.three) return;
    const t = this.three;
    t.renderer.render(t.scene, t.camera); // pastikan frame terbaru sebelum capture
    const a = document.createElement("a");
    a.href = t.renderer.domElement.toDataURL("image/png");
    a.download = `hydroship_trajectory_${Date.now()}.png`;
    a.click();
    log("Mission: trajectory disimpan (PNG)", "ok");
  },
  _reset() {
    this.count = 0;
    this.distance = 0;
    this.pos.set(0, 0, 0);
    this.three.pathGeo.setDrawRange(0, 0);
    this.three.pathGeo.attributes.position.needsUpdate = true;
    this.three.sMark.visible = false;
    this.three.eMark.visible = false;
    log("Mission: lintasan direset", "");
  },
};
