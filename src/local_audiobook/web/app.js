"use strict";

const STORAGE_KEY = "local-audiobook-studio-v1";
const DEFAULT_VOICE_REVISION = "mature-v1";
const DOWNLOAD_STORAGE_KEY = "local-audiobook-downloads-v1";
const DOWNLOAD_FORMATS = ["flac", "m4b", "mp3", "wav"];
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
  referenceClips: [],
  referenceJobId: null,
  voiceProfiles: [],
  voiceProfileId: "",
  refreshing: false,
  mutation: false,
  connected: false,
  audioUrl: null,
  saved: null,
  showArchived: false,
  selectedJobs: new Set(),
  selectedVoices: new Set(),
  monitorOverrides: {},
  downloads: {format: "flac", auto: false, since: null, submitted: {}, selected: new Set(),
    busy: false, autoPaused: false, message: "", error: false},
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
  mature: "成熟、自信、有气场的成年女性小说旁白，声音略低沉、清晰有力，情绪克制，节奏利落，停顿适度，保持自然叙述。",
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
  reference: "参考录音整理与本地识别",
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
  "Select between 1 and 5 recordings of the same speaker": "请选择同一人的 1～5 段录音。",
  "Enter the exact transcript or install the local ASR environment": "请填写录音的准确原话，或安装本地语音识别环境。",
  "The selected recordings exceed 60 seconds; shorten the selected ranges": "选中的录音超过 60 秒，请缩短每段的起止范围。",
  "The combined reference exceeds 60 seconds including pauses; shorten the ranges": "加上段间停顿后超过 60 秒，请稍微缩短范围。",
  "Each source recording must be no longer than 10 minutes; select a shorter file": "每个原始录音最多 10 分钟，请选一段较短的录音。",
  "Select a valid start and end within the recording": "起点和终点需要位于录音内，终点大于起点。",
  "The selected recording is silent or too quiet; choose a clearer recording": "选中的录音没有足够清晰的人声，请换一段较清楚的录音。",
  "No usable speech level was found in the selected recording": "选中的录音音量太低，请选择能清楚听见的人声。",
  "Each selected clip must contain at least one second of usable audio": "每段至少需要一秒可用的人声，请延长范围。",
  "Local recognition returned no speech; enter the exact transcript manually": "未识别到文字。请试听录音，并手动填写准确原话。",
  "A reference recording exceeds the 64 MB limit": "每段参考录音最多 64 MB，请选择较短的文件。",
  "A reference video exceeds the 512 MB limit": "每个参考视频最多 512 MB，请选择较短的视频。",
  "The video has no audio track; choose a video containing speech": "这个视频没有音轨，请选择包含人声的视频。",
  "The video duration is invalid; choose another video": "无法读取视频时长，请换一个完整、可播放的视频文件。",
  "Each source video must be no longer than 10 minutes; select a shorter file": "每个参考视频最多 10 分钟，请先截取较短的视频。",
  "The video cannot be decoded into usable audio; choose another video": "无法从视频提取可用音频，请换一个完整、可播放的视频文件。",
  "Upload TXT/EPUB books or a supported reference audio/video file": "书稿支持 TXT/EPUB；参考素材支持 WAV、MP3、FLAC、M4A、OGG 和 MP4、M4V、MOV、MKV、WebM、AVI。",
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
  "Stop the external conversion before removing its display record": "原窗口的转换仍在运行。请等它停止后再删除显示记录。",
  "Only paused, failed or cancelled jobs can be resumed": "只有已暂停、失败或已取消的任务可以重新排队。",
  "Select between 1 and 100 completed jobs": "一次请选择 1～100 个已完成任务。",
  "Only completed audio jobs can be bundled": "只有已完成的音频任务可以打包，录音整理任务不包含在内。",
  "A selected job has no exports in the requested format": "有任务尚未导出所选格式。请刷新后重选，下载格式不会自动转码。",
  "Select fewer jobs; a bundle can contain at most 20000 audio files": "单个压缩包最多 20000 个音频文件，请分批选择任务。",
};

