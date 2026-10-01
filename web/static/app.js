const els = {
  form: document.querySelector("#queryForm"),
  question: document.querySelector("#question"),
  questionHelp: document.querySelector("#questionHelp"),
  characterCount: document.querySelector("#characterCount"),
  database: document.querySelector("#database"),
  runButton: document.querySelector("#runButton"),
  runStatus: document.querySelector("#runStatus"),
  results: document.querySelector("#results"),
  baselinePanel: document.querySelector("#baselinePanel"),
  picardPanel: document.querySelector("#picardPanel"),
  tokenAnalysis: document.querySelector("#tokenAnalysis"),
  tokenRows: document.querySelector("#tokenRows"),
  tracePanel: document.querySelector("#tracePanel"),
  traceSummary: document.querySelector("#traceSummary"),
  traceEvents: document.querySelector("#traceEvents"),
  globalHealth: document.querySelector("#globalHealth"),
  metricStrip: document.querySelector("#metricStrip"),
  beamPicker: document.querySelector("#beamPicker"),
  evaluationSource: document.querySelector("#evaluationSource"),
  pairedGrid: document.querySelector("#pairedGrid"),
  beamOverview: document.querySelector("#beamOverview tbody"),
  difficultyTable: document.querySelector("#difficultyTable"),
  commandTrigger: document.querySelector("#commandTrigger"),
  commandDialog: document.querySelector("#commandDialog"),
  commandSearch: document.querySelector("#commandSearch"),
  commandList: document.querySelector("#commandList"),
  toastRegion: document.querySelector("#toastRegion"),
};

const commands = [
  {
    label: "Jumlah mahasiswa angkatan 2022",
    meta: "prompt",
    question: "Berapa jumlah mahasiswa angkatan 2022?",
  },
  {
    label: "Mahasiswa aktif di Agroteknologi",
    meta: "prompt",
    question: "Tolong berikan jumlah mahasiswa aktif pada Program Studi Agroteknologi.",
  },
  {
    label: "Mahasiswa Teknik Mesin angkatan 2021",
    meta: "prompt",
    question: "Tolong berikan jumlah mahasiswa angkatan 2021 pada Program Studi Teknik Mesin.",
  },
  { label: "Buka arsitektur decoder", meta: "bagian", target: "#arsitektur" },
  { label: "Buka bukti evaluasi", meta: "bagian", target: "#bukti" },
];

let questionTouched = false;
let selectedCommand = 0;
let statusTimers = [];
let evaluationRuns = [];
let selectedBeam = 4;

function clearStatusTimers() {
  statusTimers.forEach((timer) => window.clearTimeout(timer));
  statusTimers = [];
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message) {
  const toast = element("div", "toast", message);
  els.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 6000);
}

function validateQuestion() {
  const value = els.question.value.trim();
  const invalid = value.length < 3;
  if (questionTouched && invalid) {
    els.question.setAttribute("aria-invalid", "true");
    els.question.removeAttribute("data-state");
    els.questionHelp.textContent = "Pertanyaan terlalu pendek. Tulis minimal 3 karakter lalu jalankan kembali.";
    els.questionHelp.dataset.tone = "error";
  } else {
    els.question.removeAttribute("aria-invalid");
    els.questionHelp.textContent = "Gunakan pertanyaan yang dapat dijawab dari skema Neosia.";
    delete els.questionHelp.dataset.tone;
  }
  return !invalid;
}

