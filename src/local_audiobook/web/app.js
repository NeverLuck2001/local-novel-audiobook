"use strict";

const STORAGE_KEY = "local-audiobook-studio-v1";
const elements = Object.fromEntries([...document.querySelectorAll("[id]")].map(element => [element.id, element]));
const state = {
  info: null,
  files: [],
  selected: new Set(),
  jobs: [],
  external: [],
  queueBlocked: false,
  activeId: null,
  detail: null,
  plan: null,
  planSignature: null,
  mode: "preset",
  reference: null,
  refreshing: false,
  mutation: false,
  connected: false,
  audioUrl: null,
  saved: null,
  showArchived: false,
};

const voiceDescriptions = {
  Serena: "温暖、温柔的中文女声",
  Vivian: "明亮、有个性的中文女声",
  Ryan: "有节奏感的英语男声",
  Aiden: "清晰、自然的英语男声",
  Eric: "活泼的四川口音男声",
  Dylan: "清亮的北京口音男声",
  Uncle_Fu: "沉稳、醇厚的中文男声",
  Ono_Anna: "轻快的日语女声",
  Sohee: "温暖的韩语女声",
};

const styleInstructions = {
  gentle: "自然、温柔、平静的小说旁白，语速稍慢，避免夸张表演。",
  story: "声音沉稳，叙述清晰，节奏从容，适度停顿，保持长篇小说旁白的自然感。",
  dialogue: "自然讲述小说，旁白平静，对白根据上下文表达适度情绪，保持同一朗读者的声线，不要夸张表演。",
};

const statuses = {
  queued: "排队中",
  running: "正在生成",
  pending: "待继续",
  paused: "已暂停",
  failed: "需要处理",
  completed: "已完成",
  preparing: "正在准备",
  stopping: "正在保存进度",
  pausing: "正在保存进度",
  cancelling: "正在取消",
  cancelled: "已取消",
};

const phases = {
  reusing: "准备兼容语音缓存",
  starting: "准备模型与任务",
  generating: "批量合成",
  checking: "语音识别核对",
  checkpoint: "保存进度",
  rendering: "处理节奏与响度",
  exporting: "导出有声书",
  cancelling: "等待当前处理结束后取消",
  cancelled: "已取消，音频和进度已保留",
};

const serviceMessages = {
  "An existing command-line conversion is running; this queue will wait": "之前启动的命令行任务正在运行。新任务已排队，将等待它结束后使用 GPU。",
  "Conversion failed; inspect the retained log": "本次生成没有完成。进度与日志已保留，请查看下方运行记录。",
  "Native picker is unavailable; use browser upload instead": "当前系统无法打开原生文件选择器，请使用“选择文件”上传书稿。",
  "Text cleanup changes are recorded in the audit preview": "本次正文清理或发音修正已记录，请检查下方朗读稿。",
  "Some selected chapter numbers are outside this book": "部分指定章节编号超出本书目录范围，仅会生成存在的章节。",
  "Select between 1 and 100 books": "请一次选择 1 至 100 本小说。",
  "The selected TTS model has not been downloaded": "当前合成模型尚未下载完整，请先安装本地模型。",
  "Local ASR environment or model is missing": "本地语音识别环境或模型缺失，请先完成安装，或关闭语音识别核对。",
  "Completed jobs cannot be cancelled; archive the record instead": "已完成的任务无需取消，可以隐藏记录。音频仍会保留。",
  "Only queued jobs can be moved": "只有排队中的任务可以调整顺序，当前任务可能已开始。",
  "Only stopped jobs can be archived": "请先暂停或取消任务，等它停止后再隐藏记录。",
  "Only paused, failed or cancelled jobs can be resumed": "只有已暂停、失败或已取消的任务可以重新排队。",
};

function readableMessage(message) {
  return serviceMessages[message] || message;
}

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined && content !== null) element.textContent = String(content);
  return element;
}

function showNotice(message, type = "info") {
  elements.notice.className = `notice ${type === "info" ? "" : type}`;
  elements.noticeText.textContent = message;
}

function setConnected(connected) {
  state.connected = connected;
  elements.connectionDot.className = `status-dot ${connected ? "connected" : "disconnected"}`;
  elements.connectionLabel.textContent = connected ? "本地服务已连接" : "本地连接中断，正在重试";
}

function errorMessage(body, fallback) {
  if (typeof body.detail === "string") return readableMessage(body.detail);
  if (Array.isArray(body.detail)) {
    return body.detail.map(item => `${(item.loc || []).join(".")}: ${item.msg || item.message || "Invalid value"}`).join("；");
  }
  if (body.error) return typeof body.error === "string" ? body.error : JSON.stringify(body.error);
  if (body.message) return body.message;
  return fallback;
}

async function api(path, options = {}) {
  const request = {cache: "no-store", ...options};
  if (request.body && !(request.body instanceof FormData)) {
    request.headers = {"Content-Type": "application/json", ...request.headers};
    request.body = JSON.stringify(request.body);
  }
  let response;
  try {
    response = await fetch(path, request);
  } catch (error) {
    throw new Error(`无法连接本地服务。请确认界面服务仍在运行。${error.message ? ` (${error.message})` : ""}`);
  }
  let body = {};
  const responseText = await response.text();
  try {
    body = responseText ? JSON.parse(responseText) : {};
  } catch (_) {
    if (!response.ok) throw new Error(`本地服务返回 ${response.status}：${responseText.slice(0, 600)}`);
    throw new Error("本地服务返回了无法读取的数据。");
  }
  if (!response.ok) throw new Error(errorMessage(body, `请求失败 (${response.status})`));
  return body;
}

async function mutate(message, action, button) {
  if (state.mutation) return;
  state.mutation = true;
  elements.busyMessage.textContent = message;
  elements.busyOverlay.classList.remove("hidden");
  if (button) button.disabled = true;
  try {
    await action();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    state.mutation = false;
    elements.busyOverlay.classList.add("hidden");
    if (button) button.disabled = false;
    updateQueueControls();
    updateSelection();
  }
}

function getValue(object, path, fallback) {
  let current = object;
  for (const key of path.split(".")) current = current && current[key];
  return current === undefined || current === null ? fallback : current;
}