function readableMessage(message) {
  const text = String(message || "");
  const withoutType = text.replace(/^(?:ValueError|RuntimeError|TimeoutError|FileNotFoundError):\s*/, "");
  return serviceMessages[text] || serviceMessages[withoutType] || text;
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
    updateReferenceControls();
    updateListControls();
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
      strip_downloader_metadata: elements.stripDownloaderMetadata.checked,
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
  if (!state.info?.features?.downloader_cleanup) delete settings.text.strip_downloader_metadata;
  if (settings.segment.min_chars > settings.segment.target_chars || settings.segment.target_chars > settings.segment.max_chars) {
    throw new Error("段长需要满足：最小段长 ≤ 目标段长 ≤ 最大段长。");
  }
  const chapters = elements.chapters.value.trim();
  if (chapters) {
    if (!/^\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*$/.test(chapters)) throw new Error("章节选择请使用编号，例如：1,3-5,8。");
    settings.chapters = chapters.replace(/\s/g, "");
  }
  if (state.mode === "clone") {
    if (requireReference && state.referenceJobId) throw new Error("参考录音正在排队或整理，请等完成后校对文字，再生成试听。");
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
    localStorage.setItem(STORAGE_KEY, JSON.stringify({settings, selected: [...state.selected], reference: state.reference,
      defaultVoiceRevision: state.info?.default_voice_revision || DEFAULT_VOICE_REVISION,
      monitorOverrides: state.monitorOverrides, referenceClips: state.referenceClips,
      referenceJobId: state.referenceJobId, voiceProfileId: state.voiceProfileId}));
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
      state.referenceClips = Array.isArray(saved.referenceClips) ? saved.referenceClips.filter(clip => clip && typeof clip.id === "string").slice(0, 5) : [];
      state.referenceJobId = typeof saved.referenceJobId === "string" ? saved.referenceJobId : null;
      state.voiceProfileId = typeof saved.voiceProfileId === "string" ? saved.voiceProfileId : "";
      state.monitorOverrides = saved.monitorOverrides && typeof saved.monitorOverrides === "object" ? saved.monitorOverrides : {};
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
    ["stripDownloaderMetadata", "text.strip_downloader_metadata", true],
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

function applyMaturePreset() {
  setMode("preset");
  elements.speaker.value = "Vivian";
  elements.instruct.value = styleInstructions.mature;
  elements.speechRate.value = "1.15";
  elements.pitch.value = state.info?.features?.pitch_adjustment === false ? "0" : "-1";
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
  elements.cloneWorkshop.classList.toggle("hidden", mode !== "clone");
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
  updateHardwareDetail();
}

function updateHardwareDetail() {
  const info = state.info || {};
  const hardware = info.hardware || {};
  const gpu = info.gpu || hardware.gpu || {};
  const memory = gpu.total_memory_gib || hardware.gpu_memory_gib || hardware.vram_gb;
  const modelName = String(elements.modelPath.value || info.defaults?.tts?.model_path || "").split(/[\\/]/).pop();
  elements.hardwareDetail.textContent = [memory ? `${Number(memory).toFixed(1)} GB 显存` : null, modelName, info.defaults?.tts?.attention?.toUpperCase()].filter(Boolean).join(" · ") || "使用本地模型与项目配置";
}

function initializeTheme() {
  const preference = matchMedia("(prefers-color-scheme: dark)");
  let choice = "system";
  try { choice = localStorage.getItem("local-audiobook-theme") || "system"; } catch (_) {}
  elements.themeChoice.value = ["light", "dark", "system"].includes(choice) ? choice : "system";
  function applyTheme() {
    const dark = elements.themeChoice.value === "dark" || (elements.themeChoice.value === "system" && preference.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]').content = dark ? "#121916" : "#f5f3ee";
  }
  elements.themeChoice.addEventListener("change", () => {
    try { localStorage.setItem("local-audiobook-theme", elements.themeChoice.value); } catch (_) {}
    applyTheme();
  });
  preference.addEventListener("change", applyTheme);
  applyTheme();
}

function updateReferenceControls() {
  const modern = Boolean(state.info?.features?.reference_preparation);
  elements.prepareReference.disabled = !modern || !state.referenceClips.length || Boolean(state.referenceJobId);
  elements.saveVoice.disabled = !state.reference?.prepared || !elements.referenceText.value.trim() || !elements.voiceName.value.trim();
  elements.archiveVoice.disabled = !state.voiceProfileId;
  const profile = state.voiceProfiles.find(item => item.id === state.voiceProfileId);
  elements.referenceName.textContent = state.reference ? `${profile?.name || state.reference.name}${state.reference.duration ? ` · ${state.reference.duration.toFixed(1)} 秒` : ""}` : "尚未准备参考音色";
  const url = modern && state.reference ? `/api/references/${encodeURIComponent(state.reference.id)}/audio` : null;
  elements.referencePlayer.classList.toggle("hidden", !url);
  if (url && elements.referencePlayer.getAttribute("src") !== url) elements.referencePlayer.src = url;
  if (!url) {
    elements.referencePlayer.pause();
    elements.referencePlayer.removeAttribute("src");
  }
}

function referenceChanged() {
  state.reference = null;
  state.voiceProfileId = "";
  elements.voiceProfile.value = "";
  elements.referenceWarnings.textContent = "";
  elements.referenceStatus.textContent = "录音已修改，请点击整理。空白原话自动识别中文。";
  updateReferenceControls();
  persistSettings();
}

function renderReferenceClips() {
  elements.referenceClips.replaceChildren();
  for (const [index, clip] of state.referenceClips.entries()) {
    const card = node("div", "reference-clip");
    const heading = node("div", "reference-clip-heading");
    heading.append(node("strong", "", `${index + 1}. ${clip.name}`));
    const remove = node("button", "text-button", "移除");
    remove.type = "button";
    remove.disabled = Boolean(state.referenceJobId);
    remove.addEventListener("click", () => mutate("正在移除参考素材", async () => {
      if (state.info?.features?.list_management) await changeRecords("files", [clip.id]);
      state.referenceClips.splice(index, 1);
      referenceChanged();
      renderReferenceClips();
      updateListControls();
    }));
    heading.append(remove);
    card.append(heading);
    if (clip.source_kind === "video") {
      card.append(node("small", "hint", `已从视频提取第一条音轨 · ${Number(clip.duration).toFixed(1)} 秒。可按下方时间选择人声片段。`));
    }
    const player = node("audio");
    player.controls = true;
    player.preload = "metadata";
    player.src = `/api/references/${encodeURIComponent(clip.id)}/audio`;
    player.setAttribute("aria-label", `原录音 ${index + 1}`);
    card.append(player);
    const ranges = node("div", "field-row");
    for (const [key, title] of [["start", "起点 / 秒"], ["end", "终点 / 秒"]]) {
      const label = node("label", "field");
      label.append(node("span", "", title));
      const input = node("input");
      input.type = "number";
      input.min = "0";
      input.max = "600";
      input.step = "0.1";
      input.value = clip[key] ?? (key === "start" ? 0 : "");
      input.placeholder = key === "end" ? "留空到结尾" : "0";
      input.disabled = Boolean(state.referenceJobId);
      input.addEventListener("input", () => {
        clip[key] = input.value === "" ? null : Number(input.value);
        referenceChanged();
      });
      label.append(input);
      ranges.append(label);
    }
    card.append(ranges);
    const label = node("label", "field");
    label.append(node("span", "", "选中范围的原话"));
    const input = node("textarea");
    input.rows = 2;
    input.maxLength = 3000;
    input.placeholder = "中文可留空自动识别；有原话可直接粘贴。";
    input.value = clip.text || "";
    input.disabled = Boolean(state.referenceJobId);
    input.addEventListener("input", () => { clip.text = input.value; referenceChanged(); });
    label.append(input);
    card.append(label);
    elements.referenceClips.append(card);
  }
  updateReferenceControls();
  updateListControls();
}

async function loadVoiceLibrary() {
  if (!state.info?.features?.voice_library) return;
  const result = await api("/api/voices?include_archived=true");
  state.voiceProfiles = result.voices || [];
  elements.voiceProfile.replaceChildren(node("option", "", "准备一个新音色"));
  elements.voiceProfile.firstChild.value = "";
  for (const profile of state.voiceProfiles.filter(profile => !profile.archived)) {
    const option = node("option", "", `${profile.name} · ${profile.duration.toFixed(1)} 秒`);
    option.value = profile.id;
    elements.voiceProfile.append(option);
  }
  elements.voiceProfile.value = state.voiceProfileId;
  if (!elements.voiceProfile.value) state.voiceProfileId = "";
  const selected = state.voiceProfiles.find(item => item.id === state.voiceProfileId);
  if (selected && !elements.voiceName.value.trim()) elements.voiceName.value = selected.name;
  updateReferenceControls();
  renderVoices();
}

function syncReferenceJob() {
  if (!state.referenceJobId) return;
  const job = state.jobs.find(item => item.id === state.referenceJobId);
  if (!job) return;
  const progress = job.status === "queued" ? `队列第 ${job.queue_position || 1} 位` : `${job.completed || 0}/${job.total || 1} 段`;
  elements.referenceStatus.textContent = `${statuses[job.status] || job.status} · ${progress}。可在任务区暂停或取消。`;
  if (job.status === "completed" && job.reference) {
    state.reference = job.reference;
    state.voiceProfileId = "";
    elements.voiceProfile.value = "";
    elements.referenceText.value = job.reference_text || "";
    const descriptions = {clipping: "录音存在削波，建议换一段失真较少的录音", quiet: "录音偏轻，建议更靠近麦克风", silence: "空白较多，可缩短范围", short: "参考少于 3 秒，可补充一段清晰人声", long: "参考超过 30 秒，先用较短录音比较效果"};
    elements.referenceWarnings.textContent = (job.reference_warnings || []).map(warning => `${warning.clip ? `第 ${warning.clip} 段：` : ""}${descriptions[warning.code] || warning.code}`).join("；");
    (job.reference_clips || []).forEach((clip, index) => { if (state.referenceClips[index]) state.referenceClips[index].text = clip.text; });
    state.referenceJobId = null;
    elements.referenceStatus.textContent = "整理完成。试听参考录音、校对原话，然后生成一段新内容试听。";
    renderReferenceClips();
    persistSettings();
  } else if (["failed", "cancelled"].includes(job.status)) {
    state.referenceJobId = null;
    elements.referenceStatus.textContent = job.status === "failed" ? readableMessage(String(job.error || "整理失败，请查看任务记录。")) : "整理已取消，可以修改录音后重新整理。";
    renderReferenceClips();
    persistSettings();
  }
  updateReferenceControls();
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
  const preparationAvailable = Boolean(info.features?.reference_preparation);
  elements.cloneUpgradeNotice.classList.toggle("hidden", preparationAvailable);
  for (const id of ["referencePreparation", "voiceLibrary", "saveVoiceControls"]) elements[id].classList.toggle("hidden", !preparationAvailable);
  elements.referenceInput.multiple = preparationAvailable;
  elements.stripDownloaderMetadata.disabled = !info.features?.downloader_cleanup;
  elements.stripDownloaderMetadata.title = info.features?.downloader_cleanup ? "识别明确页头，不依赖章节标题" : "需要重启工作台加载新版本";
  elements.referenceInput.accept = [".wav", ".mp3", ".flac", ".m4a", ".ogg",
    ...(info.features?.reference_video_suffixes || [])].join(",");
  elements.referenceUploadHint.textContent = info.features?.reference_video
    ? "音频：WAV / MP3 / FLAC / M4A / OGG，最多 64 MB；视频：MP4 / M4V / MOV / MKV / WebM / AVI，最多 512 MB、10 分钟。自动提取第一条音轨。"
    : "音频：WAV / MP3 / FLAC / M4A / OGG，最多 64 MB；视频自动提取需要重启工作台加载更新。";
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
  updateHardwareDetail();
  if (info.version) elements.versionLabel.textContent = `LOCAL AUDIOBOOK STUDIO · ${info.version}`;
  const settings = mergeConfig(info.defaults || {}, state.saved?.settings || {});
  const voiceRevision = info.default_voice_revision || DEFAULT_VOICE_REVISION;
  if (state.saved?.defaultVoiceRevision !== voiceRevision) {
    settings.voice = mergeConfig(settings.voice || {}, info.default_voice_revision ? info.defaults.voice : {
      mode: "preset", speaker: "Vivian", language: "Chinese", instruct: styleInstructions.mature,
      reference_audio: null, reference_text: null,
    });
    settings.output = mergeConfig(settings.output || {}, {
      speech_rate: info.default_voice_revision ? info.defaults.output.speech_rate : 1.15,
      pitch_semitones: info.default_voice_revision ? info.defaults.output.pitch_semitones : -1,
    });
  }
  applySettings(settings);
  if (info.features?.pitch_adjustment === false) {
    elements.pitch.value = "0";
    updateRangeLabels();
  }
  persistSettings();
}

function fileSize(bytes) {
  const amount = Number(bytes);
  if (!Number.isFinite(amount)) return "大小未知";
  if (amount < 1024) return `${amount} B`;
  if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KB`;
  return `${(amount / 1024 ** 2).toFixed(1)} MB`;
}

function preserveListView(list, render) {
  const top = list.scrollTop;
  const focus = list.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  render();
  list.scrollTop = top;
  if (focus) [...list.querySelectorAll("[data-focus-key]")].find(item => item.dataset.focusKey === focus)?.focus({preventScroll: true});
}

function visibleFiles() {
  const query = elements.fileSearch.value.trim().toLocaleLowerCase();
  return state.files.filter(file => (!file.archived || elements.showArchivedFiles.checked) && String(file.name).toLocaleLowerCase().includes(query));
}

function selectedBooks() {
  return state.files.filter(file => !file.archived && state.selected.has(String(file.id)));
}

async function reloadFiles() {
  const result = await api("/api/files?include_archived=true");
  state.files = (result.files || []).filter(file => file.kind !== "reference");
  state.selected = new Set([...state.selected].filter(id => state.files.some(file => String(file.id) === id)));
  renderFiles();
}

async function changeRecords(collection, ids, archived = true) {
  for (let offset = 0; offset < ids.length; offset += 200) {
    await api(`/api/${collection}/bulk`, {method: "POST", body: {ids: ids.slice(offset, offset + 200), action: archived ? "archive" : "unarchive"}});
  }
}

async function changeFiles(ids, archived = true) {
  await changeRecords("files", ids, archived);
  ids.forEach(id => state.selected.delete(id));
  invalidatePlan();
  await reloadFiles();
  persistSettings();
  showNotice(archived ? "书稿记录已删除，原稿和已有任务保留；勾选“显示已删除”可恢复。" : "书稿记录已恢复，可重新选择生成。", "success");
}

function renderFiles() {
  const files = visibleFiles();
  preserveListView(elements.fileList, () => {
    elements.fileList.replaceChildren();
    for (const file of files) {
      const id = String(file.id);
      const row = node("div", `file-item ${state.selected.has(id) ? "selected" : ""}`);
      row.classList.toggle("archived", Boolean(file.archived));
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selected.has(id);
      checkbox.dataset.focusKey = `file:${id}`;
      checkbox.setAttribute("aria-label", `选择 ${file.name}`);
      checkbox.addEventListener("change", () => {
        checkbox.checked ? state.selected.add(id) : state.selected.delete(id);
        invalidatePlan();
        renderFiles();
        persistSettings();
      });
      const icon = node("span", "file-icon", String(file.name).split(".").pop().toUpperCase());
      const copy = node("div", "file-copy");
      copy.title = file.path || file.name;
      copy.append(node("strong", "", file.name), node("small", "", `${fileSize(file.size)} · ${file.archived ? "已删除，可恢复" : file.uploaded ? "已保存本地副本" : "本机书稿"}`));
      const remove = node("button", "button secondary compact", file.archived ? "恢复" : "删除");
      remove.type = "button";
      remove.dataset.focusKey = `file-action:${id}`;
      remove.disabled = !state.info?.features?.list_management || state.mutation;
      remove.setAttribute("aria-label", `${file.archived ? "恢复" : "删除"} ${file.name}`);
      remove.addEventListener("click", () => mutate("正在更新书稿列表", () => changeFiles([id], !file.archived)));
      row.append(checkbox, icon, copy, remove);
      elements.fileList.append(row);
    }
    if (!files.length) elements.fileList.append(node("p", "list-empty", state.files.length ? "没有匹配的书稿；可以修改搜索或显示已删除记录。" : "还没有书稿，点击上方选择文件。"));
  });
  updateSelection();
  updateListControls();
}

function visibleVoices() {
  const query = elements.voiceSearch.value.trim().toLocaleLowerCase();
  return state.voiceProfiles.filter(profile => (!profile.archived || elements.showArchivedVoices.checked) && profile.name.toLocaleLowerCase().includes(query));
}

function chooseVoice(profile) {
  if (state.referenceJobId) {
    elements.voiceProfile.value = state.voiceProfileId;
    showNotice("请等当前整理完成，或先在任务区取消它。", "info");
    return;
  }
  state.voiceProfileId = profile?.id || "";
  elements.voiceProfile.value = state.voiceProfileId;
  state.reference = profile?.reference || null;
  elements.referenceText.value = profile?.text || "";
  elements.voiceName.value = profile?.name || "";
  elements.referenceWarnings.textContent = "";
  updateReferenceControls();
  renderVoices();
  persistSettings();
}

async function changeVoices(ids, archived = true) {
  await changeRecords("voices", ids, archived);
  state.selectedVoices.clear();
  await loadVoiceLibrary();
  persistSettings();
  showNotice(archived ? "音色记录已删除，录音和已有任务保留；可显示已删除音色并恢复。" : "音色已恢复到本地音色库。", "success");
}

function renderVoices() {
  const voices = visibleVoices();
  state.selectedVoices = new Set([...state.selectedVoices].filter(id => voices.some(profile => profile.id === id)));
  preserveListView(elements.voiceList, () => {
    elements.voiceList.replaceChildren();
    for (const profile of voices) {
      const row = node("div", `managed-row ${profile.id === state.voiceProfileId ? "active" : ""}`);
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedVoices.has(profile.id);
      checkbox.dataset.focusKey = `voice:${profile.id}`;
      checkbox.setAttribute("aria-label", `选择音色 ${profile.name}`);
      checkbox.addEventListener("change", () => {
        checkbox.checked ? state.selectedVoices.add(profile.id) : state.selectedVoices.delete(profile.id);
        updateListControls();
      });
      const use = node("button", "list-open");
      use.type = "button";
      use.disabled = Boolean(profile.archived) || Boolean(state.referenceJobId);
      use.dataset.focusKey = `voice-open:${profile.id}`;
      use.append(node("strong", "", profile.name), node("small", "", `${profile.duration.toFixed(1)} 秒 · ${profile.archived ? "已删除" : profile.id === state.voiceProfileId ? "当前使用" : "点击使用"}`));
      use.addEventListener("click", () => chooseVoice(profile));
      const remove = node("button", "button secondary compact", profile.archived ? "恢复" : "删除");
      remove.type = "button";
      remove.disabled = !state.info?.features?.list_management || state.mutation;
      remove.addEventListener("click", () => mutate("正在更新音色库", () => changeVoices([profile.id], !profile.archived)));
      row.append(checkbox, use, remove);
      elements.voiceList.append(row);
    }
    if (!voices.length) elements.voiceList.append(node("p", "list-empty", "没有匹配的已保存音色。整理录音后可保存，或显示已删除音色。"));
  });
  updateListControls();
}

function updateListControls() {
  const enabled = Boolean(state.info?.features?.list_management) && state.connected && !state.mutation;
  const selectedFiles = state.files.filter(file => state.selected.has(String(file.id)));
  const selectedVoices = state.voiceProfiles.filter(profile => state.selectedVoices.has(profile.id));
  const selectedJobs = allJobs().filter(job => state.selectedJobs.has(job.displayId));
  for (const [suffix, items] of [["Files", selectedFiles], ["Voices", selectedVoices], ["Jobs", selectedJobs]]) {
    elements[`remove${suffix}`].disabled = !enabled || !items.some(item => !item.archived);
    elements[`restore${suffix}`].disabled = !enabled || !items.some(item => item.archived);
    elements[`clear${suffix}`].disabled = !items.length || state.mutation;
    elements[`remove${suffix}`].textContent = `删除选中${items.some(item => !item.archived) ? `（${items.filter(item => !item.archived).length}）` : ""}`;
  }
  elements.selectFiles.disabled = !visibleFiles().length || state.mutation;
  elements.selectVoices.disabled = !visibleVoices().length || state.mutation;
  elements.selectJobs.disabled = !visibleJobs().some(job => !job.readonly || job.archived || job.can_remove) || state.mutation;
  elements.clearReferences.disabled = !state.referenceClips.length || Boolean(state.referenceJobId) || state.mutation;
  elements.fileListSummary.textContent = `列表 ${visibleFiles().length} / ${state.files.length} 本 · 已选 ${selectedFiles.length} 本。删除仅移除记录，原文件保留。`;
  elements.voiceListSummary.textContent = `列表 ${visibleVoices().length} 个音色 · 已选 ${selectedVoices.length} 个。删除后可恢复。`;
  elements.jobListSummary.textContent = `列表 ${visibleJobs().length} 个任务 · 已选 ${selectedJobs.length} 个。运行中的任务会先安全取消，再删除记录；音频保留。`;
}

function updateSelection() {
  const count = selectedBooks().length;
  elements.fileCount.textContent = `${count} 本已选`;
  elements.selectionSummary.textContent = count ? `已选择 ${count} 本小说${state.plan ? "，检查结果已准备。" : "，请先检查正文。"}` : "选择书稿后，先检查正文。";
  if (count > 100) elements.selectionSummary.textContent = `已选 ${count} 本；单次最多安排 100 本，请分批检查并加入队列。`;
  elements.planButton.disabled = !count || count > 100 || state.mutation || !state.connected;
  elements.startButton.disabled = !count || count > 100 || !state.plan || state.mutation || !state.connected;
  elements.previewButton.disabled = state.mutation || !state.connected;
}

function invalidatePlan() {
  state.plan = null;
  state.planSignature = null;
  elements.planCard.classList.add("hidden");
  updateSelection();
}

function planSignature(settings) {
  return JSON.stringify({files: selectedBooks().map(file => String(file.id)).sort(), segment: settings.segment, text: settings.text, chapters: settings.chapters || ""});
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
  strip_downloader_metadata: "自动清理下载器页头（书名和作者保留为元数据）",
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
    const result = await api("/api/plan", {method: "POST", body: {files: selectedBooks().map(file => String(file.id)), settings}});
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
    const request = {kind, settings, files: kind === "preview" ? [] : selectedBooks().map(file => String(file.id))};
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
  if (job.kind === "reference") return "参考录音整理与识别";
  if (job.kind === "preview") return "声音试听";
  return job.title || job.book_title || job.name || (job.kind === "preview" ? "声音试听" : "小说生成任务");
}

function externalId(job, index = 0) {
  return `external:${job.id || job.book_id || job.path || index}`;
}

function allJobs() {
  return [...state.jobs.filter(job => !job.archived || state.showArchived).map(job => ({...job, displayId: String(job.id), readonly: false})), ...state.external.filter(job => !job.archived || state.showArchived).map((job, index) => ({...job, displayId: externalId(job, index), readonly: true}))];
}

function updateQueueControls() {
  const waitingCount = state.jobs.filter(job => job.status === "queued").length;
  elements.cancelQueued.disabled = !waitingCount || state.mutation;
  elements.cancelQueued.textContent = waitingCount ? `取消全部排队任务（${waitingCount}）` : "取消全部排队任务";
}

function visibleJobs() {
  const query = elements.jobSearch.value.trim().toLocaleLowerCase();
  const filter = elements.jobFilter.value;
  return allJobs().filter(job => jobTitle(job).toLocaleLowerCase().includes(query) &&
    (filter === "all" || (filter === "queued" ? job.status === "queued" : filter === "active" ?
      ["running", "preparing", "pausing", "cancelling", "stopping"].includes(job.status) :
      ["completed", "failed", "paused", "cancelled"].includes(job.status))));
}

async function changeJobs(ids, restore = false) {
  const jobs = allJobs().filter(job => ids.includes(job.displayId) && Boolean(job.archived) === restore);
  const owned = jobs.filter(job => !job.readonly).map(job => String(job.id));
  const external = jobs.filter(job => job.readonly);
  if (!restore && external.some(job => !(job.can_remove ?? !state.queueBlocked))) throw new Error("原窗口仍在运行，请先停止它，再删除原有任务记录。");
  for (let offset = 0; offset < owned.length; offset += 200) {
    await api("/api/jobs/bulk", {method: "POST", body: {ids: owned.slice(offset, offset + 200), action: restore ? "unarchive" : "remove"}});
  }
  for (const job of external) await api(`/api/monitor/${encodeURIComponent(job.id)}/${restore ? "unarchive" : "archive"}`, {method: "POST"});
  ids.forEach(id => state.selectedJobs.delete(id));
  await refresh(true);
  showNotice(restore ? "选中的任务记录已恢复。" : "已提交删除；运行中的任务会在安全结束后移出列表，原稿和音频保留。", "success");
}

function renderJobs() {
  const jobs = visibleJobs();
  state.selectedJobs = new Set([...state.selectedJobs].filter(id => jobs.some(job => job.displayId === id)));
  if (!jobs.some(job => job.displayId === state.activeId)) {
    const active = jobs.find(job => job.status === "running") || jobs.find(job => job.status === "queued") || jobs[0];
    state.activeId = active?.displayId || null;
    state.detail = null;
  }
  const queuedCount = state.jobs.filter(job => ["running", "queued", "preparing", "pausing", "cancelling", "stopping"].includes(job.status)).length;
  updateQueueControls();
  elements.queueCount.textContent = queuedCount ? `${queuedCount} 个任务进行中` : allJobs().length ? `${allJobs().length} 个任务` : "队列为空";
  elements.emptyJobs.classList.toggle("hidden", jobs.length > 0);
  elements.externalNotice.classList.toggle("hidden", !state.queueBlocked && !state.external.some(job => ["running", "preparing"].includes(job.status)));
  preserveListView(elements.jobList, () => {
    elements.jobList.replaceChildren();
    for (const job of jobs) {
      const row = node("div", `managed-row ${state.activeId === job.displayId ? "active" : ""}`);
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedJobs.has(job.displayId);
      checkbox.disabled = Boolean(job.readonly && !job.archived && !(job.can_remove ?? !state.queueBlocked));
      checkbox.dataset.focusKey = `job:${job.displayId}`;
      checkbox.setAttribute("aria-label", `选择任务 ${jobTitle(job)}`);
      checkbox.addEventListener("change", () => {
        checkbox.checked ? state.selectedJobs.add(job.displayId) : state.selectedJobs.delete(job.displayId);
        updateListControls();
      });
      const button = node("button", `list-open job-tab ${state.activeId === job.displayId ? "active" : ""}`);
      button.type = "button";
      button.dataset.focusKey = `job-open:${job.displayId}`;
      const progress = metrics(job);
      const percentage = progress.percent === null ? "" : ` · ${progress.percent.toFixed(0)}%`;
      const queueLabel = job.queue_position ? ` · 第 ${job.queue_position} 位` : "";
      button.append(node("strong", "", jobTitle(job)), node("small", "", `${statuses[job.status] || job.status || "状态未知"}${percentage}${queueLabel}${job.archived ? " · 已删除" : ""}${job.archive_when_stopped ? " · 等待删除" : ""}`));
      button.setAttribute("aria-pressed", String(state.activeId === job.displayId));
      button.addEventListener("click", async () => {
        state.activeId = job.displayId;
        state.detail = null;
        renderJobs();
        await refreshActiveDetail();
      });
      const remove = node("button", "button secondary compact", job.archived ? "恢复" : "删除");
      remove.type = "button";
      remove.dataset.focusKey = `job-action:${job.displayId}`;
      remove.disabled = !state.info?.features?.list_management || state.mutation || checkbox.disabled || Boolean(job.archive_when_stopped);
      remove.setAttribute("aria-label", `${job.archived ? "恢复" : "删除"}任务 ${jobTitle(job)}`);
      remove.addEventListener("click", () => mutate("正在更新任务记录", () => changeJobs([job.displayId], Boolean(job.archived))));
      row.append(checkbox, button, remove);
      elements.jobList.append(row);
    }
    if (!jobs.length) elements.jobList.append(node("p", "list-empty", "没有匹配的任务。可修改搜索、状态或显示已删除记录。"));
  });
  renderJobDetail();
  updateListControls();
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

function exportFormat(item) {
  return String(item.format || item.name?.split(".").pop() || "").toLowerCase();
}

function renderExports(exports) {
  elements.exportList.replaceChildren();
  const available = (Array.isArray(exports) ? exports : []).filter(item => safeExportUrl(item.url));
  for (const item of available) {
    const link = node("a", "export-link");
    link.href = safeExportUrl(item.url);
    link.download = item.name || "";
    const format = (exportFormat(item) || "audio").toUpperCase();
    const scope = scopeLabel(item);
    link.append(node("small", "", format), node("span", "", `下载 ${item.name || item.book_title || "音频"}${scope ? ` · ${scope}` : ""}`));
    elements.exportList.append(link);
  }
  const playable = available.find(item => /^(mp3|wav|flac|m4a|ogg)$/.test(exportFormat(item))) || available.find(item => exportFormat(item) === "m4b");
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

function savedDownloads() {
  try {
    const saved = JSON.parse(localStorage.getItem(DOWNLOAD_STORAGE_KEY) || "null");
    return saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
  } catch (_) { return {}; }
}

function loadDownloadSettings() {
  const saved = savedDownloads();
  const downloads = state.downloads;
  downloads.format = DOWNLOAD_FORMATS.includes(saved.format) ? saved.format : "flac";
  downloads.auto = saved.auto === true;
  downloads.since = Number.isFinite(saved.since) && saved.since > 0 ? saved.since : Date.now();
  downloads.submitted = saved.submitted && typeof saved.submitted === "object" && !Array.isArray(saved.submitted) ? saved.submitted : {};
  downloads.autoPaused = false;
}

function persistDownloads() {
  const downloads = state.downloads;
  try {
    localStorage.setItem(DOWNLOAD_STORAGE_KEY, JSON.stringify({format: downloads.format, auto: downloads.auto,
      since: downloads.since, submitted: downloads.submitted}));
    return true;
  } catch (_) {
    downloads.auto = false;
    downloads.autoPaused = true;
    downloads.error = true;
    downloads.message = "浏览器未能保存下载记录，自动下载已停止；仍可手动下载。";
    return false;
  }
}

function downloadKey(job, format = state.downloads.format) {
  return `${job.id}:${format}:${job.finished_at || job.created_at || "completed"}`;
}

function downloadFiles(job, format = state.downloads.format) {
  const files = new Map();
  for (const item of Array.isArray(job.exports) ? job.exports : []) {
    if (exportFormat(item) === format && safeExportUrl(item.url)) files.set(item.id || item.url, item);
  }
  return [...files.values()];
}

function downloadableJobs() {
  return state.jobs.filter(job => job.status === "completed" && job.kind !== "reference" &&
    (!job.archived || state.showArchived) && downloadFiles(job).length);
}

function queueBusy() {
  return state.jobs.some(job => ["running", "queued", "preparing", "pausing", "cancelling", "stopping"].includes(job.status));
}

function visibleDownloadJobs() {
  const query = elements.downloadSearch.value.trim().toLocaleLowerCase();
  return downloadableJobs().filter(job => jobTitle(job).toLocaleLowerCase().includes(query));
}

function renderDownloads() {
  const downloads = state.downloads;
  const supported = Boolean(state.info?.features?.batch_download);
  const jobs = visibleDownloadJobs();
  const focusedJob = document.activeElement?.dataset.downloadJob;
  const scrollTop = elements.downloadList.scrollTop;
  downloads.selected = new Set([...downloads.selected].filter(id => jobs.some(job => String(job.id) === id)));
  elements.downloadFormat.value = downloads.format;
  elements.autoDownload.checked = downloads.auto;
  elements.downloadFormat.disabled = downloads.busy;
  elements.autoDownload.disabled = downloads.busy || !supported;
  elements.downloadCount.textContent = jobs.length ? `${jobs.length} 个可下载任务` : "暂无可下载任务";
  elements.downloadHelp.textContent = supported ? "默认选 FLAC，自动下载默认关闭。这里只收取已经生成的格式，不会重新合成或转码。" :
    "批量下载需要 0.3.1 或更新的工作台后台；已有单文件下载链接仍可使用。";
  elements.downloadList.replaceChildren();
  for (const job of jobs) {
    const files = downloadFiles(job);
    const row = node("label", "download-item");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.dataset.downloadJob = String(job.id);
    checkbox.checked = downloads.selected.has(String(job.id));
    row.classList.toggle("selected", checkbox.checked);
    checkbox.disabled = downloads.busy;
    checkbox.setAttribute("aria-label", `选择下载 ${jobTitle(job)}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) downloads.selected.add(String(job.id));
      else downloads.selected.delete(String(job.id));
      renderDownloads();
    });
    const copy = node("span", "download-item-copy");
    const size = files.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
    const sent = downloads.submitted[downloadKey(job)] ? " · 已提交浏览器，可再次下载" : "";
    copy.append(node("strong", "", jobTitle(job)), node("small", "", `${files.length} 个 ${downloads.format.toUpperCase()} · ${fileSize(size)}${sent}`));
    row.append(checkbox, copy);
    elements.downloadList.append(row);
  }
  if (!jobs.length) elements.downloadList.append(node("p", "field-help", `还没有已完成的 ${downloads.format.toUpperCase()} 音频任务。完成后会出现在这里；已删除记录需先勾选“显示已删除记录”。`));
  if (focusedJob) [...elements.downloadList.querySelectorAll("input")].find(input => input.dataset.downloadJob === focusedJob)?.focus({preventScroll: true});
  elements.downloadList.scrollTop = scrollTop;
  const selected = jobs.filter(job => downloads.selected.has(String(job.id)));
  const files = selected.flatMap(job => downloadFiles(job));
  const size = files.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
  elements.selectDownloads.disabled = !jobs.length || downloads.busy;
  elements.clearDownloads.disabled = !selected.length || downloads.busy;
  elements.removeDownloads.disabled = !selected.some(job => !job.archived) || downloads.busy || state.mutation || !state.info?.features?.list_management;
  elements.batchDownload.disabled = !selected.length || selected.length > 100 || downloads.busy || !supported || !state.connected;
  elements.selectDownloads.textContent = jobs.length > 100 ? "选择前 100 项" : "选择全部";
  elements.batchDownload.textContent = downloads.busy ? "正在准备下载…" : selected.length ? `批量下载 ZIP（${selected.length}）` : "批量下载 ZIP";
  elements.downloadSummary.textContent = selected.length ? `已选择 ${selected.length} 个任务、${files.length} 个音频，约 ${fileSize(size)}。${selected.length > 100 ? "单次最多 100 个任务，请减少选择。" : "原文件按任务与书籍分目录保留，音质不变。"}` : "选择已完成任务后，一次下载全部所选音频；每次最多 100 个任务。";
  elements.downloadStatus.classList.toggle("error", downloads.error);
  elements.downloadStatus.textContent = downloads.message || (downloads.auto ?
    queueBusy() ? "自动下载已开启，等待队列空闲后收取新完成的小说。" : "自动下载已开启，等待新完成的小说；重开页面后会补领未提交的结果。" :
    "自动下载已关闭，音频仍保存在项目输出目录。");
}

