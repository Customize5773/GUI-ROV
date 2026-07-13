// replay-scene.js — Renderer trajectory 3D untuk halaman Replay.
//
// SENGAJA merupakan modul terpisah (bukan mengimpor missionPage) supaya state
// live (mission.js: recording, dead-reckoning realtime) dan state replay tidak
// pernah bercampur — sesuai syarat "pisahkan state live vs replay". Desain scene
// (grid dasar, garis lintasan sonar, marker ROV, garis kedalaman, marker S/E,
// muat model FBX) diadaptasi dari mission.js `_buildScene` agar tampil identik,
// tanpa menyentuh/menyisipkan risiko regresi ke kode live Mission.
//
// Berbeda dari mission.js, modul ini TIDAK mengintegrasi posisi sendiri. Ia
// hanya menerima pose historis (setPose) dan seluruh lintasan (setPath) yang
// sudah direkonstruksi oleh replay.js dari log rekaman.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadModelOnce, fitAndCenter, orient } from "../model.js";
import { CONFIG } from "../config.js";
import { log } from "../core.js";

const SONAR = 0x14d8ff;
const MAX_POINTS = 6000; // cukup untuk 1 run misi pada laju sampel telemetry

export function createTrajectoryScene(container) {
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

  scene.add(new THREE.HemisphereLight(0x9fdfff, 0x06121a, 1.0));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(5, 10, 4);
  scene.add(key);

  const grid = new THREE.PolarGridHelper(30, 12, 8, 64, 0x1c3a45, 0x12252e);
  scene.add(grid);
  const sq = new THREE.GridHelper(60, 30, 0x16303c, 0x102029);
  sq.position.y = -0.01;
  scene.add(sq);

  // garis lintasan (seluruh path dimuat sekali; berapa yang tampak diatur setProgress)
  const points = new Float32Array(MAX_POINTS * 3);
  const pathGeo = new THREE.BufferGeometry();
  pathGeo.setAttribute("position", new THREE.BufferAttribute(points, 3));
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

  // garis kedalaman dari permukaan ke ROV
  const dropGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
  const drop = new THREE.Line(dropGeo, new THREE.LineDashedMaterial({ color: SONAR, dashSize: 0.2, gapSize: 0.15, transparent: true, opacity: 0.4 }));
  scene.add(drop);

  // marker Start / End
  const sMark = marker("S", "#37d392");
  const eMark = marker("E", "#f5a524");
  sMark.visible = false; eMark.visible = false;
  scene.add(sMark); scene.add(eMark);

  loadRovModel(rov);

  let count = 0;      // jumlah titik path yang dimuat
  let raf = null;

  function loop() {
    raf = requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  }
  function start() { if (!raf) loop(); }
  function stop() { if (raf) { cancelAnimationFrame(raf); raf = null; } }

  function resize() {
    const w2 = container.clientWidth, h2 = container.clientHeight;
    if (!w2 || !h2) return;
    camera.aspect = w2 / h2; camera.updateProjectionMatrix();
    renderer.setSize(w2, h2);
  }

  // poses: array {x,y,z,heading,roll,pitch} — muat seluruh lintasan sekali.
  function setPath(poses) {
    count = Math.min(poses.length, MAX_POINTS);
    for (let i = 0; i < count; i++) {
      const p = poses[i];
      points[i * 3] = p.x;
      points[i * 3 + 1] = p.y;
      points[i * 3 + 2] = p.z;
    }
    pathGeo.setDrawRange(0, 0);
    pathGeo.attributes.position.needsUpdate = true;
    pathGeo.computeBoundingSphere();
    if (count > 0) {
      sMark.visible = true;
      sMark.position.set(poses[0].x, poses[0].y + 0.6, poses[0].z);
      eMark.visible = true;
      eMark.position.set(poses[count - 1].x, poses[count - 1].y + 0.6, poses[count - 1].z);
    } else {
      sMark.visible = false; eMark.visible = false;
    }
  }

  // tampilkan lintasan sampai n titik (jejak progresif mengikuti scrubber)
  function setProgress(n) {
    pathGeo.setDrawRange(0, Math.max(0, Math.min(n, count)));
  }

  function setPose(p) {
    if (!p) return;
    rov.position.set(p.x, p.y, p.z);
    rov.rotation.y = -THREE.MathUtils.degToRad(p.heading || 0);
    rov.rotation.x = THREE.MathUtils.degToRad(p.pitch || 0);
    rov.rotation.z = THREE.MathUtils.degToRad(-(p.roll || 0));
    dropGeo.setFromPoints([new THREE.Vector3(p.x, 0, p.z), new THREE.Vector3(p.x, p.y, p.z)]);
    drop.computeLineDistances();
  }

  function dispose() {
    stop();
    try { renderer.dispose(); } catch (e) {}
    if (renderer.domElement && renderer.domElement.parentNode) {
      renderer.domElement.parentNode.removeChild(renderer.domElement);
    }
  }

  return { start, stop, resize, setPath, setProgress, setPose, dispose, get pointCount() { return count; } };
}

function marker(text, color) {
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
}

function loadRovModel(rovGroup) {
  const url = CONFIG.MODEL_URL;
  if (!url) return;
  loadModelOnce(url).then((base) => {
    const model = base.clone(true);
    orient(model, url, true);
    fitAndCenter(model, 1.0);
    while (rovGroup.children.length) rovGroup.remove(rovGroup.children[0]);
    rovGroup.add(model);
    log("Replay: model ROV dimuat", "ok");
  }).catch(() => log("Replay: gagal muat model ROV, memakai marker built-in", "warn"));
}
