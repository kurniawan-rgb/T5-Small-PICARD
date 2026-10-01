# Text-to-SQL UNHAS — T5-small + PICARD

Folder eksperimen aktif untuk membandingkan checkpoint T5-small yang sama pada:

- baseline beam search: `http://127.0.0.1:8201`
- PICARD Haskell `parse_with_guards`: `http://127.0.0.1:8202`
- web pengujian baseline vs PICARD: `http://127.0.0.1:8300`

## Isi repositori

```text
configs/       konfigurasi dua server
src/           source serving dan adapter PICARD
tools/         evaluator paired/resumable
web/           UI penelitian dan proxy same-origin
data/          test case 50, 100, dan 500 pertanyaan
results/       hasil evaluasi utama (50 kasus dan beam 1/2/4 pada 500 kasus)
docs/          kontrak model dan catatan eksperimen
checkpoints/   petunjuk penempatan checkpoint; bobot model tidak disertakan
database/      petunjuk penempatan SQLite; database mentah tidak disertakan
```

Repositori publik ini berisi kode dan artefak evaluasi yang penting untuk
dokumentasi penelitian. Bobot model, database mentah, backup web, log proses,
PID, dan cache tidak diunggah. Database asli memiliki kolom data pribadi;
jangan mempublikasikannya tanpa izin dan proses de-identifikasi yang sesuai.

Sebelum menjalankan layanan, tempatkan checkpoint yang kompatibel di
`checkpoints/t5-small-unhas-picard-noalias-20260917/` dan database yang
diizinkan untuk digunakan di `database/neosia/neosia.sqlite`. Jika nama
direktori checkpoint berbeda, sesuaikan `model_path` pada kedua berkas
`configs/serve_*.json`. Periksa pula kontrak pelatihan pada
`docs/model_training_contract.json`.

## Menjalankan

```bash
cd /home/ubuntu/text2sql-picard-unhas
docker compose -p text2sql_noalias_test up -d
docker compose -p text2sql_noalias_test ps
curl http://127.0.0.1:8201/dbs
curl http://127.0.0.1:8202/dbs
curl http://127.0.0.1:8300/api/health
```

## Web pengujian

Setelah `docker compose up -d`, buka web melalui SSH port forwarding:

```bash
gcloud compute ssh ubuntu@t5-sql-picard \
  --zone=asia-southeast1-c \
  -- -L 8300:127.0.0.1:8300
```

Lalu buka `http://127.0.0.1:8300` pada browser lokal. Web akan:

- mengirim satu pertanyaan ke baseline dan PICARD secara paralel;
- membandingkan SQL, hasil eksekusi, status, dan latency;
- menampilkan diff token keluaran akhir;
- menampilkan trace kandidat top-k yang benar-benar ditolak PICARD;
- membaca ringkasan evaluasi dari `results/eval_baseline_vs_picard_50.json`.

Trace decoder adalah observasi keputusan parser selama generasi. Diff token hanya
membandingkan dua SQL final; keduanya sengaja ditampilkan terpisah agar tidak
mengklaim bahwa setiap perbedaan SQL pasti merupakan token yang ditolak PICARD.

## Uji satu pertanyaan

```bash
curl 'http://127.0.0.1:8201/ask/neosia/Berapa%20jumlah%20mahasiswa%20angkatan%202022'
curl 'http://127.0.0.1:8202/ask/neosia/Berapa%20jumlah%20mahasiswa%20angkatan%202022'
```

## Evaluasi 50 kasus

```bash
python3 tools/evaluate.py \
  --cases data/test_cases_50.json \
  --database database/neosia/neosia.sqlite \
  --out results/eval_local_50.json \
  --ids {1..50} \
  --baseline-endpoint http://127.0.0.1:8201 \
  --picard-endpoint http://127.0.0.1:8202 \
  --compose-project text2sql_noalias_test \
  --picard-service picard-api-noalias \
  --restart-picard-first
```

Evaluator menulis hasil setiap kasus dan me-restart API PICARD setelah timeout
agar request lama tidak menyebabkan timeout berantai.

Hasil evaluasi yang disertakan:

- `results/eval_baseline_vs_picard_50.json`: evaluasi 50 kasus awal;
- `results/eval_beams_1_run_500.json`: beam 1, 500 kasus;
- `results/eval_run_500.json`: beam 2, 500 kasus;
- `results/eval_beams_4_run_500.json`: beam 4, 500 kasus.

Jangan menafsirkan *execution match* sebagai bukti SQL sudah benar secara
semantik pada semua kasus; periksa pula SQL dan baris hasilnya.