function numberValue(id) {
  const input = elements[id];
  if (!input.checkValidity() || input.value.trim() === "") throw new Error(`请检查“${input.closest("label").querySelector("span").textContent.trim()}”的数值范围。`);
  const value = Number(input.value);
  if (!Number.isFinite(value)) throw new Error("请填写有效数值。");
  return value;
}

function pronunciationMap() {
  const input = elements.pronunciation.value.trim();
  if (!input) return {};
  if (input.startsWith("{")) {
    let parsed;
    try { parsed = JSON.parse(input); } catch (_) { throw new Error("发音修正 JSON 格式有误。也可以每行填写：原文 => 读法。"); }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("发音修正需要 JSON 对象。");
    for (const [original, spoken] of Object.entries(parsed)) {
      if (!original.trim() || typeof spoken !== "string" || !spoken.trim()) throw new Error("发音修正的原文和读法必须是非空文字。");
    }
    return parsed;
  }
  const result = Object.create(null);
  for (const line of input.split(/\r?\n/).filter(line => line.trim())) {
    const separator = line.indexOf("=>");
    if (separator < 1) throw new Error(`发音修正格式有误：“${line}”。请填写：原文 => 读法。`);
    const original = line.slice(0, separator).trim();
    const spoken = line.slice(separator + 2).trim();
    if (!original || !spoken) throw new Error("发音修正的原文和读法都需要填写。");
    if (Object.hasOwn(result, original)) throw new Error(`发音修正包含重复原文：“${original}”。`);
    result[original] = spoken;
  }
  return result;
}

function collectSettings(requireReference = true) {
  const settings = {
    voice: {
      mode: state.mode,
      speaker: elements.speaker.value,
      language: elements.language.value,
      instruct: state.mode === "clone" ? "" : elements.instruct.value.trim(),
    },
    tts: {
      model_path: elements.modelPath.value,
      batch_size: numberValue("batchSize"),
      temperature: numberValue("temperature"),
      seed: numberValue("seed"),
    },
    segment: {
      target_chars: numberValue("targetChars"),
      max_chars: numberValue("maxChars"),
      min_chars: numberValue("minChars"),
    },
    text: {
      remove_urls: elements.removeUrls.checked,
      strip_front_matter: elements.stripFrontMatter.checked,
      join_wrapped_lines: elements.joinLines.checked,
      remove_line_patterns: elements.removePatterns.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean),
      pronunciation_map: pronunciationMap(),
    },
    quality: {
      asr_check: elements.asrCheck.checked,
      max_cer: numberValue("maxCer") / 100,
      max_retry: numberValue("maxRetry"),
    },
    output: {
      speech_rate: numberValue("speechRate"),
      pitch_semitones: numberValue("pitch"),
      paragraph_pause: numberValue("paragraphPause"),
      chapter_pause: numberValue("chapterPause"),
      sentence_pause: numberValue("sentencePause"),
      loudness_lufs: numberValue("loudness"),
      chapter_formats: [elements.chapterFormat.value],
      m4b: elements.m4b.checked,
      mp3: elements.mp3.checked,
    },
  };
  if (!settings.tts.model_path) throw new Error("没有可用的本地合成模型。请先安装模型，再启动界面。");
  if (settings.segment.min_chars > settings.segment.target_chars || settings.segment.target_chars > settings.segment.max_chars) {
    throw new Error("段长需要满足：最小段长 ≤ 目标段长 ≤ 最大段长。");
  }
  const chapters = elements.chapters.value.trim();
  if (chapters) {
    if (!/^\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*$/.test(chapters)) throw new Error("章节选择请使用编号，例如：1,3-5,8。");
    settings.chapters = chapters.replace(/\s/g, "");
  }
  if (state.mode === "clone") {
    if (requireReference && !state.reference) throw new Error("请先选择参考音频。");
    if (requireReference && !elements.referenceText.value.trim()) throw new Error("请填写参考音频的完整原话。");
    settings.voice.reference_audio = state.reference ? state.reference.id || state.reference.path : null;
    settings.voice.reference_text = elements.referenceText.value.trim();
  }
  return settings;
}

function persistSettings() {
  try {
    const settings = collectSettings(false);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({settings, selected: [...state.selected], reference: state.reference}));
  } catch (_) {
    // Invalid intermediate values remain editable without replacing the last valid profile.
  }
}

function loadSavedSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved && saved.settings && typeof saved.settings === "object") {
      state.saved = saved;
      state.selected = new Set(Array.isArray(saved.selected) ? saved.selected.map(String) : []);
      state.reference = saved.reference && typeof saved.reference === "object" ? saved.reference : null;
    }
  } catch (_) {
    state.saved = null;
  }
}

function applySettings(settings) {
  const assignments = {
    speaker: ["voice.speaker", "Serena"],
    language: ["voice.language", "Chinese"],
    instruct: ["voice.instruct", ""],
    referenceText: ["voice.reference_text", ""],
    batchSize: ["tts.batch_size", 4],
    temperature: ["tts.temperature", .8],
    seed: ["tts.seed", 20261001],
    targetChars: ["segment.target_chars", 200],
    maxChars: ["segment.max_chars", 300],
    minChars: ["segment.min_chars", 60],
    maxCer: ["quality.max_cer", .15],
    maxRetry: ["quality.max_retry", 3],
    speechRate: ["output.speech_rate", 1],
    pitch: ["output.pitch_semitones", 0],
    paragraphPause: ["output.paragraph_pause", .45],
    chapterPause: ["output.chapter_pause", 1],
    sentencePause: ["output.sentence_pause", .18],
    loudness: ["output.loudness_lufs", -18],
  };
  for (const [id, [path, fallback]] of Object.entries(assignments)) {
    const value = getValue(settings, path, fallback);
    elements[id].value = id === "maxCer" ? Math.round(value * 100) : value;
  }
  for (const [id, path, fallback] of [
    ["removeUrls", "text.remove_urls", false],
    ["stripFrontMatter", "text.strip_front_matter", false],
    ["joinLines", "text.join_wrapped_lines", false],
    ["asrCheck", "quality.asr_check", true],
    ["m4b", "output.m4b", true],
    ["mp3", "output.mp3", false],
  ]) elements[id].checked = Boolean(getValue(settings, path, fallback));
  elements.chapterFormat.value = getValue(settings, "output.chapter_formats", ["flac"])[0] || "flac";
  const replacements = getValue(settings, "text.pronunciation_map", {});
  elements.pronunciation.value = Object.entries(replacements).map(([original, spoken]) => `${original} => ${spoken}`).join("\n");
  elements.removePatterns.value = getValue(settings, "text.remove_line_patterns", []).join("\n");
  elements.chapters.value = typeof settings.chapters === "string" ? settings.chapters : "";
  setMode(getValue(settings, "voice.mode", "preset"), getValue(settings, "tts.model_path", ""));
  elements.referenceName.textContent = state.reference ? state.reference.name : "选择参考音频";
  updateRangeLabels();
  updateVoiceDescription();
}

