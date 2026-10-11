# v1.1.1 本地验收记录

日期：2026-10-11（北京时间）。基于远端 `main` 的 `40047eff70b004dd96bc56e24ec941b60a836f29`，完成两张湖景重绘、云水动效调整和 Easy Going 接入。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Python 后端完整回归 `python -m pytest -q` | **176 passed**，52.92 秒；一个既有 Starlette / httpx 弃用警告，无失败。 |
| 前端被试/iPad协议、前测提示、每日训练与反馈策略 | **54 passed**，运行下列五个既有 Node 测试文件。 |
| Edge 实际 WebGL 与 Web Audio 检查 | 通过两景可见变化、固定对象、暂停、减少动态效果、30/24 fps 调度及 Easy Going 解码和层次切换。 |
| Easy Going 运行文件完整性 | 三条 WAV 与 metadata 的 SHA-256 和本地准备文件一致；44.1 kHz、16bit、立体声，2,646,000 帧，均为 60 秒。 |
| 音频 PCM 检查 | 七种非空层组合无采样削波，已修补循环首尾连续；保留原相位和声部平衡。 |
| 独立代码审查 | 修复低配节流余量丢失导致约 15 fps 的问题；修复后实际浏览器验证正常 30 fps、低配 24 fps。 |
| 目录与分发检查 | JS / JSON 的六首曲目一致，运行图片和音轨均可被后端提供；源音轨、购买 ZIP、预览、真实数据和私有配置不在提交中。 |
| Git 空白检查 | `git diff --check` 通过。 |

第一次后端运行因验证环境缺少已声明的 `qrcode==8.2` 出现一项失败、175项通过；补齐依赖后完整复跑 176 项通过。项目 requirements 已含该依赖，无需修改依赖文件。

## 浏览器检查细节

`tests/check-scenes-audio.cjs` 使用静态隔离服务、只读模拟 API 和静音 Edge，不创建脑电会话。

- 两图在 8 秒间隔的天空检测区约 **61–72%** 像素发生可见变化，水面约 **89–91%**；这些是图像差异检测，不是脑电测量。
- 树、山、草岸、小屋、灌木与两侧芦苇的固定检测区差异均为 **0**；暂停后的整帧差异为 **0**。
- 减少动态效果启用后，动画时间保持 **0**。
- 30 fps 调用输入下，正常/低配一秒实际绘制 **30/24** 次；暂停期间 **0/0**，恢复后一秒 **30/24**。
- Easy Going 实际解码为三条同时循环的音源，循环周期 **60 秒**。40、55、70 分分别收敛到 `[1,0,0]`、`[1,1,0]`、`[1,1,1]`，切层不重启音源。

运行方式（浏览器检查需要额外安装 Playwright 和 Edge）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test-participant-ipad.cjs tests/test-calibration-cues.cjs tests/test-calibration-instructions.cjs tests/test-daily-ui.cjs tests/test-feedback-policy.cjs
node tests/check-scenes-audio.cjs
```

浏览器检查的截图与结果写入被忽略的 `runtime/browser-scenes-audio/`。测试环境为 Windows、Python 3.12 与 Chromium/Edge。运行项目本身不需要 Node 或 Playwright。

没有新做 Muse 或 iPad 真机验收，也未做硬件输出听感验收；软件解码、音源调度、PCM 数值和浏览器视口验证不代表目标设备的实际听感、触摸或 BLE 验收。原 iPad 使用限制仍见 [连接说明](IPAD.md)。