function setLoading(isLoading) {
  els.results.setAttribute("aria-busy", String(isLoading));
  els.runButton.disabled = isLoading;
  els.runButton.dataset.state = isLoading ? "loading" : "default";
  [els.baselinePanel, els.picardPanel].forEach((panel) => {
    panel.dataset.state = isLoading ? "loading" : panel.dataset.state;
    if (isLoading) {
      panel.querySelector('[data-role="status"]').textContent = "Decoding";
      panel.querySelector('[data-role="status"]').dataset.status = "loading";
      panel.querySelector('[data-role="latency"]').textContent = "—";
      const body = panel.querySelector('[data-role="body"]');
      body.replaceChildren(element("p", "empty-copy", "Menunggu respons model…"));
    }
  });

  clearStatusTimers();
  if (isLoading) {
    els.runStatus.textContent = "Dua decoder berjalan secara paralel. Jangan tutup halaman.";
    statusTimers.push(window.setTimeout(() => {
      els.runStatus.textContent = "Model masih melakukan decoding di CPU VM. Proses dapat memerlukan beberapa menit.";
    }, 10000));
    statusTimers.push(window.setTimeout(() => {
      els.runStatus.textContent = "PICARD memeriksa kandidat token ke parser pada setiap langkah; tetap menunggu respons.";
    }, 60000));
  }
}

function formatLatency(ms) {
  if (!Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function makeCopyButton(value) {
  const button = element("button", "copy-button", "Salin SQL");
  button.type = "button";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Tersalin ✓";
      button.dataset.state = "success";
      window.setTimeout(() => {
        button.textContent = "Salin SQL";
        button.dataset.state = "default";
      }, 2500);
    } catch {
      button.textContent = "Gagal menyalin";
      button.dataset.state = "error";
      showToast("SQL tidak dapat disalin. Pilih teks SQL secara manual.");
    }
  });
  return button;
}

function resultBlock(title, content, kind = "text") {
  const block = element("section", "result-block");
  const head = element("div", "result-block__head");
  head.append(element("h4", "", title));
  if (kind === "sql" && content) head.append(makeCopyButton(content));
  block.append(head);
  if (kind === "sql") {
    const pre = element("pre", "sql-code");
    const code = element("code", "", content || "SQL tidak tersedia");
    pre.append(code);
    block.append(pre);
  } else {
    block.append(element("pre", "row-output", content));
  }
  return block;
}

function renderResult(panel, result) {
  const status = panel.querySelector('[data-role="status"]');
  const latency = panel.querySelector('[data-role="latency"]');
  const body = panel.querySelector('[data-role="body"]');
  panel.dataset.state = result.ok ? "success" : "error";
  status.textContent = result.ok ? `${result.status} OK` : (result.status ? `HTTP ${result.status}` : "Tidak terhubung");
  status.dataset.status = result.ok ? "ok" : "error";
  latency.textContent = formatLatency(result.latency_ms);
  body.replaceChildren();

  if (result.query) body.append(resultBlock("SQL", result.query, "sql"));
  if (result.ok) {
    const rows = Array.isArray(result.rows) ? result.rows : [];
    body.append(resultBlock(`Hasil eksekusi · ${rows.length} baris`, JSON.stringify(rows, null, 2)));
  } else {
    const box = element("div", "error-box");
    box.append(element("strong", "", "Request tidak menghasilkan eksekusi yang valid."));
    box.append(element("span", "", result.error || "Server tidak memberikan detail kesalahan."));
    body.append(box);
  }
}

function sqlTokens(sql) {
  if (!sql) return [];
  const pattern = /'(?:''|[^'])*'|"(?:""|[^"])*"|<=|>=|<>|!=|[A-Za-z_][A-Za-z0-9_.$]*|\d+(?:\.\d+)?|[(),=*<>;+\/-]/g;
  return sql.match(pattern) || [];
}

function diffTokenSequences(left, right) {
  const rows = left.length + 1;
  const cols = right.length + 1;
  const matrix = Array.from({ length: rows }, () => new Uint16Array(cols));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      matrix[i][j] = left[i].toLowerCase() === right[j].toLowerCase()
        ? matrix[i + 1][j + 1] + 1
        : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
    }
  }

  const baseline = [];
  const picard = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i].toLowerCase() === right[j].toLowerCase()) {
      baseline.push({ token: left[i], diff: "same" });
      picard.push({ token: right[j], diff: "same" });
      i += 1;
      j += 1;
    } else if (matrix[i + 1][j] >= matrix[i][j + 1]) {
      baseline.push({ token: left[i], diff: "removed" });
      i += 1;
    } else {
      picard.push({ token: right[j], diff: "added" });
      j += 1;
    }
  }
  while (i < left.length) baseline.push({ token: left[i++], diff: "removed" });
  while (j < right.length) picard.push({ token: right[j++], diff: "added" });
  return { baseline, picard };
}

