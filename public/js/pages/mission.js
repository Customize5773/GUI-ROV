import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadModelOnce, fitAndCenter, orient } from "../model.js";
import { CONFIG } from "../config.js";
import { pilotAxes, log, num } from "../core.js";

const SONAR = 0x14d8ff;
const MAX_POINTS = 3000;
const VEL_SCALE = 0.02;     // unit dunia per (satuan-thrust · detik)
const DEPTH_SCALE = 0.5;    // unit dunia per meter kedalaman
const CRUISE_SURGE = 25;    // dorongan maju otomatis saat auto-cruise

export const missionPage = {
  three: null,
  recording: false,
  autoCruise: true,
  follow: false,
  pos: new THREE.Vector3(0, 0, 0),
  heading: 0,
  depth: 0,
  attitude: { roll: 0, pitch: 0 },
  points: null,             // Float32Array
  count: 0,
  distance: 0,
  visible: false,
  raf: null,
  clock: null,
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
          </div>
        </div>
        <div class="mission__stage" id="missionStage">
          <div class="mission__btns">
            <button class="chip chip--go" id="msStart">Start</button>
            <button class="chip" id="msPause">Pause</button>
            <button class="chip" id="msReset">Reset</button>
            <button class="chip" id="msCruise" aria-pressed="true">Auto-cruise</button>
            <button class="chip" id="msFollow" aria-pressed="false">Follow</button>
          </div>
          <div class="mission__hint" id="msHint">Tekan <b>Start</b> untuk mulai merekam lintasan</div>
        </div>
      </div>`;

    this.els.x = root.querySelector("#msX");
    this.els.y = root.querySelector("#msY");
    this.els.d = root.querySelector("#msD");
    this.els.dist = root.querySelector("#msDist");
    this.els.hint = root.querySelector("#msHint");

    root.querySelector("#msStart").onclick = () => this._start();
    root.querySelector("#msPause").onclick = () => this._pause();
    root.querySelector("#msReset").onclick = () => this._reset();
    const cruiseBtn = root.querySelector("#msCruise");
    cruiseBtn.onclick = () => {
      this.autoCruise = !this.autoCruise;
      cruiseBtn.setAttribute("aria-pressed", String(this.autoCruise));
    };
    const followBtn = root.querySelector("#msFollow");
    followBtn.onclick = () => {
      this.follow = !this.follow;
      followBtn.setAttribute("aria-pressed", String(this.follow));
    };

    this._buildScene(root.querySelector("#missionStage"));
    this.clock = new THREE.Clock();
  },

  _buildScene(container) {
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x05121a, 0.022);

    const w = container.clientWidth || 600, h = container.clientHeight || 400;
    const camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 500);
    camera.position.set(8, 9, 12);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.49;

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
    const path = new THREE.Line(pathGeo, new THREE.LineBasicMaterial({ color: SONAR, transparent: true, opacity: 0.9 }));
    scene.add(path);

    // marker ROV
    const rov = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(0.5, 0.28, 0.7),
      new THREE.MeshStandardMaterial({ color: 0xcfd9e0, roughness: 0.5 })
    );
    rov.add(body);
    const bow = new THREE.Mesh(
      new THREE.ConeGeometry(0.16, 0.4, 16),
      new THREE.MeshStandardMaterial({ color: SONAR, emissive: SONAR, emissiveIntensity: 0.7 })
    );
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

    this.three = { scene, camera, renderer, controls, container, path, pathGeo, rov, drop, dropGeo, sMark, eMark };
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
    if (this.clock) this.clock.getDelta(); // buang delta menumpuk saat tersembunyi
    if (!this.raf) this._loop();
  },
  onHide() {
    this.visible = false;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  },

  onTelemetry(d) {
    if (Number.isFinite(d.heading)) this.heading = ((d.heading % 360) + 360) % 360;
    if (Number.isFinite(d.depth)) this.depth = d.depth;
    this.attitude.roll = d.roll || 0;
    this.attitude.pitch = d.pitch || 0;
  },

  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.visible || !this.three) return;
    const dt = Math.min(this.clock.getDelta(), 0.1);

    // integrasi posisi (dead-reckoning)
    let surge = pilotAxes.surge, sway = pilotAxes.sway;
    if (this.recording && this.autoCruise && Math.abs(surge) < 1) surge = CRUISE_SURGE;
    const hr = THREE.MathUtils.degToRad(this.heading);
    // heading 0 = utara (-Z); timur = +X
    const fwd = new THREE.Vector3(Math.sin(hr), 0, -Math.cos(hr));
    const right = new THREE.Vector3(Math.cos(hr), 0, Math.sin(hr));
    const vel = fwd.multiplyScalar(surge * VEL_SCALE).add(right.multiplyScalar(sway * VEL_SCALE));
    if (this.recording) {
      const step = vel.clone().multiplyScalar(dt);
      this.pos.add(step);
      this.distance += step.length();
    }
    this.pos.y = -this.depth * DEPTH_SCALE;

    const t = this.three;
    // ROV marker
    t.rov.position.copy(this.pos);
    t.rov.rotation.y = -hr;
    t.rov.rotation.x = THREE.MathUtils.degToRad(this.attitude.pitch);
    t.rov.rotation.z = THREE.MathUtils.degToRad(-this.attitude.roll);

    // garis kedalaman
    t.dropGeo.setFromPoints([new THREE.Vector3(this.pos.x, 0, this.pos.z), this.pos.clone()]);
    t.drop.computeLineDistances();

    // rekam titik lintasan
    if (this.recording) this._maybeAddPoint();

    // marker E mengikuti posisi terakhir
    if (this.count > 0) { t.eMark.visible = true; t.eMark.position.copy(this.pos).setY(this.pos.y + 0.6); }

    // follow cam
    if (this.follow) {
      const desired = this.pos.clone().add(new THREE.Vector3(6, 7, 9));
      t.camera.position.lerp(desired, 0.05);
      t.controls.target.lerp(this.pos, 0.1);
    }

    t.controls.update();
    t.renderer.render(t.scene, t.camera);

    // readout
    this.els.x.textContent = num(this.pos.x, 2);
    this.els.y.textContent = num(this.pos.z, 2);
    this.els.d.textContent = num(this.depth, 2);
    this.els.dist.textContent = num(this.distance, 2);
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
    this.three.pathGeo.computeBoundingSphere();
  },

  _start() {
    if (!this.recording && this.count === 0) {
      // tandai titik Start di posisi saat ini
      this.three.sMark.visible = true;
      this.three.sMark.position.copy(this.pos).setY(this.pos.y + 0.6);
      this._maybeAddPoint();
    }
    this.recording = true;
    this.els.hint.style.display = "none";
    log("Mission: rekam lintasan dimulai", "ok");
  },
  _pause() {
    this.recording = false;
    log("Mission: lintasan dijeda", "warn");
  },
  _reset() {
    this.recording = false;
    this.count = 0;
    this.distance = 0;
    this.pos.set(0, 0, 0);
    this.three.pathGeo.setDrawRange(0, 0);
    this.three.pathGeo.attributes.position.needsUpdate = true;
    this.three.sMark.visible = false;
    this.three.eMark.visible = false;
    this.els.hint.style.display = "";
    log("Mission: lintasan direset", "");
  },
};
