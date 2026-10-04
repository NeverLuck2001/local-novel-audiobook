# 本地小说有声书工作台

[English](README.md) · [架构说明](docs/architecture.md) · [速度实测](docs/performance.md)

把中文 TXT、EPUB 小说批量转换为有声书，全程在自己的电脑上运行。使用官方 **Qwen3-TTS-12Hz-1.7B-CustomVoice** 合成语音，可选官方 **Qwen3-ASR-0.6B** 检查漏读、重复和错读，通过 FFmpeg 导出章节音频与带章节标记的 M4B。

安装模型需要下载；转换使用本地模型路径和离线推理设置，不向云端 TTS 服务发送小说。

## 功能

- 本地浏览器界面：选择／上传文件，预览章节、字数、分段与清洗审计。
- 九个官方预设音色、自由填写语气指令、短文本试听。
- 语速 0.75–1.5 倍、音高 ±3 半音、句段停顿、响度调节。
- 原文保留的发音替换表；ASR 按实际送入合成的文字核对。
- 可选网址、下载器正文前杂文、自定义整行规则清理。
- 章节选择、持久排队顺序与队列位置、下一个运行、安全暂停／取消、恢复、失败段重试。
- 一键取消全部排队任务；隐藏／找回已停止记录，保留所有音频。
- 展示进度、已生成音频时长、合成耗时、RTF、预计剩余时间和日志。
- 只读观察已启动的命令行任务，新任务等它结束后再用显卡。
- 官方批量推理、逐段缓存和校验、失败原因／识别转录留存。
- FLAC／WAV／MP3 章节及完整 M4B，保留 EPUB 元信息、封面和章节顺序。

0.2.3 增加 **“御姐旁白 · 稍快”**，并设为新任务默认：Vivian 声线、成熟自信的表达指令、1.15 倍输出语速、音高 -1 半音。浏览器首次更新只迁移这组声音设置，其他已保存选项保留；已有任务继续使用自己的快照。这是在 CustomVoice 内调节风格，不能保证任意设计新的声线，长篇生成前建议先试听。FFmpeg 缺少 `rubberband` 时将音高设为 0。

已经停止的界面任务和旧命令行记录均可 **删除记录／恢复记录**；原文、语音缓存、输出和日志保留，勾选 **“显示已删除记录”** 可以恢复。原窗口仍有转换进程时不能删除其显示记录。尚未重启的旧服务可先使用浏览器内的可恢复记录，重启后自动同步到新接口。验证范围与限制见[交接说明](docs/handoff.md)。

界面开放预设音色，检测到另行下载的 **Base** 权重后才启用参考音频克隆入口；克隆需提供录音及准确的参考文本，本次环境尚未验证其实际音频效果。**VoiceDesign** 是另一套权重，不能把 CustomVoice 当作任意音色设计模型。本版没有自动多角色分配或大模型改写正文。

## Windows 安装

需要 Python 3.12、NVIDIA CUDA 显卡、可在 PATH 中找到的 FFmpeg 和 FFprobe。独立音高调节需要 FFmpeg 编译包含 `rubberband`；只有语速调节时可退回 `atempo`。已经验证的环境是 RTX 5090 D 32 GB、PyTorch 2.8.0+cu128、BF16、SDPA，其他设备需要自行验证。

```powershell
git clone https://github.com/NeverLuck2001/local-novel-audiobook.git
cd local-novel-audiobook
pwsh -File scripts/setup.ps1 -DownloadModels
```

脚本建立 TTS／界面环境 `.venv` 和独立 ASR 环境 `.venv-asr`。不要混装：当前固定的 qwen-tts 0.1.1 要求 Transformers 4.57.3，qwen-asr 0.0.6 要求 4.57.6。

不立即下载时省略 `-DownloadModels`，稍后执行：

```powershell
.\.venv\Scripts\python.exe scripts/download_models.py preset asr --provider modelscope
.\.venv\Scripts\python.exe audiobook.py --doctor
```

也可以选择 `--provider huggingface`。下载器只使用官方模型仓库，记录模型身份。依赖快照见 `requirements-lock-*-windows.txt`；优先按安装脚本部署。Linux／WSL 可用 `bash scripts/setup-wsl.sh`，另装 FFmpeg，并在本地配置中将 ASR 路径改为 `.venv-asr-linux/bin/python`。当前重点验证的是 Windows 路径。

## 打开界面

```powershell
.\.venv\Scripts\python.exe ui.py
```

打开启动器显示的本机地址，选择小说，查看清洗和章节预览，选择音色并调节朗读方式，最后加入队列。先用短文本试听更容易发现语气或读音不合适的问题。

音色、语气和合成参数变化会生成新语音。预设音色模式下，新任务可导入历史界面任务中输入及合成身份匹配的成功语音；仅改输出语速、音高、停顿、响度或编码时可直接复用，仍检查文件与当前核对策略，每次任务保留独立记录与输出。克隆模式目前仅支持同一任务续跑复用，不做跨任务导入。

音高是后处理微调，不等于模型训练或任意改变说话人身份。语气指令属于模型条件，不能保证精确执行每项情绪要求。

服务器仅监听本机回环地址，不提供远程登录／公网部署模式。只能控制界面自己启动的任务；已在其他终端启动的任务只读展示。暂停要等安全的分段或批次边界，合成和编码过程中可能需要等待。关闭界面服务器不会强杀转换进程，重启后按保存的进程身份重新关联。

