# AI 应用开发实习生测试提交说明

## 作业链接

**代码、结果与说明：<https://github.com/3080295198/aios-shot-segmenter>**

仓库内同时提供 `shot-segmenter/`、`shot-results/` 和完整 ZIP。ZIP 也可以在仓库根目录直接下载。

提交包内包含：

- `shot-segmenter/`：可运行代码、依赖、测试和说明
- `shot-results/`：3 条视频的镜头时间、预览图、切点对比图、JSON、CSV 和 HTML 报告

压缩包不包含原始招聘测试视频。上传到 GitHub Releases、Gitee 附件或可公开访问的网盘后，将分享链接填入本节，并确认招聘方无需登录即可访问。

## 实现结果

一次运行批量处理了 3 条视频，共得到 148 个镜头：

| 视频 | 时长 | 成功切出的镜头数 | 自动验证 |
| --- | ---: | ---: | --- |
| sample-01.mp4 | 160.33s | 46 | 7/7 通过 |
| sample-02.mp4 | 137.93s | 65 | 7/7 通过 |
| sample-03.mp4 | 157.73s | 37 | 7/7 通过 |

每条视频均输出镜头起止时间、实际中点预览图、切点前后对比图和镜头级 JSON/CSV。HTML 报告可直接浏览全部结果。

## 运行方法

环境要求：Python 3.10+、FFmpeg（`ffprobe` 可在命令行调用）。

```powershell
cd shot-segmenter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python segment_videos.py .\input-videos --output ..\shot-results --overwrite
```

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python segment_videos.py /path/to/videos --output ./shot-results
```

运行完成后打开 `shot-results/report.html`。核心结果位于：

- `shot-results/shots.csv`：所有镜头汇总
- `shot-results/shots.json`：结构化结果
- `shot-results/manifest.json`：运行摘要
- `shot-results/failures.json`：失败记录
- `shot-results/sample-XX/previews/`：每个镜头的预览图
- `shot-results/sample-XX/boundaries/`：每个切点的前后画面

## 输出示例

以 `sample-03.mp4` 为例：

| 镜头 | 起止时间 | 时长 | 预览图 |
| ---: | --- | ---: | --- |
| 1 | 0.00–1.47s | 1.47s | `sample-03/previews/shot_0001.jpg` |
| 2 | 1.47–3.47s | 2.00s | `sample-03/previews/shot_0002.jpg` |
| 3 | 3.47–5.07s | 1.60s | `sample-03/previews/shot_0003.jpg` |
| 4 | 5.07–6.47s | 1.40s | `sample-03/previews/shot_0004.jpg` |
| ... | ... | ... | ... |
| 23 | 63.93–78.60s | 14.67s | `sample-03/previews/shot_0023.jpg` |
| 24 | 78.60–102.40s | 23.80s | `sample-03/previews/shot_0024.jpg` |
| 31 | 125.07–126.80s | 1.73s | `sample-03/previews/shot_0031.jpg` |
| 37 | 149.07–157.73s | 8.67s | `sample-03/previews/shot_0037.jpg` |

完整 37 条时间记录见 `shot-results/sample-03/shots.csv`，全镜头总览见 `shot-results/sample-03/contact-sheet.jpg`。

## 判断依据与主要取舍

每个分析帧提取 HSV 颜色直方图、感知哈希和低分辨率灰度像素差。颜色变化、结构变化和像素变化综合形成切点分数。程序同时使用固定阈值和局部自适应阈值，再合并极近的重复检测并抑制瞬时闪白。

- 不使用大模型：本任务主要是视觉突变，轻量方法速度快、结果可解释。
- 不使用音频：一条素材没有音轨，且音乐重拍不代表画面切镜。
- 不重新导出视频：只输出时间轴和预览图，避免额外转码和空间消耗。
- 低置信度结果不隐藏：边界分数低、使用自适应规则或镜头很短时标记 `needs_review=true`。

更完整说明见 `README.md`。

## 异常处理

- 每条视频独立处理，并在独立临时目录中生成结果。
- 文件损坏、无视频流、帧率非法、解码失败或结果校验失败时，记录文件名、错误类型、原因和简短堆栈。
- 单条失败后继续处理后续视频；有失败任务时命令退出码为 1，但成功结果仍保留。
- 实际用损坏 MP4 测试，记录 `moov atom not found`；排在它后面的正常视频继续处理并输出 46 个镜头。

## 验证结果

自动化验证共 7 项：时间轴非空、从 0 秒开始、结束于视频末尾、相邻镜头连续、时长为正、预览图可读、切点证据完整。3 条视频全部通过，148 张预览图和 145 张切点图可读取；HTML 共 296 个本地资源链接，缺失数为 0；核心单元测试 4/4 通过。

同时使用 FFmpeg 内置场景分数作为独立对照。FFmpeg 阈值 0.20 在三条视频中分别得到 44、75、29 个候选事件，本程序合并后得到 45、64、36 个边界。数量和主要时间位置基本一致，少量差异来自重复切点合并和低对比切点自适应保留。

发现的问题：

- `sample-03` 有 18 个低置信度镜头需要人工复核，主要集中在 0.14–0.20 的边界分数区间。
- `sample-03` 第 24 个镜头长 23.80 秒，可能包含未被检测到的缓慢转场，应优先复核。
- 快速摇镜、主体突然占满画面或强闪光仍可能产生假边界。
- 当前时间轴基于恒定帧率；已有素材均为 15 fps CFR，VFR 素材需要改用显示时间戳。

详细记录见 `VALIDATION.md`。

## AI 开发规则

本次没有冒用既往团队规则，以下三条是结合本次作业整理，并收录在 `AGENTS.example.md`：

1. **先运行真实输入，再判断完成。** 解决了只通过编译但实际输出存在一帧空隙、HTML 图片路径失效的问题。
2. **按单条视频隔离失败。** 解决一个损坏文件导致整批任务无结果的问题，并完成真实故障测试。
3. **自动结果必须保留可审计证据。** 保存分数、判断方式、置信度、预览图和切点前后图，便于人工复核和后续调参。

完整说明见 `AI_DEVELOPMENT_RULES.md`。

