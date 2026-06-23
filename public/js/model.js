// model.js — Pemuat model ROV bersama (single-parse + clone).
// Model di-parse SEKALI per-URL lalu di-cache. Konsumen (scene Control & Mission)
// memanggil loadModelOnce() dan meng-clone hasilnya: clone berbagi geometry &
// material yang sama, jadi model 3D berat hanya menempati memori satu kali.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

const cache = new Map(); // url -> Promise<THREE.Object3D> (objek dasar, jangan diubah)

/* Muat model sekali per-URL; pemanggil berikutnya memakai Promise yang sama. */
export function loadModelOnce(url) {
  if (cache.has(url)) return cache.get(url);
  const ext = url.split(".").pop().toLowerCase();
  const p = new Promise((resolve, reject) => {
    if (ext === "glb" || ext === "gltf") {
      new GLTFLoader().load(url, (g) => resolve(g.scene), undefined, reject);
    } else if (ext === "fbx") {
      new FBXLoader().load(url, (o) => resolve(o), undefined, reject);
    } else {
      reject(new Error("ekstensi model tidak didukung: " + ext));
    }
  });
  cache.set(url, p);
  return p;
}

/* Skala model agar dimensi terbesarnya = targetSize, lalu pusatkan ke origin. */
export function fitAndCenter(obj, targetSize = 1) {
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const s = targetSize / maxDim;
  obj.scale.setScalar(s);
  obj.position.sub(center.multiplyScalar(s));
}

/* Orientasikan instance sesuai format file:
   - FBX biasanya Z-up  -> miringkan -90° pada X agar Y-up.
   - GLB/GLTF sudah Y-up -> tanpa koreksi.
   flip180=true memutar heading 180° pada sumbu-atas yang benar untuk tiap format. */
export function orient(model, url, flip180 = false) {
  const ext = url.split(".").pop().toLowerCase();
  if (ext === "fbx") {
    model.rotation.x = -Math.PI / 2;
    if (flip180) model.rotation.z = Math.PI; // atas model Z-up = sumbu-Z lokal
  } else if (flip180) {
    model.rotation.y = Math.PI;              // GLB sudah Y-up
  }
}
