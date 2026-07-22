// replay.js — Halaman REPLAY (fitur nilai tambah KKI 2026, §4.7 opsional).
//
// Dua fungsi:
//   1. REKAM sesi   : tombol Start/Stop mengirim {type:"record_start"/"record_stop"}
//                     via WebSocket mentah (wsSend) — BUKAN command "cmd", jadi tak
//                     pernah diteruskan ke ROV. Rekaman berjalan sepenuhnya di server.
//   2. PUTAR ULANG  : pilih sesi tersimpan, lalu scrubber timeline menggerakkan
//                     video 2 kamera DAN posisi ROV di scene 3D SECARA BERSAMAAN,
//                     memakai session_start_time sebagai acuan waktu bersama.
//
// ISOLASI (syarat task): halaman ini punya state sendiri dan TIDAK memakai jalur
// kontrol live. Satu-satunya pesan keluar adalah record_start/record_stop. Tidak
// ada kode di sini yang bisa mengirim surge/sway/arm/mode ke ROV.
//
// Posisi x,y ROV tidak ada di log (server hanya punya heading/depth). Ia
// direkonstruksi di sini dengan integrator dead-reckoning yang SAMA seperti
// mission.js (VEL_SCALE, DEPTH_SCALE, konvensi heading), dari log trajectory +
// command surge/sway — sehingga lintasan replay sepadan dengan tampilan live.

import { CONFIG } from "../config.js";
import { log, num, wsSend } from "../core.js";
import { createTrajectoryScene } from "./replay-scene.js";

// Konstanta dead-reckoning — HARUS sama dengan mission.js agar konsisten.
const VEL_SCALE = 0.02;
const DEPTH_SCALE = 0.5;

