# v1.1.0 本地验收记录

验收日期：2026-10-08（北京时间）。项目以原提交 `64a588ad716925e3ed3a8aeb5d357a4f7395912c` 为基础。全部采集使用明确的 synthetic 模拟源，没有连接真实 Muse 或真实被试；没有使用 Xcode 模拟器。

## 已执行并通过

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 175 passed，1 skipped；跳过项依赖仓库未包含的外部 EDF 参考数据集 |
| Node 单元／契约回归 | 54 passed，包括 13 项 iPad 客户端测试 |
| Mac 启动与停止 | LAN 启动、重复启动复用 PID、模式冲突拒绝、停止释放端口、本地模式启动与停止通过；独立端口测试没有影响已有服务 |
| Chrome 浏览器流程 | 独立 operator 与 participant 浏览器上下文，电脑本机地址与真实局域网 IP 分开访问，全流程通过，零 pageerror |
| WebKit 浏览器流程 | Playwright 1.51.1 的 WebKit 18.4（build 2140）在 macOS 上运行，同样全流程通过，零 pageerror |
| 9 组屏幕尺寸 | 810×1080、1080×810；820×1180、1180×820；834×1194、1194×834；1024×1366、1366×1024；1080×720（工具栏占用高度场景） |
| 布局检查 | 佩戴确认按钮完整可见、触摸高度至少 44px、没有横向溢出、触控工具栏可见；检查了横屏和竖屏截图 |
| 连接隔离 | 未配对访问、Origin／Host／来源伪造、其他配对设备冒用 ID、工作人员 API 越权、跨会话或过期预览、旧佩戴引导接管等回归通过 |

两种浏览器均实际完成：二维码加载 → LAN HTTP 配对并移除地址栏凭据 → 用户点击启用 Web Audio／解码随仓库提供的 WAV → 主程序发送头环与耳机引导 → 被试确认 → 跨设备缩略图 → 完整语音开始提示与回执 → 闭眼、睁眼前测 → 训练 → 暂停 → 被试网络离线／恢复 → 人工继续 → 完成 → 历史记录 → EDF ZIP 导出。

测试仅缩短明确模拟模式的前测与单轮时长至 5 秒、15 秒；生产默认和实机校准要求没有改动。Node 回归另覆盖声音中断后的用户手势恢复、无安全上下文 UUID、配对失败重试、全屏降级、Safari 返回缓存、同标签页身份恢复和不同设备时钟。

初次使用随环境提供的 Playwright 1.62.1 时，其 macOS 14 冻结版 WebKit 与运行器协议不匹配，无法创建页面。最终 WebKit 验收使用单独安装的匹配版本 1.51.1 / build 2140，不改变项目运行依赖。

## 验证边界

- WebKit 引擎测试和触摸／视口配置不是 iPad 真机 Safari，也不是 Xcode 模拟器。
- 没有 Windows 电脑和 PowerShell 运行环境；Windows 启动脚本已静态审查，尚未运行验证。
- 未验证真实 iPad 的系统安全区、锁屏、后台调度、蓝牙耳机延迟和真实 Muse BLE 采集。
- 标准实现为同一局域网 HTTP。USB 热点、有线转接器和 Mac USB 共享仅提供条件说明，未声称实测连通。
- 新版本源代码、版本标记与 GitHub 上传是不同步骤；只有实际推送成功才算完成远端发布。

## 复现浏览器测试

浏览器依赖只用于开发测试；使用者运行主程序不需要 Node。请选择独立的测试端口和数据目录，不要对正在使用的真实训练服务运行测试。

先运行后端（Mac 示例）：

```bash
.venv/bin/python server.py --lan --port 8778 --data-dir runtime/browser-test-sessions
```

另一个终端在项目目录执行：

```bash
npm install --prefix .browser-tools --no-save playwright@1.51.1
node .browser-tools/node_modules/playwright/cli.js install webkit
NODE_PATH=.browser-tools/node_modules CHENGSI_BROWSER=webkit node tests/ipad-browser.js
```

Chrome 测试使用已安装的 Google Chrome：

```bash
NODE_PATH=.browser-tools/node_modules CHENGSI_BROWSER=chromium node tests/ipad-browser.js
```

Windows PowerShell 对应设置 `$env:NODE_PATH='.browser-tools/node_modules'`、`$env:CHENGSI_BROWSER='webkit'`，再执行 `node tests/ipad-browser.js`。Windows 浏览器测试命令尚未在本次环境执行。

默认测试地址为 `http://127.0.0.1:8778`，可用 `CHENGSI_URL` 更改；截图与模拟导出默认写入已忽略的 `runtime/browser-checks/`，可用 `CHENGSI_TEST_OUTPUT` 更改。旧电脑若需要匹配版本的浏览器，应依照 Playwright 官方系统要求选择运行器；本版本不要求用户安装测试工具。