function setMode(mode, preferredPath = "") {
  const availableModels = (state.info?.models || []).filter(model => model.available !== false && model.mode === mode);
  if (mode === "clone" && !availableModels.length) mode = "preset";
  state.mode = mode;
  elements.presetMode.classList.toggle("active", mode === "preset");
  elements.cloneMode.classList.toggle("active", mode === "clone");
  elements.presetFields.classList.toggle("hidden", mode !== "preset");
  elements.cloneFields.classList.toggle("hidden", mode !== "clone");
  const models = (state.info?.models || []).filter(model => model.available !== false && model.mode === mode);
  elements.modelPath.replaceChildren();
  for (const model of models) {
    const option = node("option", "", model.label || model.id);
    option.value = model.path || model.id;
    elements.modelPath.append(option);
  }
  if (!models.length && mode === "preset" && state.info?.defaults?.tts?.model_path) {
    const path = String(state.info.defaults.tts.model_path);
    const option = node("option", "", path.split(/[\\/]/).pop());
    option.value = path;
    elements.modelPath.append(option);
  }
  if ([...elements.modelPath.options].some(option => option.value === preferredPath)) elements.modelPath.value = preferredPath;
}

function updateRangeLabels() {
  elements.speechRateValue.textContent = `${Number(elements.speechRate.value).toFixed(2)}×`;
  const pitch = Number(elements.pitch.value);
  elements.pitchValue.textContent = `${pitch > 0 ? "+" : ""}${pitch} 半音`;
  elements.temperatureValue.textContent = Number(elements.temperature.value).toFixed(2);
}

function updateVoiceDescription() {
  elements.voiceDescription.textContent = voiceDescriptions[elements.speaker.value] || "声线语言与本次朗读语言可以分别选择。";
}

function mergeConfig(base, overrides) {
  const result = {...base};
  for (const [key, value] of Object.entries(overrides || {})) {
    result[key] = value && typeof value === "object" && !Array.isArray(value) ? {...(base[key] || {}), ...value} : value;
  }
  return result;
}

function renderInfo(info) {
  state.info = info;
  elements.speaker.replaceChildren();
  const voices = info.voices?.length ? info.voices : Object.keys(voiceDescriptions).map(id => ({id, label: id}));
  for (const voice of voices) {
    const option = node("option", "", voice.label || voice.id);
    option.value = voice.id;
    elements.speaker.append(option);
  }
  const cloneAvailable = (info.models || []).some(model => model.available !== false && model.mode === "clone");
  elements.cloneMode.disabled = !cloneAvailable;
  elements.cloneMode.title = cloneAvailable ? "使用本地 Base 模型与参考音频" : "需要先安装 Qwen3-TTS Base 模型";
  if (info.features?.native_picker === false) {
    elements.pickButton.disabled = true;
    elements.pickButton.title = "当前系统不支持原生文件选择，请使用“选择文件”上传。";
  }
  if (info.features?.pitch_adjustment === false) {
    elements.pitch.disabled = true;
    elements.pitch.closest("label").querySelector("small").textContent = "当前 FFmpeg 缺少所需滤镜，音高调整暂不可用。";
  }
  const hardware = info.hardware || {};
  const gpu = info.gpu || hardware.gpu || {};
  const deviceName = info.device_name || hardware.device_name || (typeof gpu === "string" ? gpu : gpu.name) || hardware.device || info.defaults?.tts?.device;
  elements.hardwareName.textContent = deviceName || "本地推理环境";
  const deviceMemory = gpu.total_memory_gib || hardware.gpu_memory_gib || hardware.vram_gb;
  const modelName = String(info.defaults?.tts?.model_path || "").split(/[\\/]/).pop();
  elements.hardwareDetail.textContent = [deviceMemory ? `${Number(deviceMemory).toFixed(1)} GB 显存` : null, modelName, info.defaults?.tts?.attention?.toUpperCase()].filter(Boolean).join(" · ") || "使用本地模型与项目配置";
  if (info.version) elements.versionLabel.textContent = `LOCAL AUDIOBOOK STUDIO · ${info.version}`;
  const settings = mergeConfig(info.defaults || {}, state.saved?.settings || {});
  applySettings(settings);
  if (info.features?.pitch_adjustment === false) {
    elements.pitch.value = "0";
    updateRangeLabels();
  }
}