async function withDownloadLock(action, automatic = false) {
  if (!navigator.locks?.request) return action();
  return navigator.locks.request("local-audiobook-downloads", automatic ? {ifAvailable: true} : {}, lock => lock ? action() : undefined);
}

async function submitDownloads(jobs, automatic = false) {
  const downloads = state.downloads;
  if (downloads.busy || !jobs.length) return;
  if (jobs.length > 100) throw new Error("一次最多下载 100 个任务，请分批选择。");
  const format = downloads.format;
  downloads.busy = true;
  downloads.error = false;
  downloads.message = `正在准备 ${jobs.length} 个任务的下载清单…`;
  renderDownloads();
  try {
    const result = await api("/api/downloads", {method: "POST", body: {jobs: jobs.map(job => String(job.id)), format}});
    const url = safeExportUrl(result.url);
    if (!url || new URL(url).pathname !== "/api/downloads") throw new Error("本地服务未返回有效的打包下载地址。");
    if (automatic && (!downloads.auto || downloads.format !== format)) return;
    const fresh = savedDownloads();
    if (automatic && (fresh.auto !== true || fresh.format !== format || fresh.since !== downloads.since)) return;
    if (fresh.submitted && typeof fresh.submitted === "object" && !Array.isArray(fresh.submitted)) Object.assign(downloads.submitted, fresh.submitted);
    for (const job of jobs) downloads.submitted[downloadKey(job, format)] = Date.now();
    const saved = persistDownloads();
    if (automatic && !saved) return;
    const link = node("a", "hidden");
    link.href = url;
    link.download = "";
    document.body.append(link);
    try { link.click(); } finally { link.remove(); }
    downloads.error = !saved;
    downloads.message = `${automatic ? "自动收取：" : ""}${result.jobs} 个任务、${result.files} 个音频（约 ${fileSize(result.size)}）已交给浏览器下载。请在浏览器下载列表确认；若被拦截，可允许本站下载后手动重试。${saved ? "" : " 下载记录未能保存，自动下载已停止。"}`;
  } catch (error) {
    downloads.error = true;
    if (automatic) downloads.autoPaused = true;
    downloads.message = `${error.message}${automatic ? " 自动收取暂时停止，可手动下载或重新开启开关。" : ""}`;
  } finally {
    downloads.busy = false;
    renderDownloads();
  }
}