export const replayPage = {
  visible: false,
  scene: null,
  els: {},
  recording: false,
  session: null,     // sesi yang sedang diputar (data + timeline)
  playing: false,
  playT: 0,          // waktu putar relatif (ms sejak session_start_time)
  lastFrameTs: 0,    // performance.now() terakhir loop putar
  raf: null,

  init(root) {
    root.innerHTML = `
      <div class="replay">
        <div class="campage__head">
          <div>
            <span class="panel__eyebrow">RECORD &amp; REPLAY</span>
            <h2 class="tele__title">Replay Sesi Misi</h2>
          </div>
          <div class="campage__head-actions">
            <button class="chip chip--go" id="rpRecBtn">● Start Recording</button>
            <span class="badge" id="rpRecState">IDLE</span>
          </div>
        </div>

        <div class="replay__body">
          <aside class="replay__sessions">
            <div class="replay__sessions-head">
              <span class="panel__eyebrow">SESI TERSIMPAN</span>
              <button class="chip chip--ghost" id="rpRefresh" title="Muat ulang daftar">⟳</button>
            </div>
            <ul class="replay__list" id="rpList"><li class="replay__empty">Memuat…</li></ul>
          </aside>

          <section class="replay__stage">
            <div class="camgrid replay__cams" id="rpCams"></div>
            <div class="mission__stage replay__scene" id="rpScene">
              <div class="mission__hint" id="rpSceneHint">Pilih sesi untuk memuat lintasan &amp; video</div>
            </div>

            <div class="replay__timeline">
              <div class="replay__transport">
                <button class="chip chip--go" id="rpPlay" disabled>▶</button>
                <span class="replay__time" id="rpTime">00:00.0 / 00:00.0</span>
                <span class="replay__meta" id="rpMeta"></span>
              </div>
              <input type="range" id="rpScrub" class="replay__scrub" min="0" max="1000" value="0" step="1" disabled />
            </div>
          </section>
        </div>
      </div>`;

    this._injectStyle();

    this.els.recBtn = root.querySelector("#rpRecBtn");
    this.els.recState = root.querySelector("#rpRecState");
    this.els.list = root.querySelector("#rpList");
    this.els.cams = root.querySelector("#rpCams");
    this.els.sceneHost = root.querySelector("#rpScene");
    this.els.sceneHint = root.querySelector("#rpSceneHint");
    this.els.play = root.querySelector("#rpPlay");
    this.els.scrub = root.querySelector("#rpScrub");
    this.els.time = root.querySelector("#rpTime");
    this.els.meta = root.querySelector("#rpMeta");

    this.els.recBtn.onclick = () => this._toggleRecord();
    root.querySelector("#rpRefresh").onclick = () => this._loadSessions();
    this.els.play.onclick = () => this._togglePlay();
    this.els.scrub.oninput = () => {
      this._pause();
      this.playT = Number(this.els.scrub.value);
      this._applyTime(this.playT);
    };

    // scene 3D dibangun sekali (kontainer sudah punya ukuran saat init dipanggil)
    this.scene = createTrajectoryScene(this.els.sceneHost);

    this._loadSessions();
  },

  onShow() {
    this.visible = true;
    if (this.scene) { this.scene.resize(); this.scene.start(); }
    this._loadSessions();
  },
  onHide() {
    this.visible = false;
    this._pause();
    if (this.scene) this.scene.stop();
  },

  /* ===================== REKAMAN ===================== */

  // status rekaman dari server (dipanggil app.js saat pesan record_status tiba)
  onRecordStatus(st) {
    this.recording = !!(st && st.recording);
    if (!this.els.recState) return;
    if (this.recording) {
      const mins = st.max_record_ms ? Math.round(st.max_record_ms / 60000) : null;
      this.els.recState.textContent = "REC ●";
      this.els.recState.classList.add("badge--active");
      this.els.recBtn.textContent = "■ Stop Recording";
      this.els.recBtn.classList.remove("chip--go");
      this.els.meta.textContent = mins ? `Merekam… (auto-stop ${mins} mnt)` : "Merekam…";
    } else {
      this.els.recState.textContent = "IDLE";
      this.els.recState.classList.remove("badge--active");
      this.els.recBtn.textContent = "● Start Recording";
      this.els.recBtn.classList.add("chip--go");
      // segarkan daftar sesi begitu rekaman selesai (muncul entri baru)
      this._loadSessions();
    }
  },

  _toggleRecord() {
    if (this.recording) {
      wsSend({ type: "record_stop" });
      log("Replay: minta stop rekaman", "warn");
      return;
    }
    // kirim URL kamera saat ini agar server bisa men-tap stream MJPEG-nya
    const cameras = (CONFIG.CAMERAS || [])
      .filter((c) => c && c.url)
      .map((c) => ({ role: String(c.role || c.id || "").toLowerCase(), url: c.url }));
    if (!cameras.length) {
      log("Replay: tidak ada URL kamera — merekam trajectory saja (tanpa video)", "warn");
    }
    wsSend({ type: "record_start", cameras, label: CONFIG.TEAM_NAME || "" });
    log("Replay: minta mulai rekaman", "ok");
  },

  /* ===================== DAFTAR SESI ===================== */

  async _loadSessions() {
    let list = [];
    try {
      const res = await fetch("/api/recordings", { cache: "no-store" });
      list = await res.json();
    } catch (e) {
      this.els.list.innerHTML = `<li class="replay__empty">Gagal memuat daftar sesi</li>`;
      return;
    }
    if (!Array.isArray(list) || !list.length) {
      this.els.list.innerHTML = `<li class="replay__empty">Belum ada rekaman. Tekan Start Recording saat menjalankan misi.</li>`;
      return;
    }
    this.els.list.innerHTML = "";
    for (const s of list) {
      const li = document.createElement("li");
      li.className = "replay__item";
      const dur = fmtClock(s.duration_ms || 0);
      const size = fmtBytes(s.bytes || 0);
      const cams = (s.cameras || []).join("+") || "—";
      const rec = s.status === "recording" ? ` <span class="replay__rec">REC</span>` : "";
      li.innerHTML = `
        <div class="replay__item-id">${s.id}${rec}</div>
        <div class="replay__item-sub">${dur} · ${cams} · ${size} · ${s.trajectory_samples || 0} smpl</div>`;
      li.onclick = () => {
        this.els.list.querySelectorAll(".replay__item").forEach((x) => x.classList.remove("is-active"));
        li.classList.add("is-active");
        this._loadSession(s.id);
      };
      this.els.list.appendChild(li);
    }
  },

  /* ===================== MUAT & PUTAR SATU SESI ===================== */

  async _loadSession(id) {
    this._pause();
    this.els.sceneHint.style.display = "none";
    this.els.meta.textContent = "Memuat sesi…";
    try {
      const meta = await fetchJSON(`/recordings/${id}/meta.json`);
      const traj = await fetchJSONL(`/recordings/${id}/trajectory.jsonl`);
      const cmds = await fetchJSONL(`/recordings/${id}/commands.jsonl`).catch(() => []);

      // index frame video per kamera yang direkam
      const camFrames = {};
      for (const cam of (meta.cameras || [])) {
        if (!cam.frames) continue;
        const idx = await fetchJSONL(`/recordings/${id}/${cam.role}.index.jsonl`).catch(() => []);
        if (idx.length) camFrames[cam.role] = idx;
      }

      const poses = reconstructTrajectory(traj, cmds);
      const t0 = meta.session_start_time || (traj[0] && traj[0].t) || Date.now();
      const duration = Math.max(meta.duration_ms || 0, lastT(poses, camFrames) - t0, 0);

      this.session = { id, meta, t0, duration, poses, camFrames };
      this.scene.setPath(poses);
      this._buildCamViews(camFrames);

      // siapkan scrubber
      this.els.scrub.min = 0;
      this.els.scrub.max = Math.max(1, Math.round(duration));
      this.els.scrub.step = 1;
      this.els.scrub.value = 0;
      this.els.scrub.disabled = false;
      this.els.play.disabled = false;
      this.playT = 0;
      this._applyTime(0);
      this.els.meta.textContent =
        `${(meta.cameras || []).length} kamera · ${poses.length} pose · ${fmtBytes((meta.cameras || []).reduce((a, c) => a + (c.bytes || 0), 0))}`;
      log(`Replay: sesi ${id} dimuat (${poses.length} pose)`, "ok");
    } catch (e) {
      this.els.meta.textContent = "Gagal memuat sesi";
      log(`Replay: gagal memuat sesi ${id} — ${e.message}`, "err");
    }
  },

  _buildCamViews(camFrames) {
    this.els.cams.innerHTML = "";
    this._camImgs = {};
    const roles = Object.keys(camFrames);
    if (!roles.length) {
      this.els.cams.innerHTML = `<div class="replay__novideo">Sesi ini tanpa rekaman video</div>`;
      return;
    }
    for (const role of roles) {
      const cell = document.createElement("div");
      cell.className = "camcell replay__camcell";
      cell.innerHTML = `
        <div class="camcell__bar"><span class="camcell__name">${role.toUpperCase()} <b>REPLAY</b></span></div>
        <div class="camcell__view"><img alt="${role}" /></div>`;
      this.els.cams.appendChild(cell);
      this._camImgs[role] = { img: cell.querySelector("img"), lastIdx: -1 };
    }
  },

  /* ===================== TRANSPORT ===================== */

  _togglePlay() {
    if (this.playing) this._pause();
    else this._play();
  },
  _play() {
    if (!this.session) return;
    if (this.playT >= this.session.duration) this.playT = 0; // ulang dari awal
    this.playing = true;
    this.els.play.textContent = "❚❚";
    this.lastFrameTs = performance.now();
    if (!this.raf) this._loop();
  },
  _pause() {
    this.playing = false;
    if (this.els.play) this.els.play.textContent = "▶";
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  },

  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.playing || !this.session) return;
    const now = performance.now();
    const dt = now - this.lastFrameTs;
    this.lastFrameTs = now;
    this.playT += dt;
    if (this.playT >= this.session.duration) {
      this.playT = this.session.duration;
      this._applyTime(this.playT);
      this._pause();
      return;
    }
    this.els.scrub.value = Math.round(this.playT);
    this._applyTime(this.playT);
  },

  // Inti sinkronisasi: satu waktu tc menggerakkan scene 3D DAN video bersama.
  _applyTime(relMs) {
    if (!this.session) return;
    const tc = this.session.t0 + relMs;

    // --- posisi ROV di scene 3D ---
    const poses = this.session.poses;
    if (poses.length) {
      const i = lastIndexLE(poses, tc);
      if (i >= 0) { this.scene.setPose(poses[i]); this.scene.setProgress(i + 1); }
    }

    // --- frame video tiap kamera pada timestamp yang sama ---
    for (const role of Object.keys(this._camImgs || {})) {
      const frames = this.session.camFrames[role];
      const view = this._camImgs[role];
      if (!frames || !view) continue;
      const fi = lastIndexLE(frames, tc);
      if (fi >= 0 && fi !== view.lastIdx) {
        view.lastIdx = fi;
        view.img.src = `/replay/frame?session=${encodeURIComponent(this.session.id)}&cam=${role}&i=${fi}`;
      }
    }

    // --- label waktu ---
    this.els.time.textContent = `${fmtClock(relMs)} / ${fmtClock(this.session.duration)}`;
  },

  _injectStyle() {
    if (document.getElementById("replay-style")) return;
    const st = document.createElement("style");
    st.id = "replay-style";
    st.textContent = `
      .replay { display:flex; flex-direction:column; gap:14px; height:100%; }
      .replay__body { display:grid; grid-template-columns: 260px 1fr; gap:14px; flex:1; min-height:0; }
      .replay__sessions { display:flex; flex-direction:column; min-height:0; border:1px solid rgba(255,255,255,.08); border-radius:10px; padding:10px; background:rgba(255,255,255,.02); }
      .replay__sessions-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
      .replay__list { list-style:none; margin:0; padding:0; overflow-y:auto; flex:1; }
      .replay__item { padding:8px 10px; border-radius:8px; cursor:pointer; border:1px solid transparent; }
      .replay__item:hover { background:rgba(255,255,255,.05); }
      .replay__item.is-active { border-color:#14d8ff; background:rgba(20,216,255,.08); }
      .replay__item-id { font-weight:600; font-size:.86rem; }
      .replay__item-sub { font-size:.72rem; opacity:.65; margin-top:2px; }
      .replay__rec { color:#ff5a5a; font-weight:700; font-size:.7rem; }
      .replay__empty { padding:10px; font-size:.78rem; opacity:.6; }
      .replay__stage { display:flex; flex-direction:column; gap:12px; min-height:0; }
      .replay__cams { min-height:120px; }
      .replay__scene { position:relative; flex:1; min-height:260px; }
      .replay__novideo, .replay__camcell { min-height:120px; }
      .replay__novideo { display:flex; align-items:center; justify-content:center; opacity:.6; font-size:.8rem; border:1px dashed rgba(255,255,255,.15); border-radius:10px; }
      .replay__camcell .camcell__view img { width:100%; height:100%; object-fit:contain; background:#04121e; }
      .replay__timeline { display:flex; flex-direction:column; gap:6px; }
      .replay__transport { display:flex; align-items:center; gap:12px; }
      .replay__time { font-variant-numeric:tabular-nums; font-size:.82rem; opacity:.85; }
      .replay__meta { font-size:.74rem; opacity:.6; }
      .replay__scrub { width:100%; }
    `;
    document.head.appendChild(st);
  },
};