点击任务中的**取消任务**，排队／暂停任务会立即退出队列；运行任务先显示“正在取消”，等当前批次或编码安全结束后停止。取消不会删除原文、语音缓存和已导出的音频，重启界面也不会自动继续；需要时点击“重新排队”，成功语音继续复用，未完成／失败段重新尝试。**取消全部排队任务**只取消等待任务，当前运行任务不受影响。**下一个运行**调整等待顺序，排队位置会跨重启保留。已结束任务可以**隐藏记录**，勾选**显示隐藏记录**后可找回；已完成任务用隐藏代替取消。

也可以在终端控制已经打开的界面，先列出任务 ID：

```powershell
python ui.py --list-jobs
python ui.py --cancel-job <job-id>
python ui.py --cancel-queued
```

非默认端口加 `--port`。这些命令通过本地服务操作，不直接修改后台正在使用的任务文件；界面服务必须先启动。

如果旧版本仍在运行，可从新版本目录复用旧目录的模型和环境：

```powershell
<existing-workspace>\.venv\Scripts\python.exe ui.py --workspace <existing-workspace>
```

新界面的上传、配置快照和输出写在新版本自己的 `work/ui`、`output/ui`，旧目录进度只读，新 GPU 任务排队等待旧转换结束。旧任务运行期间不要执行安装脚本或升级它使用的依赖。

## 命令行与恢复

```powershell
python main.py books/ --dry-run
python main.py books/ --recursive
python main.py books/ --status
python main.py books/ --retry-failed
python main.py examples/sample.epub --chapters 1 --voice Serena --batch-size 4
```

原命令再次运行即可恢复；`--config config.local.yaml` 可指定本地配置。`--batch-size 1` 使用逐段独立采样；批次可设 1–16，但较大值没有证明是最优，建议先从 2–4 开始。官方批量接口共享随机数流，分组不同可能读法不同，已完成缓存仍可复用。

`--max-segments N` 在新成功段达到数量后暂停。`--stop-file <path>` 在该文件存在时安全暂停，恢复前删掉该文件。`--chapters 1,3-5` 选择原始章节编号；先预览实际编号。选择章节时导出的是所选章节合集。

## 清洗与发音

清洗默认关闭，处理自己的书之前应预览。正文前清理要求找到第一个章节，删除其前所有内容，可能删除真实序言。自定义规则会删除整条匹配行。发音表按字面替换，也可能命中较长词语的一部分，建议使用准确词组。

```yaml
text:
  remove_urls: true
  strip_front_matter: false
  remove_line_patterns: ['^Downloaded from.*$']
  pronunciation_map:
    重庆: 崇庆
output:
  speech_rate: 1.1
  pitch_semitones: -1.0
```

这里的发音替换仅演示如何用近音字提示模型，不代表通用的读音纠正方案。清洗报告保留删除内容和替换数量；送入模型的文字与原文同时保留。ASR 也会误识别人名、多音字和数字，失败报告中的识别文字、差异和尝试历史可以帮助区分合成错误与核对误判。

## 文件位置

- `work/<book-id>/`：清洗正文、审计、SQLite 状态、逐段原始语音缓存。
- `output/<book-id>/`：章节音频、M4B、manifest 和失败报告。
- `work/ui/`：界面的私有上传、任务记录和独立配置快照。
- `output/ui/`：界面生成的音频。
- `models/`：单独下载的权重；`examples/`：项目自写示例。

缓存检查 SHA256；损坏音频不直接复用。合成、核对、导出分别计算身份，调节响度无需重做 TTS。某段失败不会把缺段文件标成完整全书，其他段和其他书可继续。保留 `work/` 才能复用分段状态。

## 性能与验证

同一组四段自写短文本在 RTX 5090 D 上，逐段合成 111.48 秒，四段批量 34.80 秒，原文吞吐量约提高 3.20 倍；批次 1／2／4 的十二段输出均通过原有质量阈值。另一个 750 字生产管线样本合成 202.16 秒原始语音耗时 138.64 秒，RTF 0.686，四段均通过 ASR。加载模型、核对、编码另需时间，测试时显卡还有其他任务。不能将它当作整书压测或固定速度承诺。[详细方法和限制](docs/performance.md)。

RTF = 合成秒数 ÷ 音频秒数，越小越快；预计剩余时间需有已完成数据后才有参考意义。当前使用单模型批量计算；增加 Python 线程不会让一张显卡容量成倍增长。本版没有安装 FlashAttention，没有接入 vLLM-Omni 或多显卡。

运行现有回归检查：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src scripts tests audiobook.py main.py ui.py
```

## 公开文件与许可证

`.gitignore` 排除小说、参考录音、模型、输出、工作缓存、日志、环境、本地配置和机器报告；公开仓库只保留源码、项目自写示例、原有回归检查、默认配置与文档。不要把私人小说或音色参考录音加入 Git。

项目代码采用 **AGPL-3.0-or-later**，见 [LICENSE](LICENSE)。EbookLib 0.20 为 AGPL-3.0，官方 Qwen3-TTS／ASR 代码和模型有各自的 Apache-2.0 许可证。权重和 FFmpeg 单独安装，不在仓库分发。原始来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
