# coding=utf-8
"""
patch_pipeline.py — EZ-PICARD serving fixes (dijalankan SETIAP container start).

Fix 1: input truncation 512 (menyamakan training max_source_length=512).
   Pipeline serving default `truncation=DO_NOT_TRUNCATE`; input skema+pertanyaan
   (518 token) melebihi 512 -> output generasi degenerasi.
   Diubah ke LONGEST_FIRST + max_length=512, persis training.

Fix 2: server PICARD (build Maret 2022) tidak bisa mem-parse post_processor
   TemplateProcessing di tokenizer JSON -> registerTokenizer rusak senyap ->
   semua token ditolak -> query selalu kosong.
   Solusi: input encoder TETAP ber-EOS (cocok training), tetapi JSON yang
   dikirim ke picard di-strip post_processor-nya (picard hanya butuh vocab).
"""
p = "/app/seq2seq/utils/pipeline.py"
s = open(p).read()

n1 = s.count("truncation=TruncationStrategy.DO_NOT_TRUNCATE")
s = s.replace(
    "truncation=TruncationStrategy.DO_NOT_TRUNCATE",
    "truncation=TruncationStrategy.LONGEST_FIRST",
)
n2 = s.count("return_tensors=self.framework)")
s = s.replace(
    "return_tensors=self.framework)",
    "return_tensors=self.framework, max_length=512)",
)
open(p, "w").write(s)

w = "/app/seq2seq/utils/picard_model_wrapper.py"
t = open(w).read()
old = "json_str = tokenizer.backend_tokenizer.to_str(pretty=False)"
new = (
    "import json as _json\n"
    "        _tok_json = _json.loads(tokenizer.backend_tokenizer.to_str(pretty=False))\n"
    "        _tok_json['post_processor'] = None\n"
    "        json_str = _json.dumps(_tok_json)"
)
n3 = t.count(old)
t = t.replace(old, new)
open(w, "w").write(t)

print(f"patch_ok pipeline={n1}/{n2} picard_wrapper={n3}")