/* ===================== REKONSTRUKSI TRAJECTORY ===================== */
// Menghasilkan array pose {t,x,y,z,heading,roll,pitch} dari log telemetry +
// command surge/sway, memakai integrator identik mission.js.
function reconstructTrajectory(traj, cmds) {
  const samples = (traj || []).filter((s) => Number.isFinite(s.t)).sort((a, b) => a.t - b.t);
  if (!samples.length) return [];

  const surgeCmds = (cmds || []).filter((c) => c.name === "surge").sort((a, b) => a.t - b.t);
  const swayCmds = (cmds || []).filter((c) => c.name === "sway").sort((a, b) => a.t - b.t);

  let px = 0, pz = 0;         // posisi bidang dasar
  let si = 0, wi = 0;         // pointer command
  let surge = 0, sway = 0;
  const poses = [];

  for (let k = 0; k < samples.length; k++) {
    const s = samples[k];
    if (k > 0) {
      const tPrev = samples[k - 1].t;
      // command yang aktif hingga awal interval
      while (si < surgeCmds.length && surgeCmds[si].t <= tPrev) surge = numOr0(surgeCmds[si++].value);
      while (wi < swayCmds.length && swayCmds[wi].t <= tPrev) sway = numOr0(swayCmds[wi++].value);

      let dt = (s.t - tPrev) / 1000;
      if (!(dt > 0)) dt = 0;
      if (dt > 0.5) dt = 0.5; // batasi lompatan bila ada jeda telemetry

      const hr = deg2rad(numOr0(samples[k - 1].heading));
      // heading 0 = utara (-Z); timur = +X (konvensi mission.js)
      const fx = Math.sin(hr), fz = -Math.cos(hr);
      const rx = Math.cos(hr), rz = Math.sin(hr);
      px += (fx * surge + rx * sway) * VEL_SCALE * dt;
      pz += (fz * surge + rz * sway) * VEL_SCALE * dt;
    }
    const depth = numOr0(s.depth);
    poses.push({
      t: s.t,
      x: px,
      y: -depth * DEPTH_SCALE,
      z: pz,
      heading: numOr0(s.heading),
      roll: numOr0(s.roll),
      pitch: numOr0(s.pitch),
    });
  }
  return poses;
}

/* ===================== UTIL ===================== */
function numOr0(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function deg2rad(d) { return d * Math.PI / 180; }

// indeks terbesar dgn arr[i].t <= t (binary search). -1 bila semua > t.
function lastIndexLE(arr, t) {
  let lo = 0, hi = arr.length - 1, res = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].t <= t) { res = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  return res;
}

function lastT(poses, camFrames) {
  let m = poses.length ? poses[poses.length - 1].t : 0;
  for (const role of Object.keys(camFrames || {})) {
    const f = camFrames[role];
    if (f && f.length) m = Math.max(m, f[f.length - 1].t);
  }
  return m;
}

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function fetchJSONL(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  const text = await r.text();
  const out = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch (e) {}
  }
  return out;
}

function fmtClock(ms) {
  ms = Math.max(0, ms || 0);
  const total = ms / 1000;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  const d = Math.floor((ms % 1000) / 100);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${d}`;
}
function fmtBytes(b) {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
