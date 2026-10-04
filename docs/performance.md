# 本机批量推理优化

2026-10-04 使用建议：32GB 显存、BF16、当前 200 字目标／300 字上限的设置，可从 8 段开始比较，16 段先用于短章节。默认仍保留已经实际验证的 4 段；这里的 8／16 建议是基于现有实现与显存余量的工程判断，并非新增长批实测。更长的输出、ASR 常驻显存、其他 GPU 应用和同批长短差异都会影响速度及峰值显存。批量不是音质档位，但会改变随机采样结果；保持同样的 QC 标准，并比较重试次数。显存不足等批量异常会自动减小批次。

2026-10-01，Windows / RTX 5090 D 32 GB / PyTorch 2.8.0+cu128，官方 Qwen3-TTS-12Hz-1.7B-CustomVoice，Serena，BF16 / SDPA。保持分段、语气指令、采样参数和 ASR 阈值不变。

## 已接入的方案

使用官方 `generate_custom_voice(text=[...])` 列表接口，在一个 GPU 模型实例中批量推理。当前配置上限四段，同章内选择长度接近的待生成段，减少长短差异造成的等待。输出逐段保留，QC/重试独立，拼接顺序仍由正文决定。

批量过程的所有成员先以 running 保存，音频合成后以 generated + SHA256 暂存；只有逐段核对通过后才能成为 completed。中断后复用已生成且 hash 正确的待核对音频。批量推理异常会拆分，最终可回到串行。相同文字重复出现时批次按缓存 ID 去重。批次大小不改变已完成音频的身份。

Qwen 官方批量接口共用随机数流，因此不同批次大小/分组会改变采样朗读。记录保存每段请求 seed 和实际 batch_seed、batch_id、batch_size、batch_seconds。严格独立段 seed 的模式仍为 batch_size=1。不声称批量与串行逐字节一致。

## 四段短样本实测

原文包含旁白、对白、日期和数字；三种设置用同一组四段原文。

| 每批上限 | 合成时间 | 输出音频总长 | 聚合 RTF | TTS 进程峰值已分配显存 | 通过 QC |
|---|---:|---:|---:|---:|---:|
| 1 | 111.48 秒 | 45.36 秒 | 2.458 | 4.24 GiB | 4 / 4 |
| 2 | 63.72 秒 | 51.60 秒 | 1.235 | 4.74 GiB | 4 / 4 |
| 4 | 34.80 秒 | 53.20 秒 | 0.654 | 5.52 GiB | 4 / 4 |

四段批量处理相同原文所需时间约缩短为 31.2%，约 3.20 倍原文吞吐量。聚合 RTF = 批次合成时间 / 所有输出音频时长之和，越低越快；它不是单段等待时间。批量和串行采样的音频总长不同，因此不把 RTF 比值冒充严格同音频加速倍数。三种设置最高 ASR CER 都为 4.88%，没有放宽 15% 阈值。

加载模型、ASR、音频写盘和 FFmpeg 后处理不计入合成时间，wall_seconds 单独记录。批次时间在每段 generation_seconds 中平均分摊，求和等于该批实际合成时间；batch_seconds 是整批等待时间。

测试期间用户的小说任务正在同一显卡上生成，没有中断该任务。显卡占用快照已随报告记录，不能把这些数值当作空闲显卡极限、长任务持续吞吐量或最终有声书生成总时间。显存是本测试进程的 PyTorch 已分配峰值，不含其他进程、桌面、驱动或本地 ASR。

原始机器报告和音频仅保留在本地，未随公开仓库上传；测试文字来自项目自写示例。

## 接近正文长度的样本

`examples/optimization-sample.txt` 是自写小说正文，共 750 字符，按现有分段器得到四段。四段批量合成 197.84 秒音频耗时 128.05 秒，聚合 RTF 0.647，TTS 进程峰值 6.90 GiB。全部四段通过原有 QC，最高 CER 为 3.68%。另一次相同批次复测耗时 130.36 秒，音频总长和最高 CER 相同。

第二次复测将配置上限设为八，但输入只有四段，实际仍是四段批次。该记录用于重复运行的一致性观察，不证明八段批量的速度、显存或质量。报告显式保留配置上限和从每段指标得到的真实最大批次。原始机器报告仅保留在本地。

上述两组样本均不是整本小说持续压测，也没有人工长时间听感评分。

## 完整生产管线验收

独立配置使用相同官方模型、Serena、原有分段、四段批次和 ASR 阈值，对上述正文运行真正的生产 Pipeline，而非仅调用模型接口。四段均首次通过 QC；ASR 最高 CER 3.68%，四段的插入/删除计数均为零。批量合成 202.16 秒音频耗时 138.64 秒，聚合 RTF 0.686，TTS 峰值 6.90 GiB。

成功输出章节 FLAC 和约 204.29 秒、带一条章节标记的 M4B。Pipeline 自身 elapsed_this_run 为 211.84 秒，包含 TTS 加载、合成、核对和编码，但不包含此前的模型身份检查/ASR worker 预启动，不把它称为完整命令的总耗时。完整机器报告保留在本地。

再以 `--batch-size 1` 运行同一输入/配置，生成零段、缓存四段；四个原始音频和完整 M4B 的 SHA256、修改时间均保持不变，旧导出未被归档。这验证了切换批次策略后的实际缓存兼容性。

## 调研与取舍

- [Qwen 官方模型接口](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py) 和[官方模型说明](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)：已支持列表批量输入。此方案直接复用本机安装的官方权重/包，没有新模型或第三方推理 fork。
- [vLLM-Omni 的 TTS 批量/CUDA Graph 文档](https://docs.vllm.ai/projects/vllm-omni/en/latest/user_guide/examples/offline_inference/text_to_speech/)：有进一步优化空间，但[官方安装条件](https://docs.vllm.ai/projects/vllm-omni/en/latest/getting_started/installation/gpu/)目前不原生支持 Windows，需要单独 Linux/WSL 环境。没有在正在工作的 Windows 环境中换装其依赖，也没有把其他 GPU 的宣传数据当成本机结果。
- [FlashAttention 官方项目](https://github.com/Dao-AILab/flash-attention)：减少注意力计算访存，Windows 编译仍需更多测试。现有 SDPA 已可使用 PyTorch 提供的优化内核。本轮主要收益来自批量推理，未安装 FlashAttention，也不把核对失败归因于缺少它。
- 多个 Python 线程同时操作一个 TTS 模型会共享随机数和生成状态；多个模型进程会重复占用显存。当前单 GPU 使用一个模型实例做批量计算，保持串行 QC 和 SQLite 写入。

## 使用与复测

原命令 `python main.py books/` 下次启动自动使用四段上限。`--batch-size 1` 回到逐段模式；允许显式设置 1–16，但更大值尚未做本机质量/显存验收，不建议直接把 16 当作最优值。

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_batch.py
.\.venv\Scripts\python.exe scripts/benchmark_batch.py --sizes 1 4 --text-file examples/optimization-sample.txt --report docs/benchmark-custom.json
```

批次输入不足时实际批次可能小于配置上限，真实大小在每个 clip 的 batch_size 中。小型尾段仍走串行接口。