async function maybeAutoDownload() {
  const downloads = state.downloads;
  if (!downloads.auto || downloads.autoPaused || downloads.busy || !state.connected || !state.info?.features?.batch_download || queueBusy()) return;
  await withDownloadLock(async () => {
    const saved = savedDownloads();
    if (saved.auto !== true || saved.format !== downloads.format) return;
    if (Number.isFinite(saved.since) && saved.since > 0) downloads.since = saved.since;
    if (saved.submitted && typeof saved.submitted === "object" && !Array.isArray(saved.submitted)) Object.assign(downloads.submitted, saved.submitted);
    const jobs = downloadableJobs().filter(job => (job.kind === "convert" || !job.kind) &&
      Date.parse(job.finished_at || "") >= downloads.since && !downloads.submitted[downloadKey(job)]);
    await submitDownloads(jobs.slice(0, 100), true);
  }, true);
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
    archive: ["正在删除显示记录", "记录已移入回收列表，原文、音频和缓存保留。勾选“显示已删除记录”可以恢复。"],
    unarchive: ["正在恢复任务记录", "记录已恢复显示，任务不会自动启动。"],
  };
  const [busy, success] = messages[action];
  button.addEventListener("click", () => mutate(busy, async () => {
    if (job.readonly && !state.info?.features?.monitor_archive) {
      const monitor = await api("/api/monitor");
      if (action === "archive" && monitor.queue_blocked) {
        throw new Error(readableMessage("Stop the external conversion before removing its display record"));
      }
      // Older running servers cannot load new routes until restarted. Keep
      // recoverable display changes locally, then sync them to the new API.
      state.monitorOverrides[job.id] = action === "archive";
      persistSettings();
    } else {
      const group = job.readonly ? "monitor" : "jobs";
      await api(`/api/${group}/${encodeURIComponent(job.id)}/${action}`, {method: "POST"});
      if (job.readonly) {
        delete state.monitorOverrides[job.id];
        persistSettings();
      }
    }
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
  const subtitle = [job.readonly ? "原有命令行任务 · 仅查看" : job.kind === "reference" ? "参考录音整理 · 原始录音保留" : job.kind === "preview" ? "真实模型试听 · 使用当前设置快照" : "本地批量生成 · 自动保存进度"];
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
  elements.segmentProgress.textContent = progress.completed === null ? "等待进度信息" : `${progress.completed.toLocaleString()} / ${progress.total === null ? "待确认" : progress.total.toLocaleString()} ${job.kind === "reference" ? "段录音已整理" : "段已完成核对"}`;
  elements.audioDuration.closest(".metrics-grid").classList.toggle("hidden", job.kind === "reference");
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
    if (job.archived || (job.can_remove ?? !state.queueBlocked)) {
      elements.jobActions.append(actionButton(job.archived ? "恢复记录" : "删除记录", job.archived ? "unarchive" : "archive", job));
      elements.jobActions.append(node("p", "", "历史记录可以删除或恢复；原文、音频和缓存保留。删除记录不会启动转换。"));
    } else {
      elements.jobActions.append(node("p", "", "原窗口的转换正在运行，这里仅显示进度。停止后可删除显示记录。"));
    }
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
      elements.jobActions.append(actionButton(job.archived ? "恢复记录" : "删除记录", job.archived ? "unarchive" : "archive", job));
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
  if (state.refreshing || (!force && document.hidden && !state.downloads.auto)) return;
  state.refreshing = true;
  try {
    if (state.info?.features?.monitor_archive) {
      for (const [identifier, removed] of Object.entries(state.monitorOverrides)) {
        try {
          await api(`/api/monitor/${encodeURIComponent(identifier)}/${removed ? "archive" : "unarchive"}`, {method: "POST"});
          delete state.monitorOverrides[identifier];
          persistSettings();
        } catch (_) {
          // Keep the local intent if a running external conversion defers removal.
        }
      }
    }
    const responses = await Promise.allSettled([api("/api/jobs"), api(`/api/monitor?include_removed=${state.showArchived}`)]);
    if (responses[0].status === "fulfilled") {
      state.jobs = responses[0].value.jobs || [];
      const active = state.jobs.find(job => String(job.id) === state.activeId);
      if (active && (Array.isArray(active.logs) || Array.isArray(active.exports))) state.detail = active;
      setConnected(true);
    } else {
      setConnected(false);
    }
    if (responses[1].status === "fulfilled") {
      state.external = (responses[1].value.tasks || responses[1].value.jobs || []).map(job => ({
        ...job, archived: state.monitorOverrides[job.id] ?? job.archived,
      }));
      state.queueBlocked = Boolean(responses[1].value.queue_blocked);
    }
    if (state.connected && !state.info) {
      try {
        renderInfo(await api("/api/info"));
        const files = await api("/api/files?include_archived=true");
        state.files = (files.files || []).filter(file => file.kind !== "reference");
        renderFiles();
      } catch (_) {
        // The next refresh retries initialization after a temporary service restart.
      }
    }
    syncReferenceJob();
    renderJobs();
    const selected = state.jobs.find(job => String(job.id) === state.activeId);
    if (!selected || (!Array.isArray(selected.logs) && !Array.isArray(selected.exports))) await refreshActiveDetail();
    updateSelection();
    renderDownloads();
    try { await maybeAutoDownload(); } catch (error) {
      state.downloads.autoPaused = true;
      state.downloads.error = true;
      state.downloads.message = `自动收取暂时停止：${error.message}。可以手动下载或重新开启开关。`;
      renderDownloads();
    }
  } finally {
    state.refreshing = false;
  }
}

function attachEvents() {
  elements.fileSearch.addEventListener("input", renderFiles);
  elements.showArchivedFiles.addEventListener("change", renderFiles);
  elements.selectFiles.addEventListener("click", () => { visibleFiles().forEach(file => state.selected.add(String(file.id))); invalidatePlan(); renderFiles(); persistSettings(); });
  elements.clearFiles.addEventListener("click", () => { state.selected.clear(); invalidatePlan(); renderFiles(); persistSettings(); });
  for (const [id, archived] of [["removeFiles", true], ["restoreFiles", false]]) elements[id].addEventListener("click", () => mutate("正在更新书稿记录", () => changeFiles(state.files.filter(file => state.selected.has(String(file.id)) && Boolean(file.archived) !== archived).map(file => String(file.id)), archived)));
  elements.jobSearch.addEventListener("input", renderJobs);
  elements.jobFilter.addEventListener("change", renderJobs);
  elements.selectJobs.addEventListener("click", () => { state.selectedJobs = new Set(visibleJobs().filter(job => !job.readonly || job.archived || (job.can_remove ?? !state.queueBlocked)).map(job => job.displayId)); renderJobs(); });
  elements.clearJobs.addEventListener("click", () => { state.selectedJobs.clear(); renderJobs(); });
  for (const [id, restore] of [["removeJobs", false], ["restoreJobs", true]]) elements[id].addEventListener("click", () => mutate("正在更新任务记录", () => changeJobs([...state.selectedJobs], restore)));
  elements.voiceSearch.addEventListener("input", renderVoices);
  elements.showArchivedVoices.addEventListener("change", renderVoices);
  elements.selectVoices.addEventListener("click", () => { state.selectedVoices = new Set(visibleVoices().map(profile => profile.id)); renderVoices(); });
  elements.clearVoices.addEventListener("click", () => { state.selectedVoices.clear(); renderVoices(); });
  for (const [id, archived] of [["removeVoices", true], ["restoreVoices", false]]) elements[id].addEventListener("click", () => mutate("正在更新音色记录", () => changeVoices(state.voiceProfiles.filter(profile => state.selectedVoices.has(profile.id) && Boolean(profile.archived) !== archived).map(profile => profile.id), archived)));
  elements.clearReferences.addEventListener("click", () => mutate("正在移除参考素材", async () => {
    if (state.info?.features?.list_management) await changeRecords("files", state.referenceClips.map(clip => clip.id));
    state.referenceClips = [];
    referenceChanged();
    renderReferenceClips();
  }));
  elements.downloadSearch.addEventListener("input", renderDownloads);
  elements.removeDownloads.addEventListener("click", () => mutate("正在删除下载任务记录", () => changeJobs([...state.downloads.selected])));

  elements.downloadFormat.addEventListener("change", () => {
    state.downloads.format = elements.downloadFormat.value;
    state.downloads.since = Date.now();
    state.downloads.selected.clear();
    state.downloads.autoPaused = false;
    state.downloads.message = "";
    state.downloads.error = false;
    persistDownloads();
    renderDownloads();
  });
  elements.autoDownload.addEventListener("change", () => {
    state.downloads.auto = elements.autoDownload.checked;
    if (state.downloads.auto) state.downloads.since = Date.now();
    state.downloads.autoPaused = false;
    state.downloads.message = "";
    state.downloads.error = false;
    persistDownloads();
    renderDownloads();
  });
  elements.selectDownloads.addEventListener("click", () => {
    state.downloads.selected = new Set(visibleDownloadJobs().slice(0, 100).map(job => String(job.id)));
    renderDownloads();
  });
  elements.clearDownloads.addEventListener("click", () => {
    state.downloads.selected.clear();
    renderDownloads();
  });
  elements.batchDownload.addEventListener("click", async () => {
    try {
      await withDownloadLock(() => submitDownloads(downloadableJobs().filter(job => state.downloads.selected.has(String(job.id)))));
    } catch (error) {
      state.downloads.error = true;
      state.downloads.message = error.message;
      renderDownloads();
    }
  });
  window.addEventListener("storage", event => {
    if (event.key === DOWNLOAD_STORAGE_KEY) {
      loadDownloadSettings();
      renderDownloads();
    }
  });
  elements.showArchived.addEventListener("change", () => {
    state.showArchived = elements.showArchived.checked;
    refresh(true);
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
  elements.referenceInput.addEventListener("change", () => mutate("正在保存参考素材；视频将自动提取音频", async () => {
    const files = [...elements.referenceInput.files];
    elements.referenceInput.value = "";
    if (!files.length) return;
    if (state.referenceJobId) throw new Error("请等当前整理完成，或先在任务区取消它。");
    const modern = Boolean(state.info?.features?.reference_preparation);
    if (modern && state.referenceClips.length + files.length > 5) throw new Error("最多使用 5 段录音；可以先移除一段再添加。");
    const videoSuffixes = state.info?.features?.reference_video_suffixes || [];
    for (const file of files) {
      const suffix = `.${file.name.split(".").pop().toLowerCase()}`;
      const video = videoSuffixes.includes(suffix);
      if (!elements.referenceInput.accept.split(",").includes(suffix)) throw new Error("当前后台不支持此参考格式；视频自动提取需要重启工作台加载更新。");
      if (file.size > (video ? 512 : 64) * 1024 ** 2) throw new Error(video ? "每个参考视频最多 512 MB，请选择较短的视频。" : "每段参考录音最多 64 MB，请选择较短的录音。");
    }
    for (const file of files) {
      const body = new FormData();
      body.append("file", file);
      const result = await api("/api/files/reference", {method: "POST", body});
      const reference = result.file || result.reference || result;
      if (modern) {
        state.referenceClips.push({...reference, start: 0, end: null, text: ""});
        referenceChanged();
        renderReferenceClips();
      } else {
        state.reference = reference;
        updateReferenceControls();
        persistSettings();
      }
    }
    showNotice(modern ? "参考音频已保存，视频已自动提取音频。可试听并调整起止范围、填写原话，然后点击整理；空白原话自动识别中文。" : "参考录音已保存，请填写准确原话。自动整理和音色库需要重启工作台。", "success");
  }));
  elements.prepareReference.addEventListener("click", () => mutate("正在安排参考录音整理", async () => {
    const clips = state.referenceClips.map(clip => ({id: clip.id, start: clip.start ?? 0, end: clip.end ?? null, text: clip.text || ""}));
    for (const clip of clips) if (!Number.isFinite(clip.start) || clip.start < 0 || (clip.end !== null && (!Number.isFinite(clip.end) || clip.end <= clip.start))) throw new Error("请检查每段录音的起止范围。");
    if (!state.info.features?.reference_asr && clips.some(clip => !clip.text.trim())) throw new Error("本地识别环境未安装，请填写每段录音的原话。");
    const job = await api("/api/references/prepare", {method: "POST", body: {clips}});
    state.referenceJobId = job.id;
    state.reference = null;
    state.activeId = job.id;
    state.detail = null;
    renderReferenceClips();
    persistSettings();
    await refresh(true);
    showNotice("录音整理已进入队列。完成后校对文字、保存音色，再生成新内容试听。", "success");
  }, elements.prepareReference));
  elements.voiceProfile.addEventListener("change", () => chooseVoice(state.voiceProfiles.find(profile => !profile.archived && profile.id === elements.voiceProfile.value)));
  elements.saveVoice.addEventListener("click", () => mutate("正在保存本地音色", async () => {
    const profile = await api("/api/voices", {method: "POST", body: {name: elements.voiceName.value.trim(), reference_id: state.reference.id, text: elements.referenceText.value.trim()}});
    state.voiceProfileId = profile.id;
    state.reference = profile.reference;
    await loadVoiceLibrary();
    persistSettings();
    showNotice("音色已保存在本机，下次可直接选择。请先生成新内容试听。", "success");
  }, elements.saveVoice));
  elements.archiveVoice.addEventListener("click", () => mutate("正在更新音色库", () => changeVoices([state.voiceProfileId]), elements.archiveVoice));
  for (const input of [elements.voiceName, elements.referenceText]) input.addEventListener("input", () => { updateReferenceControls(); persistSettings(); });
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
    if (button.dataset.style === "mature") {
      applyMaturePreset();
    } else {
      elements.instruct.value = styleInstructions[button.dataset.style];
    }
    persistSettings();
  }));
  const planInputs = new Set(["stripDownloaderMetadata", "removeUrls", "stripFrontMatter", "joinLines", "removePatterns", "pronunciation", "chapters", "targetChars", "maxChars", "minChars"]);
  document.querySelectorAll(".settings-panel input,.settings-panel select,.settings-panel textarea,.cleaning-options input,.cleaning-options textarea").forEach(input => {
    input.addEventListener("input", () => {
      updateRangeLabels();
      if (input.id === "speaker") updateVoiceDescription();
      if (input.id === "modelPath") updateHardwareDetail();
      if (planInputs.has(input.id)) invalidatePlan();
      persistSettings();
    });
    input.addEventListener("change", persistSettings);
  });
  elements.resetSettings.addEventListener("click", () => {
    if (!state.info) return;
    state.saved = null;
    state.reference = null;
    if (!state.referenceJobId) state.referenceClips = [];
    state.voiceProfileId = "";
    elements.voiceProfile.value = "";
    applySettings(state.info.defaults || {});
    renderReferenceClips();
    if (!state.info.default_voice_revision) applyMaturePreset();
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
  initializeTheme();
  loadSavedSettings();
  loadDownloadSettings();
  attachEvents();
  const results = await Promise.allSettled([api("/api/info"), api("/api/files?include_archived=true")]);
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
  renderReferenceClips();
  try { await loadVoiceLibrary(); } catch (error) { showNotice(`无法读取本地音色库：${error.message}`, "error"); }
  await refresh(true);
  setInterval(() => refresh(), 3000);
}

initialize().catch(error => {
  setConnected(false);
  showNotice(`界面初始化失败：${error.message}`, "error");
});