function renderTokenLine(label, tokens) {
  const row = element("div", "token-row");
  row.append(element("div", "token-row__label", label));
  const stream = element("div", "token-stream");
  tokens.forEach((item) => {
    const token = element("span", "sql-token", item.token);
    token.dataset.diff = item.diff;
    stream.append(token);
  });
  row.append(stream);
  return row;
}

function renderTokenDiff(baselineSql, picardSql) {
  if (!baselineSql || !picardSql) {
    els.tokenAnalysis.hidden = true;
    return;
  }
  const diff = diffTokenSequences(sqlTokens(baselineSql), sqlTokens(picardSql));
  els.tokenRows.replaceChildren(
    renderTokenLine("Baseline", diff.baseline),
    renderTokenLine("PICARD", diff.picard),
  );
  els.tokenAnalysis.hidden = false;
}

function traceStat(label, value) {
  const wrapper = element("div");
  wrapper.append(element("dt", "", label), element("dd", "", String(value)));
  return wrapper;
}

function renderTrace(trace) {
  if (!trace) {
    els.tracePanel.hidden = true;
    return;
  }
  els.traceSummary.replaceChildren(
    traceStat("Mode", trace.mode || "—"),
    traceStat("Kandidat diperiksa", trace.checked ?? 0),
    traceStat("Diterima", trace.accepted ?? 0),
    traceStat("Ditolak", trace.rejected ?? 0),
  );
  els.traceEvents.replaceChildren();
  const events = Array.isArray(trace.events) ? trace.events : [];
  if (!events.length) {
    els.traceEvents.append(element(
      "p",
      "empty-copy",
      "Tidak ada kandidat top-k yang ditolak pada generasi ini. PICARD tetap aktif dan memeriksa kandidat yang tercatat pada ringkasan.",
    ));
  } else {
    events.forEach((event) => {
      const row = element("div", "trace-event");
      row.append(
        element("span", "trace-event__step", `step ${event.step}`),
        element("span", "trace-event__token", `tolak ${JSON.stringify(event.token)}`),
        element("span", "trace-event__prefix", `${event.reason}: ${event.prefix || "awal sequence"}`),
      );
      els.traceEvents.append(row);
    });
  }
  els.tracePanel.hidden = false;
}

async function runComparison(event) {
  event.preventDefault();
  questionTouched = true;
  if (!validateQuestion()) {
    els.question.focus();
    return;
  }

  setLoading(true);
  els.tokenAnalysis.hidden = true;
  els.tracePanel.hidden = true;
  const started = performance.now();
  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: els.question.value.trim(),
        db_id: els.database.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Server web menolak request.");

    renderResult(els.baselinePanel, payload.baseline);
    renderResult(els.picardPanel, payload.picard);
    renderTokenDiff(payload.baseline.query, payload.picard.query);
    renderTrace(payload.picard.decoder_trace);
    els.question.dataset.state = "success";
    els.runButton.dataset.state = "success";
    const elapsed = formatLatency(Math.round(performance.now() - started));
    els.runStatus.textContent = `Perbandingan selesai dalam ${elapsed}. Periksa SQL, hasil eksekusi, diff, dan trace decoder.`;
    window.setTimeout(() => {
      els.runButton.dataset.state = "default";
      els.question.removeAttribute("data-state");
    }, 1500);
  } catch (error) {
    els.runButton.dataset.state = "error";
    els.runStatus.textContent = `Perbandingan gagal: ${error.message}`;
    showToast(`Perbandingan gagal. ${error.message}`);
  } finally {
    clearStatusTimers();
    els.runButton.disabled = false;
    els.results.setAttribute("aria-busy", "false");
  }
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    const online = health.baseline?.ok && health.picard?.ok;
    els.globalHealth.dataset.state = online ? "online" : "offline";
    els.globalHealth.lastChild.textContent = online ? " Kedua server online" : " Server perlu diperiksa";
  } catch {
    els.globalHealth.dataset.state = "offline";
    els.globalHealth.lastChild.textContent = " Status tidak tersedia";
  }
}