function fileSize(bytes) {
  const amount = Number(bytes);
  if (!Number.isFinite(amount)) return "大小未知";
  if (amount < 1024) return `${amount} B`;
  if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KB`;
  return `${(amount / 1024 ** 2).toFixed(1)} MB`;
}

function renderFiles() {
  elements.fileList.replaceChildren();
  for (const file of state.files) {
    const id = String(file.id);
    const row = node("div", `file-item ${state.selected.has(id) ? "selected" : ""}`);
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(id);
    checkbox.setAttribute("aria-label", `选择 ${file.name}`);
    checkbox.addEventListener("change", () => {
      checkbox.checked ? state.selected.add(id) : state.selected.delete(id);
      invalidatePlan();
      renderFiles();
      persistSettings();
    });
    const extension = String(file.name).split(".").pop().toUpperCase();
    const icon = node("span", "file-icon", extension);
    const copy = node("div", "file-copy");
    copy.append(node("strong", "", file.name), node("small", "", `${fileSize(file.size)} · ${file.path || "已保存到本地"}`));
    const remove = node("button", "file-remove", "×");
    remove.type = "button";
    remove.title = "从当前列表隐藏，不删除原书稿或已生成音频";
    remove.setAttribute("aria-label", `隐藏 ${file.name}`);
    remove.addEventListener("click", () => {
      state.files = state.files.filter(item => String(item.id) !== id);
      state.selected.delete(id);
      invalidatePlan();
      renderFiles();
      persistSettings();
    });
    row.append(checkbox, icon, copy, remove);
    elements.fileList.append(row);
  }
  updateSelection();
}

function updateSelection() {
  const count = state.selected.size;
  elements.fileCount.textContent = `${count} 本已选`;
  elements.selectionSummary.textContent = count ? `已选择 ${count} 本小说${state.plan ? "，检查结果已准备。" : "，请先检查正文。"}` : "选择书稿后，先检查正文。";
  elements.planButton.disabled = !count || state.mutation || !state.connected;
  elements.startButton.disabled = !count || !state.plan || state.mutation || !state.connected;
  elements.previewButton.disabled = state.mutation || !state.connected;
}

function invalidatePlan() {
  state.plan = null;
  state.planSignature = null;
  elements.planCard.classList.add("hidden");
  updateSelection();
}

function planSignature(settings) {
  return JSON.stringify({files: [...state.selected].sort(), segment: settings.segment, text: settings.text, chapters: settings.chapters || ""});
}

function addFiles(files) {
  for (const file of files || []) {
    const index = state.files.findIndex(existing => String(existing.id) === String(file.id));
    if (index >= 0) state.files[index] = file;
    else state.files.push(file);
    state.selected.add(String(file.id));
  }
  invalidatePlan();
  renderFiles();
  persistSettings();
}

async function uploadFiles(files) {
  const selectedFiles = [...files];
  if (!selectedFiles.length) return;
  const unsupported = selectedFiles.filter(file => !/\.(txt|epub)$/i.test(file.name));
  if (unsupported.length) {
    showNotice(`仅支持 TXT 与 EPUB。请移除：${unsupported.map(file => file.name).join("、")}`, "error");
    return;
  }
  await mutate("正在把书稿保存到本地工作区", async () => {
    const body = new FormData();
    selectedFiles.forEach(file => body.append("files", file));
    const result = await api("/api/files/upload", {method: "POST", body});
    addFiles(result.files);
    showNotice(`已添加 ${(result.files || []).length} 本小说。可以先检查正文与章节。`, "success");
  }, elements.uploadButton);
  elements.fileInput.value = "";
}

const auditLabels = {
  removed_urls: "移除网址",
  urls_removed: "移除网址",
  removed_lines: "移除整行",
  removed_front_matter_chars: "移除前置说明 / 字",
  front_matter_chars_removed: "移除前置说明 / 字",
  pronunciation_replacements: "发音替换",
  replacement_count: "发音替换",
  source_chars: "原始字符",
  clean_chars: "清理后字符",
  original_chars: "原始字符",
  spoken_chars: "朗读字符",
  warnings: "提醒",
  remove_urls: "移除网址",
  remove_user_matching_lines: "按规则移除整行",
  strip_front_matter: "移除章节前说明",
  remove_controls: "清理控制字符",
  strip_html: "清理 HTML 标签",
  join_wrapped_lines: "合并排版换行",
  format_cleanup: "清理文本格式",
  pronunciation_map: "发音修正",
  pronunciation_replacement: "发音修正",
  skip_repeated_spine_item: "跳过 EPUB 重复页面",
  skip_navigation_or_non_linear: "跳过 EPUB 导航页面",
  skip_image_only_or_empty: "跳过图片或空白页面",
  remove_adjacent_duplicate_title: "移除重复标题",
  review_non_paragraph_document: "检查特殊排版页面",
  split_toc_anchors: "按 EPUB 目录拆分章节",
  missing_toc_anchor: "目录锚点未找到",
  exclude_chapter: "排除章节",
};

function renderAudit(book, section) {
  if (!book.audit) return;
  const audit = node("div", "audit-list");
  if (!Array.isArray(book.audit)) {
    for (const [key, value] of Object.entries(book.audit)) {
      if (typeof value === "number" && value > 0) audit.append(node("span", "", `${auditLabels[key] || key} ${value.toLocaleString()}`));
    }
    if (audit.childElementCount) section.append(audit);
    return;
  }
  const operations = new Map();
  book.audit.forEach(record => {
    const operation = record.operation || "change";
    operations.set(operation, (operations.get(operation) || 0) + 1);
  });
  for (const [operation, count] of operations) audit.append(node("span", "", `${auditLabels[operation] || operation} · ${count} 项`));
  if (audit.childElementCount) section.append(audit);
  if (!book.audit.length) return;
  const details = node("details", "audit-details");
  details.append(node("summary", "", "查看清理、发音修正明细与移除内容"));
  book.audit.forEach(record => {
    const entry = node("div", "audit-entry");
    const heading = [auditLabels[record.operation] || record.operation || "文本调整"];
    if (record.removed_count) heading.push(`${record.removed_count} 项`);
    if (record.removed_chars) heading.push(`${record.removed_chars} 字符`);
    if (record.title) heading.push(record.title);
    entry.append(node("strong", "", heading.join(" · ")));
    if (record.source && record.operation !== "pronunciation_map") entry.append(node("small", "", `来源：${record.source}`));
    let content = "";
    if (typeof record.removed === "string") content = record.removed;
    else if (Array.isArray(record.removed)) content = record.removed.map(item => typeof item === "string" ? item : `${item.text || ""}${item.pattern ? `\n规则：${item.pattern}` : ""}`).join("\n\n");
    const replacements = record.replacements || record.changes;
    if (Array.isArray(replacements)) content = replacements.map(item => `${item.source || item.original || ""} → ${item.spoken || item.replacement || ""}${item.count ? ` (${item.count} 次)` : ""}`).join("\n");
    if (record.spoken && typeof record.source === "string") content = `${record.source} → ${record.spoken}${record.count ? ` (${record.count} 次)` : ""}`;
    if (record.before_chars !== undefined && record.after_chars !== undefined) content = `调整前 ${record.before_chars} 字符，调整后 ${record.after_chars} 字符。`;
    if (content) entry.append(node("pre", "text-preview", content));
    if (record.preview_truncated) entry.append(node("small", "", "这里展示节选。完整原文保留在书稿中，任务审计保存数量、摘要与部分内容。"));
    details.append(entry);
  });
  if (book.audit_truncated) details.append(node("p", "field-help", `这里展示部分明细，共 ${book.audit_count || "更多"} 项。任务保留审计数量、摘要与内容节选；完整内容可对照原始书稿。`));
  section.append(details);
}

function renderPlan(plan) {
  elements.planContent.replaceChildren();
  const books = plan.books || [];
  let totalSegments = 0;
  for (const book of books) {
    const section = node("article", "plan-book");
    section.append(node("h3", "", book.title || book.name || "未命名小说"));
    const stats = node("div", "plan-stats");
    const chapterCount = Array.isArray(book.chapters) ? book.chapters.length : book.chapters ?? book.chapter_count;
    const segmentCount = Array.isArray(book.segments) ? book.segments.length : book.segments ?? book.segment_count;
    const chars = book.chars ?? book.char_count;
    if (chapterCount !== undefined) stats.append(node("span", "", `${chapterCount} 章`));
    if (segmentCount !== undefined) {
      stats.append(node("span", "", `${segmentCount} 个朗读段落`));
      totalSegments += Number(segmentCount) || 0;
    }
    if (chars !== undefined) stats.append(node("span", "", `${Number(chars).toLocaleString()} 字符`));
    section.append(stats);
    for (const warning of book.warnings || []) section.append(node("p", "plan-warning", readableMessage(typeof warning === "string" ? warning : warning.message || JSON.stringify(warning))));
    renderAudit(book, section);
    const preview = book.spoken_preview || (typeof book.preview === "string" ? book.preview : book.preview?.spoken || book.preview?.text || book.preview?.cleaned || "");
    if (preview) {
      const details = node("details");
      details.open = books.length === 1;
      details.append(node("summary", "", "查看实际朗读稿预览"), node("pre", "text-preview", preview));
      section.append(details);
    }
    if (typeof book.preview === "string" && book.spoken_preview && book.preview !== book.spoken_preview) {
      const details = node("details");
      details.append(node("summary", "", "对照保留的正文原文"), node("pre", "text-preview", book.preview));
      section.append(details);
    }
    const chapterTitles = book.chapter_titles || (Array.isArray(book.chapters) ? book.chapters : []);
    if (chapterTitles.length) {
      const details = node("details");
      details.append(node("summary", "", "查看章节目录与编号"));
      const list = node("ol", "chapter-list");
      chapterTitles.forEach((chapter, index) => {
        const item = node("li");
        const title = typeof chapter === "string" ? chapter : chapter.title || chapter.name;
        const number = typeof chapter === "object" ? chapter.index ?? chapter.number ?? index + 1 : index + 1;
        item.append(node("span", "", String(number).padStart(2, "0")), node("div", "", title || `Chapter ${number}`));
        list.append(item);
      });
      details.append(list);
      section.append(details);
    }
    elements.planContent.append(section);
  }
  elements.planTitle.textContent = books.length > 1 ? `${books.length} 本书稿已准备` : books[0]?.title || "书稿已准备";
  elements.planSummary.textContent = `${totalSegments.toLocaleString()} 个朗读段落`;
  elements.planCard.classList.remove("hidden");
  updateSelection();
}

async function analyzeFiles() {
  await mutate("正在解析章节与检查朗读稿", async () => {
    const settings = collectSettings(false);
    const result = await api("/api/plan", {method: "POST", body: {files: [...state.selected], settings}});
    if (!result.books?.length) throw new Error("没有找到可以朗读的正文。请检查文件内容与清理设置。");
    state.plan = result;
    state.planSignature = planSignature(settings);
    renderPlan(result);
    elements.planCard.scrollIntoView({behavior: "smooth", block: "nearest"});
  }, elements.planButton);
}

async function startJob(kind) {
  const button = kind === "preview" ? elements.previewButton : elements.startButton;
  await mutate(kind === "preview" ? "正在安排真实声音试听" : "正在保存设置并加入生成队列", async () => {
    const settings = collectSettings();
    if (kind === "convert" && (!state.plan || state.planSignature !== planSignature(settings))) {
      invalidatePlan();
      throw new Error("书稿或清理设置已变更，请重新检查文本与章节。");
    }
    const request = {kind, settings, files: kind === "preview" ? [] : [...state.selected]};
    if (kind === "preview") {
      request.preview_text = elements.previewText.value.trim();
      if (!request.preview_text) throw new Error("请先填写一段试听文字。");
      if (request.preview_text.length > 600) throw new Error("试听文字请控制在 600 字符以内。");
      delete settings.chapters;
    }
    const result = await api("/api/jobs", {method: "POST", body: request});
    const job = result.job || result;
    if (!job.id) throw new Error("任务已提交，但服务没有返回任务编号。请刷新生成进度查看。");
    state.activeId = String(job.id);
    state.detail = null;
    persistSettings();
    await refresh(true);
    elements.activityTitle.scrollIntoView({behavior: "smooth", block: "nearest"});
    showNotice(kind === "preview" ? "试听已进入队列。完成后可在生成进度中播放。" : "小说已加入队列。你可以离开此页面，服务会继续生成。", "success");
  }, button);
}

function finite(value) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function metrics(job) {
  const progress = typeof job.progress === "object" && job.progress ? job.progress : job;
  const completed = finite(progress.completed ?? job.completed);
  const total = finite(progress.total ?? job.total);
  let percent = finite(progress.percent ?? job.percent);
  const currentReadonlyRtf = job.readonly && job.status === "running" ? finite(job.run_rtf) : null;
  if (percent === null && completed !== null && total > 0) percent = 100 * completed / total;
  return {
    completed, total,
    percent: percent === null ? null : Math.max(0, Math.min(100, percent)),
    rtf: currentReadonlyRtf ?? finite(progress.rtf ?? job.rtf),
    audioSeconds: finite(progress.audio_seconds ?? job.audio_seconds),
    generationSeconds: finite(progress.generation_seconds ?? job.generation_seconds),
    eta: finite(progress.eta_seconds ?? job.eta_seconds),
    retries: finite(job.retry_count ?? job.retries ?? job.qc?.retries),
    failed: finite(job.failed_count ?? job.failures ?? job.qc?.failed),
    rtfSource: currentReadonlyRtf !== null ? "current_run" : job.rtf_source || (job.readonly ? "historical" : null),
  };
}

function scopeLabel(record) {
  if (record.export_scope !== "selected_chapters" && record.complete_book !== false) return null;
  const chapters = Array.isArray(record.selected_chapters) ? record.selected_chapters : [];
  const listed = chapters.length > 12 ? `${chapters.slice(0, 12).join(",")}…（${chapters.length} 章）` : chapters.join(",");
  return listed ? `仅所选章节 ${listed}` : "仅所选章节";
}

function duration(seconds, compact = false) {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const amount = Math.floor(seconds);
  const hours = Math.floor(amount / 3600);
  const minutes = Math.floor(amount % 3600 / 60);
  const remainder = amount % 60;
  if (compact) return hours ? `${hours}时 ${minutes}分` : minutes ? `${minutes}分 ${remainder}秒` : `${remainder}秒`;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}` : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function jobTitle(job) {
  return job.title || job.book_title || job.name || (job.kind === "preview" ? "声音试听" : "小说生成任务");
}

function externalId(job, index = 0) {
  return `external:${job.id || job.book_id || job.path || index}`;
}

function allJobs() {
  return [...state.jobs.filter(job => !job.archived || state.showArchived).map(job => ({...job, displayId: String(job.id), readonly: false})), ...state.external.map((job, index) => ({...job, displayId: externalId(job, index), readonly: true}))];
}

function updateQueueControls() {
  const waitingCount = state.jobs.filter(job => job.status === "queued").length;
  elements.cancelQueued.disabled = !waitingCount || state.mutation;
  elements.cancelQueued.textContent = waitingCount ? `取消全部排队任务（${waitingCount}）` : "取消全部排队任务";
}

function renderJobs() {
  const jobs = allJobs();
  if (!jobs.some(job => job.displayId === state.activeId)) {
    const active = jobs.find(job => job.status === "running") || jobs.find(job => job.status === "queued") || jobs[0];
    state.activeId = active?.displayId || null;
    state.detail = null;
  }
  elements.jobList.replaceChildren();
  const queuedCount = state.jobs.filter(job => ["running", "queued", "preparing", "pausing", "cancelling", "stopping"].includes(job.status)).length;
  updateQueueControls();
  elements.queueCount.textContent = queuedCount ? `${queuedCount} 个任务进行中` : jobs.length ? `${jobs.length} 个任务` : "队列为空";
  elements.emptyJobs.classList.toggle("hidden", jobs.length > 0);
  elements.externalNotice.classList.toggle("hidden", !state.queueBlocked && !state.external.some(job => ["running", "preparing"].includes(job.status)));
  for (const job of jobs) {
    const button = node("button", `job-tab ${state.activeId === job.displayId ? "active" : ""}`);
    button.type = "button";
    const progress = metrics(job);
    const percentage = progress.percent === null ? "" : ` · ${progress.percent.toFixed(0)}%`;
    const queueLabel = job.queue_position ? ` · 队列第 ${job.queue_position} 位` : "";
    button.append(node("strong", "", jobTitle(job)), node("small", "", `${job.readonly ? "原有任务 · " : ""}${statuses[job.status] || job.status || "状态未知"}${percentage}${queueLabel}${job.archived ? " · 已隐藏" : ""}`));
    button.setAttribute("aria-pressed", String(state.activeId === job.displayId));
    button.addEventListener("click", async () => {
      state.activeId = job.displayId;
      state.detail = null;
      renderJobs();
      await refreshActiveDetail();
    });
    elements.jobList.append(button);
  }
  renderJobDetail();
}

function safeExportUrl(url) {
  if (typeof url !== "string") return null;
  try {
    const parsed = new URL(url, location.origin);
    return parsed.origin === location.origin && parsed.pathname.startsWith("/api/") ? parsed.href : null;
  } catch (_) {
    return null;
  }
}

function renderExports(exports) {
  elements.exportList.replaceChildren();
  const available = (Array.isArray(exports) ? exports : []).filter(item => safeExportUrl(item.url));
  for (const item of available) {
    const link = node("a", "export-link");
    link.href = safeExportUrl(item.url);
    link.download = item.name || "";
    const format = String(item.format || item.name?.split(".").pop() || "AUDIO").toUpperCase();
    const scope = scopeLabel(item);
    link.append(node("small", "", format), node("span", "", `下载 ${item.name || item.book_title || "音频"}${scope ? ` · ${scope}` : ""}`));
    elements.exportList.append(link);
  }
  const playable = available.find(item => /^(mp3|wav|flac|m4a|ogg)$/i.test(item.format || item.name?.split(".").pop() || "")) || available.find(item => /^m4b$/i.test(item.format || item.name?.split(".").pop() || ""));
  elements.audioPreview.classList.toggle("hidden", !playable);
  const url = playable ? safeExportUrl(playable.url) : null;
  if (url !== state.audioUrl) {
    elements.audioPlayer.pause();
    if (url) elements.audioPlayer.src = url;
    else elements.audioPlayer.removeAttribute("src");
    elements.audioPlayer.load();
    state.audioUrl = url;
  }
  if (playable) elements.audioPreviewName.textContent = `${playable.name || "生成音频"}${scopeLabel(playable) ? ` · ${scopeLabel(playable)}` : ""}`;
}

function logText(job) {
  const logs = job.logs ?? job.log;
  if (typeof logs === "string") return logs;
  if (Array.isArray(logs)) return logs.map(line => typeof line === "string" ? line : line.message || line.text || JSON.stringify(line)).join("\n");
  return "暂无运行记录。";
}

function actionButton(label, action, job, className = "secondary") {
  const button = node("button", `button ${className}`, label);
  button.type = "button";
  const messages = {
    pause: ["正在请求安全暂停并保存进度", "已提交暂停请求，当前处理会安全保存后停止。"],
    cancel: ["正在取消任务", "任务已取消或正在安全停止。已生成音频和进度会保留，不会自动重启。"],
    resume: ["正在安排任务续跑", "任务已加入队列，将复用兼容的已完成音频。"],
    retry: ["正在安排失败部分重试", "未完成部分已加入队列，通过核对的音频会复用。"],
    "move-first": ["正在调整队列", "该任务将在当前任务结束后优先运行。"],
    archive: ["正在隐藏任务记录", "记录已隐藏，音频和缓存仍保留。勾选“显示隐藏记录”可以找回。"],
    unarchive: ["正在恢复任务记录", "记录已恢复显示，任务不会自动启动。"],
  };
  const [busy, success] = messages[action];
  button.addEventListener("click", () => mutate(busy, async () => {
    await api(`/api/jobs/${encodeURIComponent(job.id)}/${action}`, {method: "POST"});
    await refresh(true);
    showNotice(success, "success");
  }, button));
  return button;
}

function renderJobDetail() {
  const selected = allJobs().find(job => job.displayId === state.activeId);
  elements.jobDetail.classList.toggle("hidden", !selected);
  if (!selected) return;
  const job = !selected.readonly && state.detail && String(state.detail.id) === String(selected.id) ? {...selected, ...state.detail} : selected;
  const progress = metrics(job);
  elements.activeJobTitle.textContent = jobTitle(job);
  const subtitle = [job.readonly ? "原有命令行任务 · 仅查看" : job.kind === "preview" ? "真实模型试听 · 使用当前设置快照" : "本地批量生成 · 自动保存进度"];
  if (job.current_chapter || job.chapter_title) subtitle.push(job.current_chapter || job.chapter_title);
  if (job.phase && phases[job.phase]) subtitle.push(phases[job.phase]);
  if (job.active_batch_size) subtitle.push(`当前批次 ${job.active_batch_size} 段`);
  if (job.queue_position) subtitle.push(`队列第 ${job.queue_position} 位`);
  const reusableSegments = finite(job.cache_reused_segments) || 0;
  if (reusableSegments > 0) subtitle.push(`兼容缓存 ${reusableSegments} 段，核对后复用`);
  const scopes = [...new Set([scopeLabel(job), ...(Array.isArray(job.books) ? job.books.map(scopeLabel) : [])].filter(Boolean))];
  if (scopes.length) subtitle.push(scopes.join("；"));
  else if (typeof job.chapters === "string" && job.chapters) subtitle.push(`指定章节 ${job.chapters}`);
  elements.activeJobSubtitle.textContent = subtitle.join(" · ");
  elements.activeJobStatus.textContent = statuses[job.status] || job.status || "状态未知";
  elements.activeJobStatus.className = `status-badge ${job.status || ""}`;
  elements.progressFill.style.width = `${progress.percent ?? 0}%`;
  if (progress.percent !== null) elements.jobProgress.setAttribute("aria-valuenow", String(progress.percent));
  else elements.jobProgress.removeAttribute("aria-valuenow");
  elements.percentProgress.textContent = progress.percent === null ? "—" : `${progress.percent.toFixed(1)}%`;
  elements.segmentProgress.textContent = progress.completed === null ? "等待进度信息" : `${progress.completed.toLocaleString()} / ${progress.total === null ? "待确认" : progress.total.toLocaleString()} 段已完成核对`;
  elements.audioDuration.textContent = duration(progress.audioSeconds);
  elements.speedValue.textContent = progress.rtf === null ? "—" : `${progress.rtf.toFixed(2)} RTF`;
  const metricScope = progress.rtfSource === "current_run" ? "本次合成" : progress.rtfSource === "historical" ? "历史合成" : "合成效率";
  elements.speedDetail.textContent = progress.rtf > 0 ? `${metricScope} · ${(1 / progress.rtf).toFixed(2)}× 实时` : progress.rtfSource === "unavailable" ? "等待本次合成数据" : "RTF，越低越快";
  elements.etaValue.textContent = duration(progress.eta, true);
  elements.etaDetail.textContent = progress.eta === null ? "有足够数据时显示" : "按当前进度估算";
  elements.retryValue.textContent = `${progress.retries ?? "—"} / ${progress.failed ?? "—"}`;
  const error = job.error || job.blocked_reason || job.message;
  elements.jobError.classList.toggle("hidden", !error);
  elements.jobError.classList.toggle("error", Boolean(job.error) || job.status === "failed");
  elements.jobError.textContent = typeof error === "object" ? JSON.stringify(error) : readableMessage(error || "");
  elements.jobActions.replaceChildren();
  if (job.readonly) {
    elements.jobActions.append(node("p", "", "继续使用原来的窗口管理这次执行。界面不会暂停或修改这个任务。"));
  } else if (["running", "queued", "preparing"].includes(job.status)) {
    elements.jobActions.append(actionButton(job.status === "queued" ? "暂停排队" : "安全暂停", "pause", job));
    elements.jobActions.append(node("p", "", "暂停请求会保留已完成音频。关闭页面或界面服务，已启动的生成仍会继续。"));
  } else if (["pending", "paused"].includes(job.status)) {
    elements.jobActions.append(actionButton("继续生成", "resume", job));
  } else if (job.status === "failed") {
    elements.jobActions.append(actionButton("重试未完成部分", "retry", job));
  } else if (job.status === "cancelled") {
    elements.jobActions.append(actionButton("重新排队", "resume", job));
  }
  if (!job.readonly) {
    if (["running", "queued", "preparing", "pausing", "paused", "failed"].includes(job.status)) {
      elements.jobActions.append(actionButton("取消任务", "cancel", job, "danger"));
    }
    if (job.status === "queued" && job.queue_position > 1) {
      elements.jobActions.append(actionButton("下一个运行", "move-first", job));
    }
    if (["paused", "failed", "completed", "cancelled"].includes(job.status)) {
      elements.jobActions.append(actionButton(job.archived ? "恢复显示" : "隐藏记录", job.archived ? "unarchive" : "archive", job));
    }
    if (job.status === "cancelling") {
      elements.jobActions.append(node("p", "", "取消已提交，等待当前批次或编码安全结束；不会删除音频，也不会自动继续。"));
    } else if (job.status === "cancelled") {
      elements.jobActions.append(node("p", "", "已退出队列，原文、已生成音频和进度仍保留。需要时可重新排队。"));
    }
  }
  renderExports(job.exports);
  const shouldFollow = elements.jobLog.scrollTop + elements.jobLog.clientHeight >= elements.jobLog.scrollHeight - 35;
  const logs = logText(job);
  if (elements.jobLog.textContent !== logs) {
    elements.jobLog.textContent = logs;
    if (shouldFollow) elements.jobLog.scrollTop = elements.jobLog.scrollHeight;
  }
}

async function refreshActiveDetail() {
  if (!state.activeId || state.activeId.startsWith("external:")) {
    renderJobDetail();
    return;
  }
  const id = state.activeId;
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (state.activeId === id) {
      state.detail = result.job || result;
      renderJobDetail();
    }
  } catch (error) {
    if (state.activeId === id) showNotice(`无法读取任务详情：${error.message}`, "error");
  }
}

