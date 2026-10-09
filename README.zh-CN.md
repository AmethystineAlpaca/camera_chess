# Camera Chess — 用实体棋盘与 Stockfish 对弈

**V1 · Apple Silicon macOS** · [English](README.md) | 简体中文 | [Русский](README.ru.md)

普通国际象棋棋盘、摄像头和一台 Mac，就能与电脑下棋。Camera Chess 使用 **ChessQueries / ChessQ Lite** 在本地识别棋子位置，同步数字棋盘，再由 **Stockfish** 计算并语音播报下一步。无需电子棋盘或棋子传感器。

![摄像头识别实体国际象棋棋盘，与 Stockfish 进行人机对弈](docs/media/demo-v1.gif)

[屏幕录像 · 55 秒](docs/media/demo-v1.mp4) · [手机竖屏演示 · 29 秒](docs/media/demo-v1-portrait.mp4) · [GIF](docs/media/demo-v1.gif)

公开视频已移除音轨和原始录制元数据；应用本身支持英语语音播报。

## 主要功能

- **实体棋盘对弈：** 摄像头识别棋盘，通过置信度和连续帧确认减少不稳定识别的影响。
- **Stockfish 对手：** 四档难度、走棋箭头和英语语音提示。电脑的棋子由你手动移动。
- **本地 AI：** 棋盘识别和棋力引擎均在 Mac 上运行，无需云端推理账号。
- **保存与恢复：** 支持保存、载入棋局、纠正识别错误和设置残局。
- **桌面入口：** 点击悬浮棋子进入游戏；返回主页停止摄像头；退出应用关闭后台组件。

## 快速开始

需要 Apple Silicon Mac、Python 3.12、Xcode Command Line Tools 和 Stockfish。目前提供源码安装，尚无签名安装包。

```bash
git clone https://github.com/AmethystineAlpaca/camera_chess.git
cd camera_chess
# 如未安装依赖：brew install python@3.12 stockfish
# 如未安装 Apple 命令行工具：xcode-select --install
./setup.sh
./start.sh
```

首次安装需联网下载依赖和模型，之后识别和对弈在本地运行。点击悬浮棋子，选择 **Start a New Game**，允许摄像头访问，并让全部 64 格出现在画面中。靠近摄像头的一方由电脑执棋。按提示移动电脑棋子，再移开手等待确认。

**Resume** 恢复当前服务中的棋局；**Save / Load** 可跨重启保存棋局；**Endgame** 用于设置残局并确认执棋颜色和轮到哪方走。设置中可调整难度和语音。

## V1 实测表现

开发用 Apple Silicon Mac 上的 426 帧记录：

| 指标 | 中位数 | P95 |
| --- | ---: | ---: |
| 单帧识别处理耗时 | 387 ms | 449 ms |
| 采集到识别结果的延迟 | 487 ms | 577 ms |

这是预热后的逐帧统计，不是启动耗时，也不是完整的走棋到语音响应延迟；稳定局面确认还需要等待。两张保留的参考棋盘图像均与预期位置一致（2/2），样本量不足以代表总体准确率。光照、拍摄角度和遮挡仍会影响识别。

V1 开发环境通过了 79 项 Python 测试、网页声音与生命周期检查，以及实际模型参考图和朝向检查。

## 接下来

- 改善首次使用、摄像头设置、声音反馈及权限提示。
- 增加明确的兵升变选择，包括非后升变。
- 改善残局设置、行棋方确认和局面恢复。
- 扩充不同棋盘、光照和角度的识别评测。
- 减少稳定确认等待，并测量完整交互延迟。
- 提供经过签名和公证的 Mac 安装包。

## 反馈与致谢

[项目主页](https://github.com/AmethystineAlpaca/camera_chess) · [反馈与问题](https://github.com/AmethystineAlpaca/camera_chess/issues/new/choose) · [参与开发](CONTRIBUTING.md)

棋盘识别来自 [ChessQueries](https://github.com/JSeytre/chessqueries) / [ChessQ Lite](https://huggingface.co/joelseytre/chessq-lite)，使用 ViT-S/14 V4 FP16 权重，遵循 PolyForm Noncommercial 1.0.0；棋力引擎为 [Stockfish](https://stockfishchess.org/)，遵循 GPLv3。第三方许可分别适用。
