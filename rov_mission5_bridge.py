from pathlib import Path

src = Path("/mnt/data/Pasted code(3).py")
dst = Path("/mnt/data/rov_agent_mission_sequence.py")

text = src.read_text(encoding="utf-8")

old_cases_start = text.index("CUSTOM_CASES = [")
old_cases_end = text.index("\n]\n\n\nDEFAULT_MOTION_CONFIG", old_cases_start) + 2

new_cases = '''CUSTOM_CASES = [
    # 1. MAJU 3 DETIK
    {
        "name": "M1_FORWARD",
        "duration_ms": 5000,
        "motion": (100, 0, 0, 0.35, "hold"),
    },

    # 2. BELOK 90 DERAJAT
    {
        "name": "M2_TURN_90",
        "duration_ms": 3000,
        "motion": (0, 0, 90, 0.35, "hold"),
    },

    # 3. MAJU 3 DETIK
    {
        "name": "M3_FORWARD",
        "duration_ms": 1000,
        "motion": (60, 0, 90, 0.35, "hold"),
    },

    # 4. BELOK 180 DERAJAT
    {
        "name": "M4_TURN_180",
        "duration_ms": 3000,
        "motion": (0, 0, 180, 0.35, "hold"),
    },

    # 5. MAJU 3 DETIK
    {
        "name": "M5_FORWARD",
        "duration_ms": 2000,
        "motion": (60, 0, 180, 0.35, "hold"),
    },

    # 6. GRIPPER MENCAPIT
    {
        "name": "M6_GRIP",
        "duration_ms": 1000,
        "motion": (0, 0, 180, 0.35, "close"),
    },

    # 7. MUNDUR 1 DETIK
    {
        "name": "M7_REVERSE",
        "duration_ms": 1000,
        "motion": (-60, 0, 180, 0.35, "hold"),
    },

    # 8. NAIK KE PERMUKAAN
    {
        "name": "M8_SURFACE",
        "duration_ms": 10000,
        "motion": (0, 0, 180, 0.00, "hold"),
    },
]'''

text = text[:old_cases_start] + new_cases + text[old_cases_end:]

# Remove the old CUSTOM -> Mission5FSM handoff block.
old_handoff = '''            # Langkah 1-2 tuntas → serahkan ke FSM untuk langkah 3-8, membawa
            # heading CASE terakhir sbg acuan yang ditahan selagi mencari hook.
            # Cek _custom_stop LAGI di sini: kill-switch yang menyala tepat di
            # batas serah terima tak boleh malah memulai FSM.
            if self._custom_state == "COMPLETE" and not self._custom_stop.is_set():
                self._log("[CUSTOM] CASE selesai → serah terima ke FSM "
                          f"(M5_YOLO_SEARCH, heading_hold={self._last_case_heading})")
                with self._lock:
                    self._thread = None      # lepaskan slot agar _start_fsm bisa mulai
                self._start_fsm("M5_YOLO_SEARCH", heading_hold=self._last_case_heading)
'''
if old_handoff not in text:
    raise RuntimeError("Blok handoff CUSTOM -> Mission5FSM tidak ditemukan.")

text = text.replace(old_handoff, '''            # CUSTOM_CASES adalah seluruh misi.
            # Setelah M8_SURFACE selesai, tidak ada serah-terima ke Mission5FSM.
''')

# On successful completion, stop and disarm. On abort/error this also guarantees
# the thrusters are stopped and the vehicle is disarmed.
old_final = '''                # Sukses -> JANGAN disarm: FSM langkah 3-8 mengambil alih dalam
                # hitungan detik dan akan arm sendiri; disarm di sini hanya
                # menciptakan jeda mati di tengah serah terima. Batal/error ->
                # wajib disarm, jangan tinggalkan wahana hidup tanpa pengendali.
                if self._custom_state != "COMPLETE" or self._custom_stop.is_set():
                    self._cmd.arm(False)
'''
if old_final not in text:
    raise RuntimeError("Blok final arm/disarm tidak ditemukan.")

text = text.replace(old_final, '''                # CUSTOM_CASES adalah seluruh misi.
                # Setelah semua tahap selesai, ROV berhenti dan DISARM.
                self._cmd.stop_all()
                self._cmd.arm(False)
''')

# Update comments so they no longer describe the old CASE->FSM architecture.
text = text.replace(
    '# True  = jalankan CASE di bawah (langkah 1-2 misi 5) lalu SERAHKAN ke Mission5FSM\n'
    '#         di M5_YOLO_SEARCH untuk langkah 3-8 (YOLO → ujung J → QR → grip →\n'
    '#         unhook → surface). Ini jalur lomba.\n'
    '# False = langsung Mission5FSM dari cfg["start_state"], tanpa CASE.',
    '# True  = jalankan seluruh urutan CUSTOM_CASES sebagai satu misi mandiri.\n'
    '#         Tidak ada serah-terima ke Mission5FSM setelah CASE selesai.\n'
    '# False = jalankan Mission5FSM dari cfg["start_state"], tanpa CUSTOM_CASES.'
)

# Update start() comments to match the new behavior.
text = text.replace(
    '        # CASE menjalankan langkah 1-2 lalu MERANTAI sendiri ke _start_fsm()\n'
    '        # (lihat _start_custom.run) — bukan lagi jalur alternatif yang buntu.\n',
    '        # Jika CUSTOM mode aktif, seluruh misi dijalankan oleh CUSTOM_CASES.\n'
)

dst.write_text(text, encoding="utf-8")

print(f"File full berhasil dibuat: {dst}")
print(f"Jumlah baris: {len(text.splitlines())}")
print("Urutan: maju 3s → 90° → maju 3s → 180° → maju 3s → grip → mundur 1s → naik → stop + disarm")
