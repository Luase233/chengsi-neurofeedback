# iPad 被试端与 Mac / Windows 连接

主程序在电脑上运行，Muse 2 仍由电脑采集；iPad 只通过 Safari 显示被试界面、播放声音、确认佩戴并接收训练状态。保留原有训练画面、配色与流程，适配 iPad 8 和全面屏 iPad 的横竖屏及安全区域。无需 Xcode、iPad 模拟器或安装原生 iPad App。

**最低支持 iPadOS 16.4。** iPad 8 和全面屏 iPad 均请使用 iPadOS 16.4 或更新版本的 Safari；机型支持不等于支持该机型出厂时的旧系统。电脑端需要 64 位 Python 3.12。

## 推荐连接：同一局域网

1. 电脑和 iPad 连接同一个可信 Wi-Fi，或让电脑通过网线连接同一路由器。避开访客网络与开启了客户端隔离的无线网络。
2. 在电脑双击 **启动系统.command**（Mac）或 **启动系统.cmd**（Windows）。新版本默认开启 iPad 局域网模式，浏览器打开本机工作人员工作台。
3. 在工作台的 iPad 连接区域选择电脑当前网络的地址，在 iPad 扫描二维码，或把完整被试链接输入 Safari。多张网卡或 VPN 会产生多个候选地址，使用与 iPad 同网段的那一个。
4. 在 iPad 上点击 **启用声音并进入展示**。声音必须由 iPad 本次点击启用；工作人员远程点击开始不能替代这一步。保持 Safari 在前台，仅保留一个播放中的被试页面。
5. 工作台确认被试端已连接且声音就绪，再按原来的佩戴确认、前测和训练流程操作。
6. 结束训练后双击 **关闭系统.command** 或 **关闭系统.cmd**。关闭浏览器不会停止主程序。

默认端口为 `8768`。iPad 上的 `127.0.0.1` / `localhost` 指向 iPad 自己，不能用它连接电脑；应使用工作台给出的完整地址。主程序与前端资源都在电脑本地，安装完成后，局域网训练无需互联网。连接地址可能带有配对凭据，不要把二维码或完整链接公开发布。

首次联网如果系统询问是否允许程序接收传入连接，请允许本机 Python / 本程序用于所选可信网络。Windows 建议把当前可信 Wi-Fi 设为“专用网络”，仅放行该网络范围；不要为排错关闭整个防火墙或开启路由器公网端口映射。工作人员工作台始终使用电脑本机地址。

## Mac：安装与启动