function metric(label, value, note) {
  const wrapper = element("dl", "metric");
  wrapper.append(element("dt", "", label));
  const dd = element("dd", "", value);
  dd.append(element("span", "", note));
  wrapper.append(dd);
  return wrapper;
}

function percent(count, total) {
  return `${((count / total) * 100).toFixed(1).replace(".", ",")}%`;
}

function pairedItem(label, value, note) {
  const item = element("dl", "paired-item");
  item.append(element("dt", "", label));
  item.append(element("dd", "", String(value)));
  if (note) item.append(element("p", "", note));
  return item;
}

function comparisonCell(before, after, unit = "") {
  return `${before}${unit} → ${after}${unit}`;
}

function seconds(value) {
  return Number(value).toFixed(3).replace(".", ",");
}

function renderEvaluation(run) {
  const total = run.baseline.n;
  els.beamPicker.querySelectorAll("button[data-beam]").forEach((button) => {
    button.setAttribute("aria-pressed", String(Number(button.dataset.beam) === run.beam));
  });
  els.evaluationSource.textContent = `Beam ${run.beam} · ${total} kasus · sumber: results/${run.source_file}`;
  els.metricStrip.replaceChildren(
    metric("API success", comparisonCell(run.baseline.api_success, run.picard.api_success), `${percent(run.baseline.api_success, total)} → ${percent(run.picard.api_success, total)} · baseline → PICARD`),
    metric("Exact match", comparisonCell(run.baseline.exact_match, run.picard.exact_match), `${percent(run.baseline.exact_match, total)} → ${percent(run.picard.exact_match, total)}`),
    metric("Execution match", comparisonCell(run.baseline.execution_match, run.picard.execution_match), `${percent(run.baseline.execution_match, total)} → ${percent(run.picard.execution_match, total)}`),
    metric("Tambahan latensi", `+${seconds(run.picard.mean_latency_s - run.baseline.mean_latency_s)} s`, `${seconds(run.baseline.mean_latency_s)} s → ${seconds(run.picard.mean_latency_s)} s · rerata`),
  );

  const paired = run.paired;
  els.pairedGrid.replaceChildren(
    pairedItem("Keduanya benar", paired.both_correct, "EX baseline dan PICARD benar"),
    pairedItem("Hanya PICARD benar", paired.improved, "Kenaikan EX otomatis, bukan audit semantik"),
    pairedItem("Hanya baseline benar", paired.regressed, "Regresi EX pada beam yang sama"),
    pairedItem("Keduanya salah", paired.both_wrong, "EX keduanya tidak cocok dengan acuan"),
  );

  const header = element("div", "difficulty-row difficulty-row--head");
  ["Difficulty", "Jumlah", "Baseline exec", "PICARD exec"].forEach((text) => header.append(element("span", "", text)));
  els.difficultyTable.replaceChildren(header);
  Object.entries(run.difficulties).forEach(([name, values]) => {
    const row = element("div", "difficulty-row");
    row.append(element("strong", "", name));
    const n = element("span", "", String(values.n));
    n.dataset.label = "Jumlah";
    const baseline = element("span", "", `${values.baseline_execution}/${values.n}`);
    baseline.dataset.label = "Baseline exec";
    const picard = element("span", "", `${values.picard_execution}/${values.n}`);
    picard.dataset.label = "PICARD exec";
    row.append(n, baseline, picard);
    els.difficultyTable.append(row);
  });

  els.beamOverview.replaceChildren();
  evaluationRuns.forEach((item) => {
    const tr = element("tr");
    if (item.beam === run.beam) tr.dataset.selected = "true";
    [
      String(item.beam),
      comparisonCell(item.baseline.api_success, item.picard.api_success),
      comparisonCell(item.baseline.exact_match, item.picard.exact_match),
      comparisonCell(item.baseline.execution_match, item.picard.execution_match),
      comparisonCell(seconds(item.baseline.mean_latency_s), seconds(item.picard.mean_latency_s), " s"),
    ].forEach((value) => tr.append(element("td", "", value)));
    els.beamOverview.append(tr);
  });
}

