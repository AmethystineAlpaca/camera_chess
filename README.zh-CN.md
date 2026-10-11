# Camera Chess — 在真实棋盘上，与本地 AI 畅快对弈！

**识别快，棋面准，有语音。全部在你的 Mac 上运行。**

**Apple Silicon macOS** · [English](README.md) | 简体中文 | [Русский](README.ru.md)

一副普通国际象棋棋盘、一个摄像头和一台 Mac，就能开启本地 AI 对弈。移动真实棋子，数字棋盘自动跟随，再听 AI 播报下一步。Camera Chess 将 **ChessQ Lite V4**、**本项目自行训练的后处理模型**与**自动棋盘透视校正**结合，实现准确、快速的棋盘识别。对弈 AI 由 **Stockfish** 提供。

- **识别快、准确率高：** 摄像头识别棋子，自行训练的 CRF / 神经网络后处理结合连续帧检查，让实体棋盘与数字棋盘保持同步。
- **斜着拍，也能校正棋盘：** 自动定位、跟踪棋盘，校正画面透视后再送入识别模型。
- **会说话的 AI 对手：** 四档难度、清晰的走棋箭头和英语语音提示，让注意力留在真实棋盘上。
- **想怎么下，就怎么开始：** 新开一局、摆出残局、在残局设置中选择执棋颜色，或保存和加载局面，随时接着下。
- **没有备用棋子，也能升变：** 支持升后、车、象、马，可以保留原兵作为替身继续走棋。
- **本地运行：** 识别模型和棋力引擎都在 Mac 上运行，无需电子棋盘、棋子传感器或云端推理账号。

![保留实体兵作为后的替身移动，数字棋盘同步并显示 AI 应手](docs/media/progress-2026-10-11-after-promotion.gif)

*升变后继续下：实体兵充当后，数字棋盘跟随走棋。* 原视频：**00:37.0–00:47.5**，原速。

