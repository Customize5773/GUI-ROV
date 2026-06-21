import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

const SONAR = 0x2be0d6;

export class RovScene {
  constructor(container) {
    this.container = container;
    this.target = { roll: 0, pitch: 0, yaw: 0 }; // derajat
    this.current = { roll: 0, pitch: 0, yaw: 0 };

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x05121a, 0.16);

    const w = container.clientWidth || 600;
    const h = container.clientHeight || 400;
    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
    this.camera.position.set(1.6, 1.15, 1.9);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 1.2;
    this.controls.maxDistance = 5;
    this.controls.maxPolarAngle = Math.PI * 0.52;
    this.controls.target.set(0, 0, 0);

    this._lights();
    this._compass();
    this.rov = this._buildProceduralRov();
    this.rov.rotation.order = "YXZ";
    this.scene.add(this.rov);

    window.addEventListener("resize", () => this._resize());
    this._resize();
    this._animate();
  }

  _lights() {
    this.scene.add(new THREE.HemisphereLight(0x9fdfff, 0x06121a, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(3, 5, 2);
    this.scene.add(key);
    const rim = new THREE.PointLight(SONAR, 0.8, 12);
    rim.position.set(-2, 1, -2);
    this.scene.add(rim);
  }

  _compass() {
    // grid sonar radial di "dasar"
    const grid = new THREE.PolarGridHelper(1.5, 8, 5, 64, 0x1c3a45, 0x12252e);
    grid.position.y = -0.45;
    this.scene.add(grid);

    // cincin kompas
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(1.45, 0.012, 12, 96),
      new THREE.MeshBasicMaterial({ color: SONAR, transparent: true, opacity: 0.55 })
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -0.45;
    this.scene.add(ring);

    // mata angin N/E/S/W
    const dirs = [["N", 0], ["E", 90], ["S", 180], ["W", 270]];
    for (const [t, deg] of dirs) {
      const a = THREE.MathUtils.degToRad(deg);
      const spr = this._label(t, t === "N" ? "#2be0d6" : "#6e8299");
      spr.position.set(Math.sin(a) * 1.62, -0.4, -Math.cos(a) * 1.62);
      this.scene.add(spr);
    }
  }

  _label(text, color) {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const x = c.getContext("2d");
    x.fillStyle = color;
    x.font = "bold 72px 'JetBrains Mono', monospace";
    x.textAlign = "center";
    x.textBaseline = "middle";
    x.fillText(text, 64, 68);
    const tex = new THREE.CanvasTexture(c);
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
    spr.scale.set(0.28, 0.28, 0.28);
    return spr;
  }

  // model default
  _buildProceduralRov() {
    const g = new THREE.Group();
    const matTube = new THREE.MeshStandardMaterial({ color: 0xcfd9e0, roughness: 0.5, metalness: 0.1 });
    const matFrame = new THREE.MeshStandardMaterial({ color: 0x16242f, roughness: 0.7 });
    const matThr = new THREE.MeshStandardMaterial({ color: 0x0c1318, roughness: 0.6, metalness: 0.3 });

    // dua tabung apung di atas
    for (const sx of [-1, 1]) {
      const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 0.78, 20), matTube);
      tube.rotation.x = Math.PI / 2;
      tube.position.set(sx * 0.17, 0.14, 0);
      g.add(tube);
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.11, 16, 12), matTube);
      cap.position.set(sx * 0.17, 0.14, 0.39);
      g.add(cap);
    }

    // rangka utama
    const frame = new THREE.Mesh(new THREE.BoxGeometry(0.52, 0.16, 0.66), matFrame);
    frame.position.y = -0.04;
    g.add(frame);

    // thruster: 4 vektor horizontal (sudut) + 2 vertikal
    const thruster = () => {
      const t = new THREE.Group();
      const body = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.12, 16), matThr);
      body.rotation.x = Math.PI / 2;
      t.add(body);
      const prop = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.012, 16), matThr);
      prop.rotation.x = Math.PI / 2;
      prop.position.z = 0.07;
      t.add(prop);
      return t;
    };
    const corners = [[-0.26, -0.30, 45], [0.26, -0.30, -45], [-0.26, 0.30, 135], [0.26, 0.30, -135]];
    for (const [x, z, deg] of corners) {
      const t = thruster();
      t.position.set(x, -0.04, z);
      t.rotation.y = THREE.MathUtils.degToRad(deg);
      g.add(t);
    }
    for (const sx of [-1, 1]) {
      const t = thruster();
      t.position.set(sx * 0.2, 0.04, 0);
      t.rotation.x = Math.PI / 2; // menghadap atas-bawah
      g.add(t);
    }

    // penanda haluan (depan = +Z) warna sonar
    const bow = new THREE.Mesh(
      new THREE.ConeGeometry(0.05, 0.12, 16),
      new THREE.MeshStandardMaterial({ color: SONAR, emissive: SONAR, emissiveIntensity: 0.6 })
    );
    bow.rotation.x = Math.PI / 2;
    bow.position.set(0, -0.04, 0.42);
    g.add(bow);

    return g;
  }

  // (.glb / .fbx)
  loadModel(url, onTag) {
    const ext = url.split(".").pop().toLowerCase();
    const done = (model) => {
      if (ext === "fbx") {
        model.rotation.x = -Math.PI / 2;
      }

      this._normalize(model);
      const wrapper = new THREE.Group();
      wrapper.add(model);
      wrapper.rotation.order = "YXZ";
      this.scene.remove(this.rov);
      this.rov = wrapper;
      this.scene.add(wrapper);
      onTag && onTag("MODEL: " + url.split("/").pop());
    };
    const fail = (e) => { console.warn("Gagal memuat model:", e); onTag && onTag("MODEL: BUILT-IN (load gagal)"); };

    if (ext === "glb" || ext === "gltf") {
      new GLTFLoader().load(url, (g) => done(g.scene), undefined, fail);
    } else if (ext === "fbx") {
      new FBXLoader().load(url, (o) => done(o), undefined, fail);
    } else {
      fail("ekstensi tidak didukung: " + ext);
    }
  }

  _normalize(fbx) {
    const box = new THREE.Box3().setFromObject(fbx);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    const s = 0.9 / maxDim;
    fbx.scale.setScalar(s);
    fbx.position.sub(center.multiplyScalar(s));
  }

  setAttitude(roll, pitch, yaw) {
    this.target.roll = roll || 0;
    this.target.pitch = pitch || 0;
    this.target.yaw = yaw || 0;
  }

  _resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    const k = 0.18;
    for (const a of ["roll", "pitch", "yaw"]) {
      let d = this.target[a] - this.current[a];
      if (a === "yaw") { while (d > 180) d -= 360; while (d < -180) d += 360; }
      this.current[a] += d * k;
    }
    if (this.rov) {
      this.rov.rotation.y = THREE.MathUtils.degToRad(-this.current.yaw);
      this.rov.rotation.x = THREE.MathUtils.degToRad(this.current.pitch);
      this.rov.rotation.z = THREE.MathUtils.degToRad(-this.current.roll);
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
