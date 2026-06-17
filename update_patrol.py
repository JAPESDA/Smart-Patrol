#!/usr/bin/env python3
"""
update_patrol.py — Script update data Smart Patrol PCI JAPESDA
=============================================================
Jalankan setelah setiap sesi patroli baru selesai.
Memproses file GPX (rute) dan CSV SMART (temuan) lalu
menambahkannya ke data/tracks.geojson dan data/findings.geojson.

CARA PAKAI:
  python update_patrol.py

Script akan memandu kamu langkah demi langkah.

ATAU langsung dengan argumen:
  python update_patrol.py --gpx Tracking_XX.gpx --id SP_017 \\
         --desa DS001 --leader "Alis Mooduto" --hari 5       \\
         --mulai 2026-07-14 --selesai 2026-07-18             \\
         --area 350 --csv observasi_SP017.csv
"""

import json, math, sys, os, argparse, csv
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

# ── Konfigurasi ────────────────────────────────────────────────────────
DATA_DIR     = Path(__file__).parent / "data"
TRACKS_FILE  = DATA_DIR / "tracks.geojson"
FINDINGS_FILE= DATA_DIR / "findings.geojson"
META_FILE    = DATA_DIR / "meta.json"

TRACK_COLORS = {
    "DS001": "#4a90d9",
    "DS002": "#2dd4bf",
}
DESA_NAME = {"DS001": "Ilomata", "DS002": "Suka Makmur"}
NS = "http://www.topografix.com/GPX/1/1"

MAX_SPEED_MS = 15   # m/s — titik lebih cepat dari ini dihapus (GPS spike)

# ── Helper geometri ────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def simplify(coords, max_pts=300):
    if len(coords) <= max_pts:
        return coords
    step = len(coords) // max_pts
    s = coords[::step]
    if s[-1] != coords[-1]:
        s.append(coords[-1])
    return s

# ── Proses GPX ─────────────────────────────────────────────────────────
def process_gpx(gpx_path):
    """
    Baca GPX, bersihkan data, dan kembalikan list segmen koordinat.
    Tiap <trkseg> → satu segmen (sudah dipisah per hari oleh SMART).
    """
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    trks = root.findall(f"{{{NS}}}trk")

    if len(trks) == 0:
        print("⚠  Tidak ada <trk> ditemukan di file GPX ini.")
        return [], 0, 0

    # Kalau file gabungan (banyak trk), ambil yang tanggal patroli sesuai
    # Kalau file satu sesi (1 trk), langsung proses
    print(f"   File GPX: {len(trks)} track, mengambil track pertama")
    trk = trks[0]
    segs_raw = trk.findall(f"{{{NS}}}trkseg")

    clean_segs = []
    n_raw = n_removed = 0

    for seg in segs_raw:
        pts = []
        for p in seg.findall(f"{{{NS}}}trkpt"):
            t_el = p.find(f"{{{NS}}}time")
            t = parse_time(t_el.text) if t_el is not None else None
            if t:
                pts.append((t, float(p.get("lat")), float(p.get("lon"))))

        if not pts:
            continue

        pts.sort(key=lambda x: x[0])
        n_raw += len(pts)

        # Hapus duplikat (dt=0, d≈0) dan GPS spike
        cleaned = [pts[0]]
        for j in range(1, len(pts)):
            t_p, la_p, lo_p = cleaned[-1]
            t_c, la_c, lo_c = pts[j]
            dt = (t_c - t_p).total_seconds()
            d  = haversine(la_p, lo_p, la_c, lo_c)

            if dt == 0 and d < 0.5:
                n_removed += 1
                continue
            if dt <= 0:
                n_removed += 1
                continue
            if dt > 0 and (d / dt) > MAX_SPEED_MS and d > 50:
                n_removed += 1
                continue
            cleaned.append(pts[j])

        if len(cleaned) >= 2:
            coords = [[c[2], c[1]] for c in cleaned]  # [lon, lat]
            clean_segs.append(simplify(coords))

    n_clean = sum(len(s) for s in clean_segs)
    print(f"   GPX: {n_raw} titik asli → {n_removed} dihapus → {n_clean} titik bersih, {len(clean_segs)} segmen")
    return clean_segs, n_raw, n_removed

