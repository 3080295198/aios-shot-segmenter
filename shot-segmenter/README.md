# AIOS 视频镜头切分

一个可直接批量运行的镜头切分程序。程序读取视频目录或单个视频，输出每个镜头的起止时间、预览图、切点前后对比图、JSON/CSV 数据以及可视化 HTML 报告。

本次已用 3 条素材完成实际运行：3 条全部成功，共 148 个镜头，详细结果见 [HTML 报告](../shot-results/report.html) 和 [全局 CSV](../shot-results/shots.csv)。

## 快速运行

环境要求：

- Python 3.10 或更高版本
- FFmpeg（需要命令行中能调用 `ffprobe`）
- Python 包：`numpy`、`opencv-python-headless`

Windows PowerShell：

```powershell
cd .\shot-segmenter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python segment_videos.py .\input-videos --output ..\shot-results --overwrite
```

也可以使用封装脚本：

```powershell
.\run.ps1 -InputPath .\input-videos -OutputPath ..\shot-results -Overwrite
```

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python segment_videos.py /path/to/videos --output ./shot-results
```

一次运行即可批处理目录中的所有受支持视频。输入也可以是一个视频文件。支持 `.mp4`、`.mov`、`.mkv`、`.avi`、`.webm`、`.m4v`、`.mpeg` 和 `.mpg`。

## 输出

```text
shot-results/
  manifest.json            本次运行摘要与逐视频状态
  failures.json            失败任务及原因；本次为空数组
  shots.json               所有成功视频的镜头汇总
  shots.csv                可直接用于表格或后续流水线
  report.html              可直接打开的预览报告
  sample-01/
    shots.json             单视频完整元数据、参数、分数和验证结果
    shots.csv              单视频镜头表
    contact-sheet.jpg      全镜头总览图
    previews/              每个镜头一张 JPEG
    boundaries/            每个切点前后的并排对比图
  sample-02/
  sample-03/
```

时间采用秒和帧号双重定位。镜头时间区间为左闭右开 `[start, end)`，相邻镜头连续；帧号 `start_frame` 到 `end_frame` 为闭区间，便于按帧截取。

## 切分依据

每秒最多分析 15 帧（保留原视频帧率），每帧缩放到 180 像素宽，并提取三类低层视觉特征：

1. HSV 颜色直方图，权重 0.60，用于识别场景颜色和光照结构变化。
2. 64 位感知哈希，权重 0.20，用于识别画面整体结构变化。
3. 低分辨率灰度像素差，权重 0.20，用于补足同色调但构图不同场景的差异。

有三个筛选步骤：

- 固定阈值：综合分数达到 `0.20` 时视为明显切点。
- 自适应阈值：低对比切点若超过局部窗口中位数、MAD 阈值和 `0.14` 最低分数，也可被保留。
- 时间合并与闪切抑制：0.18 秒内的重复检测合并为一个；若短事件前后的画面高度相似，则判为闪白/瞬时异常并移除。

视频开头和结尾 0.20 秒内的边界默认忽略，避免片头淡入被拆成一个没有业务意义的极短镜头。每段仍取实际中点帧作为预览。

## 异常处理

每个视频在独立临时目录中处理，单条视频的异常不会终止整批任务：

- 文件不存在、损坏、无视频流、帧率非法、无法解码、结果校验失败都会写入 `failures.json`。
- `ffprobe` 有 30 秒超时，避免异常输入永久阻塞。
- 结果先写入 `.partial-*` 临时目录，全部图片和校验通过后才移动到正式目录。
- 遇到重复文件名自动分配唯一 `video_id`。
- 有失败任务时命令退出码为 `1`；成功视频和报告仍会保留。

已用故意损坏的 MP4 做过实测：失败视频记录为 `moov atom not found`，其后正常视频继续完成并输出 46 个镜头。测试输入仅为临时文件，不在交付结果中。

## 验证

每次处理自动执行 7 项检查：

- 时间轴非空
- 从 0 秒开始
- 结束于视频末尾
- 相邻镜头无空隙或重叠
- 所有镜头时长为正
- 所有预览图可读取
- 所有切点对比图完整

本次三条素材均通过 7/7 检查。详细数据、独立交叉检查和发现的问题见 [VALIDATION.md](VALIDATION.md)。

运行单元测试：

```powershell
python -m unittest discover -s tests -v
```

## 主要取舍

- 不引入大模型或深度网络：3 条视频的镜头边界主要是视觉突变，可解释的轻量方法速度更快，也便于查看失败原因。
- 不使用音频：`sample-03.mp4` 没有音轨，且背景音乐变化不一定等于镜头变化；纯视觉规则可以统一处理三条素材。
- 不导出新视频：题目只要求镜头时间与预览图，保留原视频可减少空间和转码损失。
- 采用双阈值：单一固定阈值会漏掉低对比切点，纯自适应阈值又容易受运动影响；两种规则结合，并用置信度标记边界。
- 不把低置信度结果当作确定真值：`needs_review=true` 的镜头需要结合切点对比图复核。

## 已知限制

- 快速摇镜、主体突然占据画面或强闪光可能与切镜产生相似分数。
- 很慢的淡入淡出没有唯一单帧切点，当前会选变化最明显的帧作为边界。
- 时间轴以恒定帧率为基础。已有素材均为 15 fps CFR；VFR 素材需要按显示时间戳进一步重构时间轴。
- 阈值按短视频和当前素材分布校准。换内容品类后应先抽样复核，不能直接假设阈值永久最优。