async function loadEvaluation() {
  try {
    const response = await fetch("/api/evaluation");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Hasil evaluasi belum tersedia");
    evaluationRuns = data.runs || [];
    if (evaluationRuns.length !== 3) throw new Error("Tiga skenario beam belum lengkap.");
    renderEvaluation(evaluationRuns.find((run) => run.beam === selectedBeam) || evaluationRuns[0]);
  } catch (error) {
    els.metricStrip.replaceChildren(element("p", "error-box", error.message));
    els.evaluationSource.textContent = "Data evaluasi belum dapat dimuat.";
  }
}

function filteredCommands() {
  const query = els.commandSearch.value.trim().toLowerCase();
  return commands.filter((command) => command.label.toLowerCase().includes(query));
}

function executeCommand(command) {
  els.commandDialog.close();
  if (command.question) {
    els.question.value = command.question;
    els.characterCount.textContent = `${command.question.length} / 500`;
    els.question.removeAttribute("aria-invalid");
    delete els.questionHelp.dataset.tone;
    document.querySelector("#uji").scrollIntoView();
    window.setTimeout(() => els.question.focus({ preventScroll: true }), 0);
  } else if (command.target) {
    document.querySelector(command.target)?.scrollIntoView();
  }
}

function renderCommands() {
  const items = filteredCommands();
  if (selectedCommand >= items.length) selectedCommand = Math.max(0, items.length - 1);
  els.commandList.replaceChildren();
  items.forEach((command, index) => {
    const item = element("button", "command-item");
    item.type = "button";
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(index === selectedCommand));
    item.append(element("span", "", command.label), element("span", "", command.meta));
    item.addEventListener("click", () => executeCommand(command));
    els.commandList.append(item);
  });
  if (!items.length) els.commandList.append(element("p", "empty-copy", "Tidak ada perintah yang cocok."));
}

function openCommandDialog() {
  selectedCommand = 0;
  els.commandSearch.value = "";
  renderCommands();
  els.commandDialog.showModal();
  window.setTimeout(() => els.commandSearch.focus(), 0);
}

els.form.addEventListener("submit", runComparison);
els.beamPicker.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-beam]");
  if (!button || !els.beamPicker.contains(button)) return;
  const run = evaluationRuns.find((item) => item.beam === Number(button.dataset.beam));
  if (!run) return;
  selectedBeam = run.beam;
  renderEvaluation(run);
});
els.question.addEventListener("blur", () => {
  questionTouched = true;
  validateQuestion();
});
els.question.addEventListener("input", () => {
  els.characterCount.textContent = `${els.question.value.length} / 500`;
  if (questionTouched) validateQuestion();
});
els.commandTrigger.addEventListener("click", openCommandDialog);
els.commandSearch.addEventListener("input", () => {
  selectedCommand = 0;
  renderCommands();
});
els.commandSearch.addEventListener("keydown", (event) => {
  const items = filteredCommands();
  if (event.key === "ArrowDown") {
    event.preventDefault();
    selectedCommand = items.length ? (selectedCommand + 1) % items.length : 0;
    renderCommands();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    selectedCommand = items.length ? (selectedCommand - 1 + items.length) % items.length : 0;
    renderCommands();
  } else if (event.key === "Enter" && items[selectedCommand]) {
    event.preventDefault();
    executeCommand(items[selectedCommand]);
  }
});
els.commandDialog.addEventListener("click", (event) => {
  if (event.target === els.commandDialog) els.commandDialog.close();
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (els.commandDialog.open) els.commandDialog.close();
    else openCommandDialog();
  }
});

renderCommands();
loadHealth();
loadEvaluation();