# ── Proses CSV SMART ───────────────────────────────────────────────────
def process_smart_csv(csv_path, sp_id):
    """
    Baca CSV ekspor SMART Desktop, filter sesi sp_id,
    dan kembalikan list findings siap masuk GeoJSON.
    """
    findings = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            patrol = row.get("Patrol ID", "").strip()
            if patrol != sp_id:
                continue
            # Skip baris Posisi (GPS track points)
            cat0 = row.get("Observation Category 0", "").strip()
            if cat0 == "Posisi" or not cat0:
                continue

            try:
                lon = float(row.get("X", 0) or 0)
                lat = float(row.get("Y", 0) or 0)
            except (ValueError, TypeError):
                continue

            if not lon or not lat:
                continue

            is_anc = row.get("Perlu tindak lanjut", "").strip().lower() in ("ya","yes","true","1")
            findings.append({
                "type": "Feature",
                "properties": {
                    "id_obs"       : row.get("Waypoint ID", ""),
                    "id_patroli"   : sp_id,
                    "id_desa"      : "",  # isi manual jika perlu
                    "tanggal"      : row.get("Waypoint Date", ""),
                    "kategori0"    : cat0,
                    "kategori1"    : row.get("Observation Category 1", ""),
                    "jenis_satwa"  : row.get("Jenis satwa", ""),
                    "jenis_tanaman": row.get("Jenis Tanaman", ""),
                    "jumlah"       : row.get("Jumlah", ""),
                    "is_ancaman"   : is_anc,
                    "pelanggaran"  : row.get("Pelanggaran", ""),
                    "keterangan"   : row.get("Keterangan", ""),
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]}
            })

    print(f"   CSV SMART: {len(findings)} temuan ditemukan untuk {sp_id}")
    return findings

# ── Load / Save JSON ───────────────────────────────────────────────────
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"   ✓ Tersimpan: {path.name} ({path.stat().st_size // 1024} KB)")

# ── Input interaktif ───────────────────────────────────────────────────
def ask(prompt, default=None, required=True):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {prompt}{suffix}: ").strip()
    if not val and default:
        return default
    if not val and required:
        print("  ⚠  Kolom ini wajib diisi.")
        return ask(prompt, default, required)
    return val or ""

def ask_int(prompt, default=None):
    val = ask(prompt, str(default) if default is not None else None)
    try:
        return int(val)
    except ValueError:
        return default or 0

def ask_float(prompt, default=None):
    val = ask(prompt, str(default) if default is not None else None)
    try:
        return float(val)
    except ValueError:
        return default or 0.0

# ── Foto helper ───────────────────────────────────────────────────────
def drive_url(raw):
    """Convert Drive share link ke direct image URL."""
    import re
    if not raw or not raw.strip():
        return ''
    m = re.search(r'/d/([a-zA-Z0-9_\-]+)', raw)
    if m:
        return f"https://drive.google.com/uc?export=view&id={m.group(1)}"
    return raw.strip()

def print_drive_guide():
    print()
    print("  CARA MENDAPATKAN LINK FOTO DARI GOOGLE DRIVE:")
    print("  1. Upload foto ke Google Drive")
    print("  2. Klik kanan foto → 'Get link'")
    print("  3. Ganti permission ke 'Anyone with the link'")
    print("  4. Klik 'Copy link'")
    print("  5. Paste link di sini")
    print("  Format: https://drive.google.com/file/d/{FILE_ID}/view")
    print()

# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Update data Smart Patrol PCI JAPESDA")
    parser.add_argument("--gpx",      help="Path file GPX dari SMART Mobile")
    parser.add_argument("--csv",      help="Path CSV ekspor SMART Desktop (opsional)")
    parser.add_argument("--id",       help="ID Patroli (misal: SP_017)")
    parser.add_argument("--desa",     help="ID Desa (DS001 atau DS002)")
    parser.add_argument("--leader",   help="Nama leader patroli")
    parser.add_argument("--hari",     type=int, help="Jumlah hari patroli")
    parser.add_argument("--personel", type=int, help="Jumlah personel")
    parser.add_argument("--area",     type=float, help="Area dipatroli (Ha)")
    parser.add_argument("--mulai",    help="Tanggal mulai (YYYY-MM-DD)")
    parser.add_argument("--selesai",  help="Tanggal selesai (YYYY-MM-DD)")
    parser.add_argument("--tahun",    help="Tahun program (Y1/Y2/Y3)")
    parser.add_argument("--kuartal",  help="Kuartal (Q1/Q2/Q3/Q4)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  🌿  UPDATE DATA SMART PATROL — PCI JAPESDA")
    print("=" * 60)
    print()

    # ── Kumpulkan info sesi ──────────────────────────────────────
    print("─ INFO SESI PATROLI ─────────────────────────────────")

    sp_id    = args.id      or ask("ID Patroli (SP_017, dst)")
    tahun    = args.tahun   or ask("Tahun Program", "Y2")
    kuartal  = args.kuartal or ask("Kuartal", "Q3")
    desa     = args.desa    or ask("ID Desa (DS001 / DS002)")
    leader   = args.leader  or ask("Nama Leader")
    mulai    = args.mulai   or ask("Tanggal Mulai (YYYY-MM-DD)")
    selesai  = args.selesai or ask("Tanggal Selesai (YYYY-MM-DD)")
    hari     = args.hari    or ask_int("Jumlah Hari Patroli", 5)
    personel = args.personel or ask_int("Jumlah Personel", 6)
    area_ha  = args.area    or ask_float("Luas Area Dipatroli (Ha, dari QGIS)", 0)

    print()
    print("─ FOTO (Opsional) ───────────────────────────────────────")
    print_drive_guide()
    foto_tim   = ask("Link foto tim patroli (Google Drive)", required=False)
    foto_kover = ask("Link foto kover/thumbnail sesi (opsional)", required=False)

    nama_desa = DESA_NAME.get(desa, desa)
    color     = TRACK_COLORS.get(desa, "#aaaaaa")

    print()
    print("─ FILE GPX (RUTE GPS) ───────────────────────────────")

    gpx_path = args.gpx or ask("Path file GPX", required=False)
    clean_segs = []
    n_raw = n_removed = 0

    if gpx_path and os.path.exists(gpx_path):
        clean_segs, n_raw, n_removed = process_gpx(gpx_path)
    elif gpx_path:
        print(f"  ⚠  File tidak ditemukan: {gpx_path} — track dilewati")

    print()
    print("─ FILE CSV SMART (TEMUAN) ───────────────────────────")

    csv_path = args.csv or ask("Path CSV SMART Desktop (kosongkan jika tidak ada)", required=False)
    new_findings = []

    if csv_path and os.path.exists(csv_path):
        new_findings = process_smart_csv(csv_path, sp_id)
    elif csv_path:
        print(f"  ⚠  File tidak ditemukan: {csv_path} — temuan dilewati")
    else:
        print("  → Tidak ada CSV temuan. Titik temuan bisa ditambah manual nanti.")

    # ── Hitung statistik temuan ──────────────────────────────────
    from collections import Counter
    cat_counts = Counter(f["properties"]["kategori0"] for f in new_findings)
    n_ancaman  = sum(1 for f in new_findings if f["properties"]["is_ancaman"])
    n_am       = cat_counts.get("Aktivitas Manusia", 0)
    n_satwa    = cat_counts.get("Patroli - Satwa Liar", 0)

    print()
    print("─ RINGKASAN ─────────────────────────────────────────")
    print(f"  Sesi      : {sp_id} ({tahun}{kuartal})")
    print(f"  Desa      : {nama_desa} ({desa})")
    print(f"  Tanggal   : {mulai} – {selesai} ({hari} hari)")
    print(f"  Leader    : {leader}")
    print(f"  Personel  : {personel} orang | Area: {area_ha} Ha")
    print(f"  Track GPS : {n_raw} titik asli → {len(clean_segs)} segmen bersih")
    print(f"  Temuan    : {len(new_findings)} total ({n_satwa} satwa, {n_am} aktv.manusia, {n_ancaman} ancaman)")
    if foto_tim:    print(f"  Foto tim  : {foto_tim[:60]}...")
    if foto_kover:  print(f"  Foto kover: {foto_kover[:60]}...")
    print()

    confirm = input("  Lanjutkan simpan? (y/n): ").strip().lower()
    if confirm not in ("y", "yes", "ya"):
        print("  ✗ Dibatalkan.")
        return

    print()
    print("─ MENYIMPAN ─────────────────────────────────────────")

    # ── Update tracks.geojson ────────────────────────────────────
    if clean_segs:
        tracks_gj = load_json(TRACKS_FILE)

        # Cek apakah SP ini sudah ada (update) atau baru (append)
        existing_ids = [f["properties"]["id_patroli"] for f in tracks_gj["features"]]
        if sp_id in existing_ids:
            print(f"  ⚠  {sp_id} sudah ada → menimpa track lama")
            tracks_gj["features"] = [f for f in tracks_gj["features"]
                                      if f["properties"]["id_patroli"] != sp_id]

        geom_type   = "MultiLineString" if len(clean_segs) > 1 else "LineString"
        geom_coords = clean_segs if len(clean_segs) > 1 else clean_segs[0]
        n_final     = sum(len(s) for s in clean_segs)

        tracks_gj["features"].append({
            "type": "Feature",
            "properties": {
                "id_patroli"    : sp_id,
                "desa"          : desa,
                "nama_desa"     : nama_desa,
                "leader"        : leader,
                "hari"          : hari,
                "area_ha"       : area_ha,
                "tahun"         : tahun,
                "kuartal"       : kuartal,
                "tgl_mulai"     : mulai,
                "tgl_selesai"   : selesai,
                "n_segmen"      : len(clean_segs),
                "n_titik_bersih": n_final,
                "foto_tim"      : foto_tim or '',
                "foto_kover"    : foto_kover or '',
            },
            "geometry": {"type": geom_type, "coordinates": geom_coords}
        })
        save_json(TRACKS_FILE, tracks_gj)

    # ── Update findings.geojson ──────────────────────────────────
    if new_findings:
        findings_gj = load_json(FINDINGS_FILE)

        # Hapus findings lama untuk sesi ini jika ada
        existing_obs = [f for f in findings_gj["features"]
                        if f["properties"]["id_patroli"] != sp_id]
        findings_gj["features"] = existing_obs + new_findings
        save_json(FINDINGS_FILE, findings_gj)

    # ── Update meta.json ─────────────────────────────────────────
    meta = load_json(META_FILE)
    tracks_gj_check = load_json(TRACKS_FILE)
    findings_gj_check = load_json(FINDINGS_FILE)

    meta["update_terakhir"] = datetime.now().strftime("%Y-%m-%d")
    meta["sesi_terakhir"]   = sp_id
    meta["total_sesi"]      = len(tracks_gj_check["features"])
    meta["total_temuan"]    = len(findings_gj_check["features"])
    meta["periode"]         = f"Y1–{tahun} (s/d {selesai})"
    save_json(META_FILE, meta)

    print()
    print("=" * 60)
    print(f"  ✅  {sp_id} berhasil ditambahkan!")
    print()
    print("  LANGKAH SELANJUTNYA:")
    print("  1. Buka Google Sheets → tab SP_Sesi")
    print(f"     Tambah baris: {sp_id} | {tahun} | {kuartal} | {desa} | "
          f"{mulai} | {selesai} | {leader} | {hari} | {personel} | {area_ha}")
    if new_findings:
        print(f"  2. Tab SP_Temuan → tambah {n_am} baris Aktivitas Manusia")
    print("  3. Push ke GitHub:")
    print("     git add data/tracks.geojson data/findings.geojson data/meta.json")
    print(f"    git commit -m 'Add {sp_id}: {nama_desa}, {hari} hari, {len(new_findings)} temuan'")
    print("     git push")
    print("  4. Dashboard update otomatis dalam ~1 menit 🎉")
    print("=" * 60)
    print()

if __name__ == "__main__":
    main()