[观看完整演示 · 73 秒 · 1.23 MB](docs/media/progress-2026-10-11.mp4) · [快速开始](#快速开始) · [识别与速度](#识别与速度)

## 摆好残局，直接开下

摆出想练习的局面，打开 **Endgame**，选择执棋颜色，让摄像头扫描。确认棋面和行棋方后，就能与 AI 对弈。

![进入残局、扫描倒计时并确认开局](docs/media/progress-2026-10-11-endgame.gif)

原视频：**00:00.8–00:01.8 + 00:07.8–00:21.8**。首页保留 1 秒，跳过选色停顿；扫描倒计时与确认过程保持原速。

## 升变，接着走

选择升变后的棋子，保留实体兵作为替身，或换上对应棋子，再确认。数字棋盘上的 **P** 标记替身，它会按升变后棋子的规则移动。

![移动兵，选择 Queen，并以 Keep pawn 方式确认升变](docs/media/progress-2026-10-11-promotion.gif)

原视频：**00:21.8–00:37.0**，原速。本段演示使用 **Queen / Keep pawn** 并人工确认。确认前请核对显示的棋面，有误时可用 **Fix recognition** 修正。

演示导出文件无声，应用本身支持语音播报。GIF 在完整录屏后段的等待前结束。[片段与导出说明](docs/media/README.md#october-11-2026-progress-demo) · [早期对弈演示](docs/media/demo-v1.mp4) · [手机竖屏演示](docs/media/demo-v1-portrait.mp4)。

## 快速开始

需要 **Apple Silicon Mac**、**Python 3.12**、**Xcode Command Line Tools** 和 **Stockfish**。目前提供源码安装。

```bash
git clone https://github.com/AmethystineAlpaca/camera_chess.git
cd camera_chess
# 如未安装依赖：brew install python@3.12 stockfish
# 如未安装 Apple 命令行工具：xcode-select --install
./setup.sh
./start.sh
```

首次安装下载依赖和模型，之后识别与对弈都在本地运行。点击悬浮棋子，允许摄像头访问，并让全部 64 格出现在画面中。双方的实体棋子均由你移动，跟随 AI 的箭头和语音提示即可。

| 入口 | 可以做什么 |
| --- | --- |
| **Start a New Game** | 从标准开局开始，AI 执靠近摄像头一侧的颜色。 |
| **Endgame** | 扫描自定义残局，选择执棋颜色并确认行棋方。 |
| **Save / Load** | 保存命名局面，重启后继续加载，保留双方颜色。 |
| **Resume** | 在服务运行期间继续当前棋局。 |
| **Settings** | 选择四档难度，调整语音，切换识别模型。 |
| **Fix recognition** | 批量修正棋子或空格，查看前后对照，恢复误判的走棋。 |
| **Back to Home / Quit Application** | 返回主页并停止摄像头，或退出并关闭后台组件。 |

## 识别与速度

**ChessQ Lite V4 + 自行训练的模型 + 透视校正。** 图像模型提供棋子概率，单独训练的后处理模型结合上一局面消解不确定的识别。八种后处理选项包含 **CRF、MLP、CNN 和 Attention**，默认使用两层 changed-square MLP。透视校正自动定位、跟踪并调整棋盘画面；无法可靠校正时使用原图。设置中可以并排查看原始画面与模型输入。

连续帧检查和棋规共同决定何时接受局面。ChessQ Lite 的权重保持不变，额外的后处理模型由本项目训练。[模型评测](docs/crf-real-photo-synthetic-previous-evaluation.md) · [透视校正](docs/perspective-experiment.md) · [训练数据](docs/crf-data.md)。

**快速处理画面：** 早期 V1 在开发用 Mac 上记录了 426 个预热后的帧，单帧处理耗时中位数为 **387 ms**，采集到识别结果的延迟中位数为 **487 ms**。该测量早于 CRF 接入；启动、稳定局面确认和完整的走棋到语音响应还需要额外时间。

**准确率：** 最新实机对弈的识别体验有明显提升。独立的 176 张照片评估使用构造的前态，默认 MLP 的整盘准确率为 **72.73%**，v1 CRF 为 **71.02%**。两者的增量尚未达到统计显著；连续视频准确率与透视校正的独立收益尚未量化。光照、拍摄角度和遮挡仍会影响表现。[评测范围与结果](docs/crf-real-photo-synthetic-previous-evaluation.md#neural-postprocessor-comparison--2026-10-11)。

## 最近更新 · 2026 年 10 月 11 日

- 支持八种后处理模型切换，包括自行训练的 CRF 与 Attention 模型。
- 加入自动透视校正，展示原始画面与模型输入对照。
- 完成实体棋盘上的残局开局、升变和兵替身后续移动演示。
- 改善识别纠错、存档恢复与语音提示。
- 加入可关闭的本地改进数据记录，以及日志文件夹入口。

[技术实验记录](docs/crf-experiment-log.md) · [升变实现](docs/promotion-physical-tracking.md)。不同棋盘、非后升变和实体棋子替换的扩展实测仍在继续。

## 数据留在本机

相机画面、存档、纠错记录和改进日志保存在本机，不纳入 Git。设置中的 **Log Data for potential improvements** 默认开启，可关闭以停止新增归档；**Open Log Folder** 打开已有档案。关闭记录不会删除旧文件，也不会自动上传数据。[存储与隐私控制](docs/local-improvement-data.md)。

## 反馈与致谢

[反馈与问题](https://github.com/AmethystineAlpaca/camera_chess/issues/new/choose) · [参与开发](CONTRIBUTING.md)

V1 支持 Apple Silicon macOS 上的本地对弈，目前尚无签名安装包、其他平台支持或在线对局集成。

棋盘识别采用 [ChessQueries / ChessQ Lite](https://github.com/JSeytre/chessqueries) 的 ViT-S/14 V4 FP16 模型，遵循 **PolyForm Noncommercial 1.0.0**；对弈 AI 使用 [Stockfish](https://stockfishchess.org/)，遵循 **GPLv3**。第三方许可分别适用。
