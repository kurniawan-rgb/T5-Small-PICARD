# Catatan eksperimen

## Tujuan

Membandingkan checkpoint T5-small yang sama pada dua strategi decoding:

1. baseline beam search tanpa PICARD;
2. beam search dengan PICARD Haskell `parse_with_guards`.

Perbedaan kedua sistem hanya pada constrained decoding. Model, tokenizer,
schema, database, beam size, dan test case dibuat sama.

## Konfigurasi

| Parameter | Nilai |
|---|---|
| Test case | 50 |
| Database ID | `neosia` |
| `num_beams` | 2 |
| `max_source_length` | 512 |
| `max_target_length` saat serving | 320 |
| `max_target_length` saat training | 192 |
| PICARD mode | `parse_with_guards` |
| PICARD schedule | `incremental` |
| Token PICARD yang diperiksa | 2 |
| Timeout per request | 180 detik |
| Perangkat | CPU |

## Hasil

| Metrik | Baseline | PICARD | Selisih PICARD |
|---|---:|---:|---:|
| API success | 38/50 | 44/50 | +6 |
| Exact match | 38/50 | 38/50 | 0 |
| Execution match | 38/50 | 42/50 | +4 |
| Mean latency | 23,810 s | 66,025 s | +42,215 s |

### Berdasarkan tingkat kesulitan

| Tingkat | Jumlah | Baseline execution | PICARD execution |
|---|---:|---:|---:|
| Easy | 18 | 18 | 18 |
| Medium | 22 | 20 | 20 |
| Hard | 10 | 0 | 4 |

PICARD memperbaiki ID 41, 43, 46, dan 47. Keempat prediksi tersebut tidak exact
match, tetapi menghasilkan baris yang sama dengan gold SQL. Tidak ada regresi
execution match pada kasus yang berhasil di baseline.

PICARD masih gagal secara execution pada ID 19, 33, 42, 44, 45, 48, 49, dan 50.
ID 48 memiliki gold target sekitar 266 token, sedangkan checkpoint dilatih
dengan batas 192 token. Menambah batas inference ke 320 tidak dapat memulihkan
informasi target yang sudah terpotong saat training.

## Interpretasi

- Execution accuracy naik dari 76% menjadi 84% pada sampel ini.
- Peningkatan muncul pada empat kasus hard.
- Exact match tidak naik karena SQL alternatif dapat benar secara semantik.
- PICARD rata-rata sekitar 2,77 kali lebih lambat pada VM CPU.
- PICARD membatasi struktur decoding, tetapi tidak menjamin maksud semantik
  pertanyaan selalu benar.

Pada empat pasangan diskordan seluruh perubahan mengarah dari baseline salah ke
PICARD benar. Uji McNemar exact dua sisi menghasilkan `p = 0,125`; hasil ini
belum signifikan pada ambang 0,05. Gunakan frasa “menunjukkan peningkatan pada
sampel pengujian”, bukan klaim peningkatan yang sudah terbukti secara statistik.

## Prosedur timeout

Client timeout tidak selalu menghentikan generation di server. Evaluator dalam
`tools/evaluate.py` me-restart `picard-api` setelah timeout dan menunggu endpoint
sehat sebelum mengirim kasus berikutnya. Ini mencegah request tertinggal membuat
kasus berikutnya ikut timeout.

## Validitas yang perlu diperiksa

Sebelum hasil dipakai sebagai klaim akhir:

1. pastikan pertanyaan dan gold SQL test tidak terdapat pada train/dev;
2. dokumentasikan hash checkpoint, tokenizer, database, dan dataset;
3. jalankan evaluasi tambahan pada set held-out yang lebih besar;
4. fine-tune ulang dengan batas target minimal 320 tanpa truncation label;
5. laporkan execution match, exact match, latency, error, dan timeout.
