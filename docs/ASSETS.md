# 可选音乐与资源说明

## 已随仓库提供

| 资源 | 来源 | 说明 |
| --- | --- | --- |
| 澄流 v01–v04 三层循环 WAV | 本项目原创 LMMS 编配 | 已完成渲染，运行无需 LMMS 或 SoundFont；编曲工程和创作报告不在本仓库范围。 |
| 晴窗漫游 v01 三层循环 WAV | 本项目原创 LMMS 编配 | 未使用参考歌曲音频采样；运行使用预渲染素材。 |
| 前测、等待、训练语音 WAV | 本项目通过阿里百炼 TTS 生成 | 运行完全本地播放，无需 API Key。 |
| 短提示音 | 本项目 `calibration-cues.js` 合成 | 可本地重建；已提供用于语音组合的 WAV。 |
| 湖景照片 | 项目使用者提供的两张照片 | 保留原画面；作者与第三方权利不因仓库公开而转让。 |
| 佩戴插画与界面动画 | 本项目 Canvas / CSS 实现 | Muse / AirPods 的产品名称归相应权利人，程序不是其官方应用。 |

音频 metadata 中的 `sourceProject` 是历史创作来源标识，不是运行依赖；不会要求下载本仓库之外的研究项目文件夹。原创音源采用 LMMS 内置免费合成器及 GeneralUser GS 的渲染结果，仓库不分发插件或 SF2 文件。

## Easy Going 本地导入

购买曲目：[Easy Going — Blips](https://blips.fm/easy-going)。其许可为购买者使用音乐的许可，具体分发范围以 [Blips 官方音乐许可](https://blips.fm/license) 为准。本公开复刻版不分发可提取的购买音轨，不声称复刻者继承原购买者许可。

拥有合法使用权及原始 `wav_Files.zip` 后，在项目文件夹运行：

```powershell
.\.venv\Scripts\python.exe prepare_easy_going.py "C:\你的素材目录\wav_Files.zip"
.\.venv\Scripts\python.exe register_local_audio.py easy-going
```

压缩包需要包含 `Track_1_Layer_1.wav`、`Track_1_Layer_2.wav`、`Track_1_Layer_3.wav`。导入会保留相位、时长和声部原始平衡，仅做已有的 3 ms 循环端点修补；不是自动分离普通歌曲。导入后刷新工作台和被试页面，选择 `Easy Going · 分层反馈`。

`assets/audio/easy-going/` 已加入 `.gitignore`。注册动作会更新曲目目录文件；如果之后发布自己的公开分支，不要把本地购买媒体加入版本控制，也不要把只有自己拥有的素材登记为公开版的默认曲目。

## 旧四首曲目

原项目的深度学习、效率提升、RE LIFE、Beta Focus 的分离音轨没有随公开版分发。程序保留相应反馈实现。若已有合法准备好的音轨与 metadata，可把原 `assets/audio/` 相应文件复制到本地，然后分别运行：

```powershell
.\.venv\Scripts\python.exe register_local_audio.py deep-learning
.\.venv\Scripts\python.exe register_local_audio.py efficiency
.\.venv\Scripts\python.exe register_local_audio.py re-life
.\.venv\Scripts\python.exe register_local_audio.py beta-focus
```

深度学习使用 `assets/audio/metadata.json` 与根目录三条 OGG；其余使用各自同名子目录的 metadata 和三条 OGG。若没有这些素材，用随仓库提供的原创曲目即可体验完整训练功能，但不能据此声称重现原论文的音频刺激条件。

## 重新生成语音（可选）

已有语音可以直接播放。只有修改提示文案才需要此步骤：安装 Node.js，在当前终端设置自己的 `DASHSCOPE_API_KEY`，随后运行：

```powershell
.\.venv\Scripts\python.exe prepare_aliyun_instructions.py --only open-complete --refresh
```

脚本默认使用其代码中指定的阿里百炼端点、模型和音色；接口可能随供应方更新，生成服务也可能产生费用。凭据只从进程环境或自行指定的私有配置读取，缓存位于忽略的 `runtime/tts-build/`。不要把密钥写入源码、README、音频 metadata 或公开配置。

## 仓库边界

只公开训练程序、可运行媒体和复刻说明。研究报告、原始数据集、伦理与证明材料、真实脑电会话、个人基线、试听截图、私有配置、购买素材源文件、LMMS 工程与创作设计报告均未包含。仓库没有擅自给全部代码与媒体添加统一 MIT 或其他许可；各资源的权利归其相应权利人。