安装 [Python 3.12 的 macOS 版本](https://www.python.org/downloads/macos/)，完整解压仓库并放到可写入的位置。首次双击 **安装环境.command**，完成后双击 **启动系统.command**。初次安装需要互联网。

如系统未允许双击运行，先在终端进入项目目录，执行：

```bash
chmod +x *.sh *.command
./setup.sh
./start.sh
```

可指定 Python、端口、数据目录，或关闭浏览器自动打开：

```bash
./setup.sh --python /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
./start.sh --port 8770 --data-dir ./runtime --no-browser
./stop.sh --port 8770
```

纯电脑本地双窗口模式：

```bash
./start.sh --local-only
```

已运行时改变 LAN / 本地模式会提示先停止。只重启本项目管理的服务：

```bash
./start.sh --restart --local-only
./start.sh --restart
```

如果没有路由器，但 Mac 通过以太网上网，可在 **系统设置 → 通用 → 共享 → 互联网共享** 中将以太网连接共享到 Wi-Fi，再让 iPad 加入该 Wi-Fi。它是否可用取决于 Mac 的网络接口；不要假设同一 Wi-Fi 接口能同时接入和创建热点。[Apple 互联网共享说明](https://support.apple.com/guide/mac-help/share-internet-connection-mac-network-users-mchlp1540/mac)

Mac 的依赖安装由 `requirements.txt` 和上游平台依赖共同解析；Windows 的 `winrt-*` 包不会安装到 Mac，`bleak` 会选择其 macOS 所需依赖。Mac 上的真实 Muse / BLE 采集仍需要目标硬件和系统蓝牙权限验证。

## Windows：安装与启动

安装 [Python 3.12 x64](https://www.python.org/downloads/windows/)，保留 Python Launcher，完整解压仓库。依次双击 **安装环境.cmd**、**启动系统.cmd**。原有 Windows 入口继续可用。

在项目目录下的 PowerShell 中也可以运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch.ps1
```

选项示例：

```powershell
.\launch.ps1 -Port 8770 -DataDir .\runtime -NoBrowser
.\stop.ps1 -Port 8770
.\launch.ps1 -LocalOnly
.\launch.ps1 -Restart
```

PowerShell 的 `-LocalOnly` 对应 Mac 脚本的 `--local-only`。`start.ps1` 负责后台启动，`launch.ps1` 还会打开工作台；两个入口默认均为 LAN 模式。

Windows 可在 **设置 → 网络和 Internet → 移动热点** 共享网络，再让 iPad 连接此热点，工作台使用热点网卡的连接地址。Microsoft 支持从 Wi-Fi、以太网或蜂窝连接共享；实际可用性依赖网卡及系统设置。[Microsoft 移动热点说明](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/use-your-windows-device-as-a-mobile-hotspot)

## 数据线与有线网络的边界

**本版本采用局域网作为标准连接方式，普通充电／同步数据线不能视为 Safari 已经连通主程序。** 接口形状和 iPad 是否具备蜂窝功能是不同条件；Wi-Fi-only iPad 8 也完全可以使用上述 LAN 方案。

| 方案 | Mac | Windows | 条件与验证状态 |
| --- | --- | --- | --- |
| 普通 USB 线直接插电脑 | 不作为通用连接方式 | 不作为通用连接方式 | 本版本没有原生 iPad App 或 USB 转发组件，不承诺插线即用 |
| iPad 的 USB 个人热点 | 系统支持 | 需 Apple Devices 或 iTunes | 要求 iPad Wi-Fi + Cellular、可用蜂窝个人热点与“信任此电脑”；随后仍需检查程序地址是否能访问，未实机验证 |
| Mac 通过 USB 共享互联网 | 有条件支持 | 无对应的本项目方案 | Apple 的有线内容缓存要求 Mac 经以太网上网、笔记本接电；未验证本项目服务的实机访问 |
| iPad 以太网转接器接同一路由器 | 可用的网络方案 | 可用的网络方案 | iPad 8 需兼容 Lightning 相机转接器与 USB 网卡，USB-C iPad 需兼容 USB 网卡；本项目仍用相同 LAN 链接，未实机验证 |

Apple 的 USB 个人热点用于将 **iPad 的蜂窝连接提供给电脑**，不是保证电脑向任意 iPad 反向共享网络。Windows 安装 Apple Devices 或 iTunes 本身也不会让 Wi-Fi-only iPad 自动出现个人热点。[Apple 个人热点说明](https://support.apple.com/en-ie/111785)

Mac 的 **系统设置 → 通用 → 共享 → 内容缓存 → 共享互联网连接** 是 Apple 官方有线共享路径。Apple 明确列出 macOS 10.13+、以太网输入和笔记本接电，并说明它不能与普通互联网共享同时开启。本版本不自动修改这些系统设置。[Apple 有线内容缓存说明](https://support.apple.com/en-ca/guide/deployment/-dep38ff24bed/web)

若后续确需稳定有线网络，优先让电脑和 iPad 的网卡接同一路由器／交换机。Apple 支持 Lightning USB 相机转接器连接兼容以太网网卡；有供电需求时使用带电源输入的 Lightning USB 3 相机转接器。[Lightning 转接器说明](https://support.apple.com/en-ie/111811) USB-C iPad 官方支持 USB 转以太网设备。[USB-C 设备说明](https://support.apple.com/en-us/108894)

## iPad 显示、声音与锁屏

- iPad 8 的 4:3 显示和全面屏 iPad 的不同长宽比使用同一被试页面，按实际可用空间调整。横屏更适合保留现有宽幅训练画面；竖屏也应能看到进入和确认按钮。
- 全屏按钮属于增强功能，不是训练前提；Safari 16.4 起在 iPadOS 支持无前缀 Fullscreen API。由被试主动点击进入，并允许用户退出；不承诺锁定系统方向。[WebKit Safari 16.4](https://webkit.org/blog/13966/webkit-features-in-safari-16-4/)
- 页面通过 `viewport-fit=cover` 和 `safe-area-inset-*` 适配全面屏底部指示条，并为动态 Safari 工具栏保留空间。[WebKit 安全区域说明](https://webkit.org/blog/7929/designing-websites-for-iphone-x/)、[Safari 15.4 动态视口单位](https://developer.apple.com/documentation/safari-release-notes/safari-15_4-release-notes)
- 若训练期间屏幕会自动熄灭，在 iPad **设置 → 显示与亮度 → 自动锁定** 选择适合训练时长的设置，训练后恢复个人设置。普通 LAN HTTP 不能依赖浏览器 Wake Lock；该接口要求安全上下文，且系统仍可能释放唤醒锁。[W3C Screen Wake Lock](https://www.w3.org/TR/screen-wake-lock/)
- 锁屏、切换应用或关闭标签页可能暂停声音与网络。回来后确认连接状态，必要时重新点击启用声音，再由工作人员继续。蓝牙耳机和实际音频延迟需要真实 iPad 检查。

## 无实机时的本地流程验证

无实机时可在本机运行电脑主程序和独立的浏览器被试页面，检查 LAN 模式接口、配对和主被试流程；浏览器按 iPad 尺寸运行，不使用 Xcode 模拟器。下面是验收方法，具体执行结果以版本发布说明为准：**本地浏览器流程通过不等于 iPad 真机、USB 网络或 Windows 实机通过**。

推荐验收步骤：

1. LAN 模式启动电脑服务，工作台显示候选连接地址与二维码；本机访问相同被试链接进行配对。
2. 分别检查 iPad 8（810×1080 / 1080×810 CSS 视口）及全面屏代表尺寸（820×1180、834×1194、1024×1366 及对应横屏）；检查进入、佩戴确认与全屏按钮不被遮挡。iPad 8 的原生分辨率为 1620×2160，本地视口按 DPR 2 配置。[Apple iPad 8 规格](https://support.apple.com/en-gb/118451)
3. 工作台选择 **模拟全流程**，填写测试编号；被试端启用声音，完成头环和耳机确认，再检查前测、训练、暂停、恢复与结束。
4. 断开被试页网络或关闭页面，确认主程序显示断连；重新进入后重新确认声音，按流程继续。
5. 确认数据只写入本机测试数据目录，停止后端后再检查导出；不要把模拟数据称为真实脑电结果。
6. 本地模式启动时，确认只提供本机被试地址，iPad 局域网连接不可用。

开发阶段的 Windows PowerShell 启动代码只能在可用环境范围内检查；没有 Windows / `pwsh` 时只能进行静态审查。Mac 脚本需要分别检查 shell 语法和本机启停流程，不能由其中一项推断另一项通过。仍需在最终 Mac / Windows 电脑、真实 iPad、真实 Muse 与目标网络完成一次验收后投入真实被试使用。

## 排错

| 现象 | 处理 |
| --- | --- |
| iPad 打不开地址 | 核对同一网络、完整地址、服务 LAN 模式、防火墙；避开访客隔离、代理和错误 VPN 网卡 |
| 多个候选地址 | 选择电脑连接 iPad 所在 Wi-Fi／有线网的网卡地址；换网络后刷新工作台获取新地址 |
| 已运行但模式不同 | 先关闭原服务再启动；Mac 用 `--restart`，Windows 用 `-Restart`，不会自动杀掉占用端口的其他程序 |
| 端口被占用 | 使用另一端口；停止时传入同一端口。一个项目目录一次管理一个后台实例 |
| 只有画面没有声音 | 在 iPad 上点击启用声音，检查音量、耳机输出与页面是否可见 |
| 连接二维码失效 | 主程序重启后重新打开连接面板，使用本次生成的完整链接 |
| 关闭页面后服务仍在 | 使用关闭系统入口；PID 文件记录本项目的后台进程，脚本校验程序路径和端口后停止 |

日志位于项目目录的 `backend-server.log` 与 `backend-server-error.log`；训练数据默认保存在 `runtime/`。分享日志前删除被试信息、设备地址与完整配对链接。
