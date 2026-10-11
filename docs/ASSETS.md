# 可选音乐与资源说明

## 已随仓库提供

| 资源 | 来源 | 说明 |
| --- | --- | --- |
| 澄流 v01–v04 三层循环 WAV | 本项目原创 LMMS 编配 | 已完成渲染，运行无需 LMMS 或 SoundFont；编曲工程和创作报告不在本仓库范围。 |
| 晴窗漫游 v01 三层循环 WAV | 本项目原创 LMMS 编配 | 未使用参考歌曲音频采样；运行使用预渲染素材。 |
| Easy Going 三层循环 WAV | 购买的 Blips 音乐包 | 随本训练项目提供运行所需的三层音轨与 metadata，原始购买压缩包和未处理源文件不分发。 |
| 前测、等待、训练语音 WAV | 本项目通过阿里百炼 TTS 生成 | 运行完全本地播放，无需 API Key。 |
| 短提示音 | 本项目 `calibration-cues.js` 合成 | 可本地重建；已提供用于语音组合的 WAV。 |
| 湖景重绘 PNG | 使用者提供的两张照片，经内置 imagegen 重绘 | 保留原构图，原生 1606 × 979；没有插值放大。提示词和工具记录见 [重绘记录](SCENES.md)。 |
| 佩戴插画与界面动画 | 本项目 Canvas / CSS 实现 | Muse / AirPods 的产品名称归相应权利人，程序不是其官方应用。 |

音频 metadata 中的 `sourceProject` 是历史创作来源标识，不是运行依赖；不会要求下载本仓库之外的研究项目文件夹。原创音源采用 LMMS 内置免费合成器及 GeneralUser GS 的渲染结果，仓库不分发插件或 SF2 文件。

## Easy Going 运行音轨与重新导入

购买曲目：[Easy Going — Blips](https://blips.fm/easy-going)。v1.1.1 已在本训练项目中接入三层运行音轨；工作台与声场试听中选择 `Easy Going · 分层反馈` 即可使用。音轨保留购买来源与校验值，版权归原权利人，不宣称本项目原创。

[Blips 官方音乐许可](https://blips.fm/license) 允许购买者在相关多媒体项目中使用音乐，并限制脱离相关项目的重新分发或再许可。本仓库将运行音轨作为本训练项目的组成部分提供，不授予这些音乐独立再使用或再许可的权利；若用于自己的其他项目，应自行获得相应许可。原始购买压缩包与未处理源音轨仍不加入仓库。

若需要从自己合法取得的原始 `wav_Files.zip` 重新生成，在项目文件夹运行：

```powershell
.\.venv\Scripts\python.exe prepare_easy_going.py "C:\你的素材目录\wav_Files.zip"
.\.venv\Scripts\python.exe register_local_audio.py easy-going
```

压缩包需要包含 `Track_1_Layer_1.wav`、`Track_1_Layer_2.wav`、`Track_1_Layer_3.wav`。导入会保留相位、时长和声部原始平衡，仅做已有的 3 ms 循环端点修补；不是自动分离普通歌曲。导入后刷新工作台和被试页面，选择 `Easy Going · 分层反馈`。

`assets/audio/easy-going/` 仅放行三条 `layer-1/2/3.wav` 与 `metadata.json`；购买 ZIP、`source/` 和其他文件仍被 Git 忽略。注册动作会同步更新 JS 与 JSON 曲目目录。系统默认曲目仍为原创澄流 v04。

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