async function refresh(force = false) {
  if (state.refreshing || (!force && document.hidden)) return;
  state.refreshing = true;
  try {
    const responses = await Promise.allSettled([api("/api/jobs"), api("/api/monitor")]);
    if (responses[0].status === "fulfilled") {
      state.jobs = responses[0].value.jobs || [];
      const active = state.jobs.find(job => String(job.id) === state.activeId);
      if (active && (Array.isArray(active.logs) || Array.isArray(active.exports))) state.detail = active;
      setConnected(true);
    } else {
      setConnected(false);
    }
    if (responses[1].status === "fulfilled") {
      state.external = responses[1].value.tasks || responses[1].value.jobs || [];
      state.queueBlocked = Boolean(responses[1].value.queue_blocked);
    }
    if (state.connected && !state.info) {
      try {
        renderInfo(await api("/api/info"));
        const files = await api("/api/files");
        state.files = (files.files || []).filter(file => file.kind !== "reference");
        renderFiles();
      } catch (_) {
        // The next refresh retries initialization after a temporary service restart.
      }
    }
    renderJobs();
    const selected = state.jobs.find(job => String(job.id) === state.activeId);
    if (!selected || (!Array.isArray(selected.logs) && !Array.isArray(selected.exports))) await refreshActiveDetail();
    updateSelection();
  } finally {
    state.refreshing = false;
  }
}

