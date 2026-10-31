# 提交检查清单

## 提交材料

- 可运行代码：[segment_videos.py](segment_videos.py)
- 依赖：[requirements.txt](requirements.txt)
- 运行方法与设计取舍：[README.md](README.md)
- 实际运行结果：[shot-results](../shot-results/)
- 验证记录：[VALIDATION.md](VALIDATION.md)
- AI 开发规则：[AI_DEVELOPMENT_RULES.md](AI_DEVELOPMENT_RULES.md)
- 脱敏规则示例：[AGENTS.example.md](AGENTS.example.md)
- 可直接修改提交的正文：[SUBMISSION.md](SUBMISSION.md)
- 可运行压缩包：`outputs/aios-shot-segmenter.zip`（上传后使用平台生成的公开链接）

## 已确认

- 3 条素材均已批量处理，共 148 个镜头。
- 每条镜头均有起止时间、预览图，并额外提供切点前后对比图。
- 3 条视频的时间轴、预览和边界证据均通过程序校验。
- 损坏视频测试证明单条失败后批处理可以继续。
- HTML 报告中的本地图片和链接已验证不存在缺失。
- 单元测试 4/4 通过。
- 交付物不包含原始测试视频、招聘页面账号、密钥或客户信息。

## 需人工完成

- 在 [HTML 报告](../shot-results/report.html) 中抽查低置信度镜头，尤其是 `sample-03` 的 `needs_review` 项。
- 如果对某个边界有业务层面的不同定义，可调整 `--scene-threshold`、`--adaptive-floor` 或 `--merge-gap` 后重跑。
- 将代码、结果和说明上传到可访问的代码仓库，并按招聘页面要求填写链接。