function attachEvents() {
  elements.showArchived.addEventListener("change", () => {
    state.showArchived = elements.showArchived.checked;
    renderJobs();
    refreshActiveDetail();
  });
  elements.cancelQueued.addEventListener("click", () => mutate("正在取消排队任务", async () => {
    const result = await api("/api/queue/cancel", {method: "POST"});
    await refresh(true);
    showNotice(`已取消 ${result.count} 个排队任务，当前正在运行的任务不受影响。`, "success");
  }, elements.cancelQueued));
  elements.dismissNotice.addEventListener("click", () => elements.notice.classList.add("hidden"));
  elements.refreshButton.addEventListener("click", () => refresh(true));
  elements.uploadButton.addEventListener("click", () => elements.fileInput.click());
  elements.fileInput.addEventListener("change", () => uploadFiles(elements.fileInput.files));
  elements.pickButton.addEventListener("click", () => mutate("请在系统窗口中选择小说文件", async () => {
    const result = await api("/api/files/pick", {method: "POST"});
    if (result.files?.length) addFiles(result.files);
  }, elements.pickButton));
  elements.referenceInput.addEventListener("change", () => mutate("正在保存参考音频", async () => {
    const file = elements.referenceInput.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    const result = await api("/api/files/reference", {method: "POST", body});
    state.reference = result.file || result.reference || result;
    elements.referenceName.textContent = state.reference.name || file.name;
    persistSettings();
    showNotice("参考音频已保存。请填写对应的完整原话。", "success");
  }));
  for (const event of ["dragenter", "dragover"]) elements.dropZone.addEventListener(event, event => {
    event.preventDefault();
    elements.dropZone.classList.add("dragging");
  });
  for (const event of ["dragleave", "drop"]) elements.dropZone.addEventListener(event, event => {
    event.preventDefault();
    elements.dropZone.classList.remove("dragging");
  });
  elements.dropZone.addEventListener("drop", event => uploadFiles(event.dataTransfer.files));
  elements.planButton.addEventListener("click", analyzeFiles);
  elements.startButton.addEventListener("click", () => startJob("convert"));
  elements.previewButton.addEventListener("click", () => startJob("preview"));
  elements.presetMode.addEventListener("click", () => {
    setMode("preset");
    persistSettings();
  });
  elements.cloneMode.addEventListener("click", () => {
    setMode("clone");
    persistSettings();
  });
  document.querySelectorAll("[data-style]").forEach(button => button.addEventListener("click", () => {
    elements.instruct.value = styleInstructions[button.dataset.style];
    persistSettings();
  }));
  const planInputs = new Set(["removeUrls", "stripFrontMatter", "joinLines", "removePatterns", "pronunciation", "chapters", "targetChars", "maxChars", "minChars"]);
  document.querySelectorAll(".settings-panel input,.settings-panel select,.settings-panel textarea,.cleaning-options input,.cleaning-options textarea").forEach(input => {
    input.addEventListener("input", () => {
      updateRangeLabels();
      if (input.id === "speaker") updateVoiceDescription();
      if (planInputs.has(input.id)) invalidatePlan();
      persistSettings();
    });
    input.addEventListener("change", persistSettings);
  });
  elements.resetSettings.addEventListener("click", () => {
    if (!state.info) return;
    state.saved = null;
    state.reference = null;
    applySettings(state.info.defaults || {});
    invalidatePlan();
    persistSettings();
    showNotice("已恢复项目默认设置。现有任务保留自己的设置快照。", "success");
  });
  elements.audioPlayer.addEventListener("error", () => {
    if (state.audioUrl) showNotice("浏览器暂时无法播放这个格式。可以下载音频，用本机播放器打开。", "info");
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
}

async function initialize() {
  loadSavedSettings();
  attachEvents();
  const results = await Promise.allSettled([api("/api/info"), api("/api/files")]);
  if (results[0].status === "fulfilled") {
    renderInfo(results[0].value);
    setConnected(true);
  } else {
    setConnected(false);
    showNotice(results[0].reason.message, "error");
  }
  if (results[1].status === "fulfilled") {
    state.files = (results[1].value.files || []).filter(file => file.kind !== "reference");
    state.selected = new Set([...state.selected].filter(id => state.files.some(file => String(file.id) === id)));
  } else if (results[0].status === "fulfilled") {
    showNotice(`无法读取书稿列表：${results[1].reason.message}`, "error");
  }
  renderFiles();
  await refresh(true);
  setInterval(() => refresh(), 3000);
}

initialize().catch(error => {
  setConnected(false);
  showNotice(`界面初始化失败：${error.message}`, "error");
});
